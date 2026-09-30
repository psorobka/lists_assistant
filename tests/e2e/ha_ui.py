"""Explicit Chromium E2E: real HA frontend/config flow and HTTP fake Listonic.

Run: python -m pytest tests/e2e/ha_ui.py -q --timeout=180
This module is deliberately outside ordinary test_*.py discovery.
"""

import asyncio
import json
from copy import deepcopy
from functools import partial
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from aiohttp import web
from homeassistant.config_entries import ConfigEntryState
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.service import async_get_all_descriptions
from homeassistant.setup import async_setup_component
from playwright.async_api import async_playwright, expect
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_recorder_block_till_done,
)

from custom_components.lists_assistant.api import ListonicClient
from custom_components.lists_assistant.const import DOMAIN


class FakeListonic:
    """Fake backend receives real HTTP; page routing cannot intercept HA requests."""

    def __init__(self):
        self.lists = {
            "1": {"Id": "1", "Name": "E2E groceries", "Active": 1, "Items": []}
        }
        self.calls = []
        self.offline = False
        self.next_id = 10

    async def handle(self, request):
        if self.offline:
            return web.Response(status=503)
        path = request.path
        if path == "/api/loginextended":
            data = await request.post()
            if data.get("password") == "wrong":
                return web.Response(status=401)
            return web.json_response(
                {"access_token": "e2e-access", "refresh_token": "e2e-refresh"}
            )
        if path == "/api/account/userinfo":
            return web.json_response({"Username": "e2e@example.test"})
        data = await request.json() if request.can_read_body else {}
        self.calls.append((request.method, path, deepcopy(data)))
        if path == "/api/lists":
            return web.json_response(list(deepcopy(self.lists).values()))
        parts = path.split("/")
        list_id = parts[3]
        target = self.lists[list_id]
        if len(parts) == 4:
            target.update(data)
        elif len(parts) == 5:
            self.next_id += 1
            item = {
                "Id": str(self.next_id),
                "Checked": 0,
                **{key.capitalize(): value for key, value in data.items()},
            }
            target["Items"].append(item)
            return web.json_response(item)
        else:
            uid = parts[5]
            if request.method == "DELETE":
                target["Items"] = [
                    item for item in target["Items"] if item["Id"] != uid
                ]
            else:
                item = next(item for item in target["Items"] if item["Id"] == uid)
                item.update({key.capitalize(): value for key, value in data.items()})
        return web.Response(status=204)


async def wait_for(check):
    """Await actual backend state; no fixed UI delays."""
    async with asyncio.timeout(30):
        while not check():  # noqa: ASYNC110 -- poll state across browser/HA/HTTP
            await asyncio.sleep(0.1)


@pytest.mark.parametrize(
    "language,mobile,dark", [("en", False, False), ("pl", True, True)]
)
@pytest.mark.parametrize("persistent_database", [True])
async def test_config_flow_and_shopping_ui(
    recorder_mock,
    hass,
    tmp_path,
    enable_custom_integrations,
    hass_client,
    hass_access_token,
    aiohttp_client,
    hass_storage,
    language,
    mobile,
    dark,
):
    fake = FakeListonic()
    app = web.Application()
    app.router.add_route("*", "/{path:.*}", fake.handle)
    fake_client = await aiohttp_client(app)
    base_url = str(fake_client.make_url("/")).rstrip("/")
    hass.config.config_dir = str(tmp_path)
    hass_storage["onboarding"] = {
        "version": 4,
        "data": {"done": ["user", "core_config", "integration", "analytics"]},
    }
    native = MockConfigEntry(domain="shopping_list", unique_id="shopping_list", data={})
    native.add_to_hass(hass)
    assert await hass.config_entries.async_setup(native.entry_id)
    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "conversation", {})
    assert await async_setup_component(hass, "frontend", {"frontend": {}})
    await hass.async_block_till_done()
    # Fail immediately on missing HA dependencies instead of a browser timeout.
    await async_get_all_descriptions(hass)
    client = await hass_client()
    with patch.object(hass.http, "start", new=AsyncMock()):
        await hass.async_start()
    await async_recorder_block_till_done(hass)
    url = str(client.make_url("/")).rstrip("/")
    artifact = (
        Path(".test-state/e2e") / f"{language}-{'mobile' if mobile else 'desktop'}"
    )
    artifact.mkdir(parents=True, exist_ok=True)

    with (
        patch(
            "custom_components.lists_assistant.api.ListonicClient",
            partial(ListonicClient, base_url=base_url),
        ),
        patch(
            "custom_components.lists_assistant.config_flow.ListonicClient",
            partial(ListonicClient, base_url=base_url),
        ),
    ):
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch()
            context = await browser.new_context(
                locale="pl-PL" if language == "pl" else "en-US",
                viewport={"width": 390, "height": 844}
                if mobile
                else {"width": 1440, "height": 1000},
                is_mobile=mobile,
                color_scheme="dark" if dark else "light",
            )
            await context.tracing.start(screenshots=True, snapshots=True, sources=True)
            token = {
                "hassUrl": url,
                "access_token": hass_access_token,
                "token_type": "Bearer",
                "expires_in": 3600,
                "expires": 9999999999999,
                "refresh_token": "e2e-only",
            }
            await context.add_init_script(
                f"localStorage.setItem('hassTokens', {json.dumps(json.dumps(token))});"
                "localStorage.setItem('selectedLanguage', "
                f"{json.dumps(json.dumps(language))});"
            )
            page = await context.new_page()
            page.set_default_timeout(15000)

            def socket_opened(socket):
                def frame_received(payload):
                    try:
                        values = json.loads(payload)
                        values = values if isinstance(values, list) else [values]
                        for value in values:
                            if value.get("type") == "result" and value.get("success"):
                                errors.append(f"RESULT {value.get('id')}")
                            if (
                                value.get("type")
                                in ("auth_required", "auth_ok", "auth_invalid")
                                or value.get("success") is False
                            ):
                                errors.append(json.dumps(value))
                    except (ValueError, TypeError):
                        pass

                socket.on("framereceived", frame_received)

                def frame_sent(payload):
                    value = json.loads(payload)
                    if "id" in value:
                        errors.append(f"COMMAND {value['id']}: {value.get('type')}")

                socket.on("framesent", frame_sent)

            page.on("websocket", socket_opened)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on(
                "console",
                lambda message: (
                    errors.append(message.text) if message.type == "error" else None
                ),
            )
            try:
                await page.goto(url + "/config/integrations/dashboard")
                add_label = (
                    "Dodaj integrację" if language == "pl" else "Add integration"
                )
                await page.get_by_role("button", name=add_label).click()
                await (
                    page.locator("ha-dialog")
                    .get_by_role("textbox")
                    .fill("Lists Assistant")
                )
                await page.get_by_text("Lists Assistant", exact=True).click()
                await page.locator("ha-form-string input").first.fill(
                    "e2e@example.test"
                )
                await page.locator('input[type="password"]').fill("wrong")
                submit = page.locator("ha-dialog").get_by_role("button").last
                await submit.click()
                await expect(
                    page.get_by_text(
                        "The email or password was rejected."
                        if language == "en"
                        else "Listonic odrzucił e-mail lub hasło.",
                        exact=False,
                    )
                ).to_be_visible()
                await page.locator('input[type="password"]').fill("e2e-fixture-only")
                await submit.click()
                await expect(page.locator("ha-selector-select").first).to_be_visible()
                await expect(page.get_by_role("combobox").last).to_contain_text(
                    "Nie łącz" if language == "pl" else "No bridge"
                )
                await page.screenshot(
                    path=str(artifact / "selection.png"), full_page=True
                )
                # Select remote list and bridge through the rendered selectors.
                await page.locator("ha-selector-select").first.click()
                await expect(
                    page.get_by_role(
                        "button",
                        name="Remove E2E groceries",
                    )
                ).to_be_visible()
                await page.keyboard.press("Escape")
                await (
                    page.locator("ha-selector-select")
                    .last.get_by_role("combobox")
                    .click()
                )
                await page.get_by_role(
                    "option", name="E2E groceries", exact=True
                ).click()
                await submit.click()
                await page.locator("ha-checkbox").click()
                await page.screenshot(
                    path=str(artifact / "confirmation.png"), full_page=True
                )
                await submit.click()
                await wait_for(lambda: bool(hass.config_entries.async_entries(DOMAIN)))
                entry = hass.config_entries.async_entries(DOMAIN)[0]
                await hass.async_block_till_done()
                await wait_for(lambda: entry.state is ConfigEntryState.LOADED)
                bridge = hass.data[DOMAIN][entry.entry_id].bridge
                await bridge.async_wait_idle()
                native_id = er.async_get(hass).async_get_entity_id(
                    "todo", "shopping_list", native.entry_id
                )
                await page.goto(url + "/todo?entity_id=" + native_id)
                await page.get_by_role("textbox").fill("UI milk")
                await page.get_by_role("textbox").press("Enter")
                await wait_for(lambda: len(fake.lists["1"]["Items"]) == 1)
                await page.locator("ha-checkbox").first.click()
                await wait_for(lambda: bool(fake.lists["1"]["Items"][0]["Checked"]))
                await page.screenshot(
                    path=str(artifact / "shopping.png"), full_page=True
                )
                assert any(
                    method == "POST" and path.endswith("/items")
                    for method, path, _ in fake.calls
                )
                assert any(
                    method == "PATCH" and data.get("checked") == 1
                    for method, _, data in fake.calls
                )
                # A cloud edit reaches the native panel through the real bridge.
                fake.lists["1"]["Items"][0].update(
                    {"Name": "Remote milk", "Checked": 0}
                )
                await bridge.coordinator.async_refresh()
                await bridge.async_wait_idle()
                await expect(
                    page.get_by_text("Remote milk", exact=True)
                ).to_be_visible()
                await expect(page.locator("ha-checkbox").first).to_have_js_property(
                    "checked", False
                )

                response = await hass.services.async_call(
                    "conversation",
                    "process",
                    {"text": "Dodaj mleko do listy zakupów", "language": "pl"},
                    blocking=True,
                    return_response=True,
                )
                assert response["response"]["response_type"] != "error"
                await wait_for(lambda: len(fake.lists["1"]["Items"]) == 2)
                await expect(page.get_by_text("mleko", exact=True)).to_be_visible()

                # Fail reads before adding locally: this is a queued, unsent write.
                fake.offline = True
                await bridge.coordinator.async_refresh()
                await page.get_by_role("textbox").fill("Offline bread")
                await page.get_by_role("textbox").press("Enter")
                await wait_for(lambda: len(bridge.state["pending"]) == 1)
                assert bridge.status == "pending"
                assert len(fake.lists["1"]["Items"]) == 2
                await expect(
                    page.get_by_text("Offline bread", exact=True)
                ).to_be_visible()
                await page.screenshot(
                    path=str(artifact / "offline.png"), full_page=True
                )
                assert await hass.config_entries.async_unload(entry.entry_id)
                fake.offline = False
                assert await hass.config_entries.async_setup(entry.entry_id)
                bridge = hass.data[DOMAIN][entry.entry_id].bridge
                await bridge.async_wait_idle()
                await wait_for(lambda: len(fake.lists["1"]["Items"]) == 3)
                assert bridge.status == "synchronized"
                assert not bridge.state["pending"]
                assert (
                    sum(
                        method == "POST" and data.get("name") == "Offline bread"
                        for method, _, data in fake.calls
                    )
                    == 1
                )
                await page.screenshot(
                    path=str(artifact / "recovered.png"), full_page=True
                )
            except Exception:
                await page.screenshot(
                    path=str(artifact / "failure.png"), full_page=True
                )
                (artifact / "page.txt").write_text(
                    await page.locator("body").aria_snapshot(), encoding="utf-8"
                )
                (artifact / "errors.txt").write_text(
                    "\n".join(errors), encoding="utf-8"
                )
                result = await page.evaluate(
                    "async () => {try {await window.hassConnection; return 'connected'}"
                    " catch(e) {return {name:e?.name,message:e?.message,"
                    "code:e?.code,detail:String(e)}}}"
                )
                (artifact / "connection.json").write_text(
                    json.dumps(result), encoding="utf-8"
                )
                raise
            finally:
                await context.tracing.stop(path=str(artifact / "trace.zip"))
                await browser.close()
