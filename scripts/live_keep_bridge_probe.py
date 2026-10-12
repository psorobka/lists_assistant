"""Opt-in voice-to-Keep-to-HA-to-Listonic live integration check.

Run from the repository root in WSL with Python 3.13 and
`uv run --with gkeepapi==0.17.1 --with-requirements requirements_test.txt --`
followed by this module. It creates and later deletes disposable lists in both
Google Keep and Listonic. Never run it against a personal Keep account.
"""

import asyncio
import getpass
import logging
from datetime import UTC, datetime
from functools import partial
from unittest.mock import patch
from uuid import uuid4

import aiohttp
import pytest

from custom_components.lists_assistant.api import ListonicClient, identifier
from custom_components.lists_assistant.const import (
    CONF_BRIDGE,
    CONF_BRIDGE_CONFIRMED,
    CONF_KEEP_ENABLED,
    CONF_KEEP_LIST_ID,
    CONF_KEEP_TOKEN,
    CONF_KEEP_USERNAME,
    CONF_LISTS,
    DOMAIN,
)


async def test_live_keep_bridge(
    hass,
    tmp_path,
    enable_custom_integrations,
    socket_enabled,
    live_listonic_credentials,
    live_keep_credentials,
    monkeypatch,
):
    """Verify real Keep and Listonic accounts using only disposable lists."""
    import gkeepapi
    from homeassistant.helpers import entity_registry as er
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from pytest_socket import _remove_restrictions

    _remove_restrictions()

    def live_resolver(hass):
        resolver = aiohttp.ThreadedResolver()
        resolver.real_close = resolver.close
        return resolver

    monkeypatch.setattr(
        "homeassistant.helpers.aiohttp_client._async_make_resolver",
        live_resolver,
    )
    hass.config.config_dir = str(tmp_path)
    keep_email, master_token = live_keep_credentials
    tag = uuid4().hex[:8]
    keep_list_title = f"HA Keep probe {tag}"
    voice_item_name = f"probe item {tag}"
    listonic_name = f"HA Keep bridge probe {datetime.now(UTC):%Y%m%d-%H%M%S} {tag}"

    keep = gkeepapi.Keep()
    keep_list = None
    entry = None
    listonic = None
    list_id = None
    async with aiohttp.ClientSession() as session:
        listonic = ListonicClient(session)
        await listonic.login(*live_listonic_credentials)
        keep_state = await hass.async_add_executor_job(
            partial(keep.authenticate, keep_email, master_token)
        )
        del keep_state
        try:
            keep_list = keep.createList(keep_list_title)
            await hass.async_add_executor_job(keep.sync)
            keep_list_id = str(keep_list.id)
            print(f"PASS created disposable Keep list: {keep_list_title}", flush=True)

            created = await listonic.create_list(listonic_name)
            list_id = identifier(created["Id"])
            print("PASS created disposable Listonic list", flush=True)

            native = MockConfigEntry(
                domain="shopping_list", unique_id="shopping_list", data={}
            )
            native.add_to_hass(hass)
            assert await hass.config_entries.async_setup(native.entry_id), (
                "native Shopping List setup"
            )
            await hass.async_block_till_done()
            local_id = er.async_get(hass).async_get_entity_id(
                "todo", "shopping_list", native.entry_id
            )
            assert local_id is not None

            entry = MockConfigEntry(
                domain=DOMAIN,
                title="Live Keep bridge probe",
                unique_id=f"live-keep-probe-{tag}",
                data={
                    "tokens": dict(listonic.tokens),
                    CONF_LISTS: [list_id],
                    CONF_BRIDGE: list_id,
                    CONF_BRIDGE_CONFIRMED: list_id,
                    CONF_KEEP_ENABLED: True,
                    CONF_KEEP_USERNAME: keep_email,
                    CONF_KEEP_TOKEN: master_token,
                    CONF_KEEP_LIST_ID: keep_list_id,
                },
            )
            entry.add_to_hass(hass)
            assert await hass.config_entries.async_setup(entry.entry_id), (
                "Lists Assistant setup"
            )
            await hass.async_block_till_done()

            async def local_items():
                response = await hass.services.async_call(
                    "todo",
                    "get_items",
                    {"entity_id": local_id},
                    blocking=True,
                    return_response=True,
                )
                return response[local_id]["items"]

            async def live_listonic_items():
                return (await listonic.lists())[list_id]["Items"]

            async def live_keep_items():
                await hass.async_add_executor_job(keep.sync)
                return list(keep_list.items)

            async def wait_for_voice_item_in_keep():
                async with asyncio.timeout(180):
                    while True:
                        items = await live_keep_items()
                        if any(
                            item.text.casefold() == voice_item_name.casefold()
                            for item in items
                        ):
                            return
                        await asyncio.sleep(3)

            async def wait_for_shared_item(name, checked=None):
                async with asyncio.timeout(90):
                    while True:
                        local = await local_items()
                        cloud = await live_listonic_items()
                        keep_items = await live_keep_items()
                        local_match = next(
                            (item for item in local if item["summary"] == name), None
                        )
                        cloud_match = next(
                            (item for item in cloud if item["Name"] == name), None
                        )
                        keep_match = next(
                            (item for item in keep_items if item.text == name), None
                        )
                        if local_match and cloud_match and keep_match:
                            if checked is None or (
                                local_match["status"]
                                == ("completed" if checked else "needs_action")
                                and bool(cloud_match["Checked"]) == checked
                                and bool(keep_match.checked) == checked
                            ):
                                return local_match, cloud_match, keep_match
                        await asyncio.sleep(2)

            print(
                "VOICE ACTION: say now: Hey Google, add "
                f"{voice_item_name} to my {keep_list_title} list",
                flush=True,
            )
            await wait_for_voice_item_in_keep()
            print("PASS Google Assistant wrote the item into real Keep", flush=True)

            voice_state = await wait_for_shared_item(voice_item_name, checked=False)
            print("PASS Keep voice item -> HA Shopping List -> Listonic", flush=True)

            await hass.services.async_call(
                "todo",
                "update_item",
                {
                    "entity_id": local_id,
                    "item": voice_state[0]["uid"],
                    "rename": f"checked {voice_item_name}",
                    "status": "completed",
                },
                blocking=True,
            )
            await wait_for_shared_item(f"checked {voice_item_name}", checked=True)
            print("PASS HA edit/check -> real Keep and Listonic", flush=True)

            reverse_item_name = f"ha item {tag}"
            await hass.services.async_call(
                "todo",
                "add_item",
                {"entity_id": local_id, "item": reverse_item_name},
                blocking=True,
            )
            reverse_state = await wait_for_shared_item(reverse_item_name, checked=False)
            print("PASS HA Shopping List -> real Keep and Listonic", flush=True)

            await hass.services.async_call(
                "todo",
                "remove_item",
                {"entity_id": local_id, "item": reverse_state[0]["uid"]},
                blocking=True,
            )
            async with asyncio.timeout(90):
                while True:
                    cloud = await live_listonic_items()
                    keep_items = await live_keep_items()
                    if not any(
                        item["Name"] == reverse_item_name for item in cloud
                    ) and not any(
                        item.text == reverse_item_name for item in keep_items
                    ):
                        break
                    await asyncio.sleep(2)
            print("PASS HA deletion -> real Keep and Listonic", flush=True)
        finally:
            if entry is not None and entry.entry_id in hass.data.get(DOMAIN, {}):
                await hass.config_entries.async_unload(entry.entry_id)
            if list_id is not None and list_id in await listonic.lists():
                await listonic.update_list(list_id, Id=list_id, Active=0)
            if list_id is not None:
                assert list_id not in await listonic.lists()
                print("PASS cleanup: disposable Listonic list deactivated", flush=True)
            if keep_list is not None:
                keep_list.delete()
                await hass.async_add_executor_job(keep.sync)
                assert keep_list.deleted
                print("PASS cleanup: disposable Keep list deleted", flush=True)


class LiveCredentialsPlugin:
    """Provide live credentials to pytest in memory, never argv or files."""

    def __init__(self, listonic_credentials, keep_credentials):
        self.listonic_credentials = listonic_credentials
        self.keep_credentials = keep_credentials

    @pytest.fixture
    def live_listonic_credentials(self):
        return self.listonic_credentials

    @pytest.fixture
    def live_keep_credentials(self):
        return self.keep_credentials


def safe_log_handle(logger, record):
    """Show error location only, without exception messages, args or locals."""
    if record.levelno >= logging.ERROR and record.exc_info:
        error_type, _, trace = record.exc_info
        locations = []
        while trace:
            locations.append(f"{trace.tb_frame.f_code.co_filename}:{trace.tb_lineno}")
            trace = trace.tb_next
        print(
            f"ERROR {logger.name}: {error_type.__name__} at {' -> '.join(locations)}",
            flush=True,
        )


if __name__ == "__main__":
    import gpsoauth

    listonic_email = input("Listonic test account email: ")
    listonic_password = getpass.getpass("Listonic password: ")
    keep_email = input("Google test account email: ")
    android_id = input("Android ID (16-digit hex): ")
    oauth_token = getpass.getpass("Google oauth_token cookie: ")
    try:
        token_response = gpsoauth.exchange_token(keep_email, oauth_token, android_id)
    except Exception as error:
        print(f"FAIL Google token exchange: {type(error).__name__}", flush=True)
        raise SystemExit(1) from None
    master_token = token_response.get("Token")
    if not master_token:
        print(
            "FAIL Google token exchange; returned fields: "
            + ", ".join(sorted(token_response)),
            flush=True,
        )
        raise SystemExit(1)
    oauth_token = None
    print("PASS received a Google Keep master token in memory", flush=True)
    plugin = LiveCredentialsPlugin(
        (listonic_email, listonic_password), (keep_email, master_token)
    )
    logging.disable(logging.NOTSET)
    result = 1
    try:
        with patch("logging.Logger.handle", safe_log_handle):
            result = pytest.main(
                [
                    "-q",
                    "-s",
                    __file__,
                    "--tb=no",
                    "--show-capture=no",
                    "--timeout=240",
                ],
                plugins=[plugin],
            )
    except Exception as error:
        print(f"FAIL live Keep bridge probe: {type(error).__name__}", flush=True)
    finally:
        plugin.listonic_credentials = None
        plugin.keep_credentials = None
        listonic_password = None
        master_token = None
    raise SystemExit(result)
