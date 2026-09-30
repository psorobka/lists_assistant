"""Explicit live bridge test, outside default pytest/CI discovery.

Run `python -m scripts.live_bridge_probe` from the repository root in WSL.
Requires requirements_test.txt. Uses a disposable cloud list and fixture HA.
"""

import asyncio
import getpass
import logging
from datetime import UTC, datetime
from unittest.mock import patch
from uuid import uuid4

import aiohttp
import pytest

from custom_components.lists_assistant.api import ListonicClient, identifier
from custom_components.lists_assistant.const import (
    CONF_BRIDGE,
    CONF_BRIDGE_CONFIRMED,
    CONF_LISTS,
    DOMAIN,
)


async def test_live_bridge(
    hass,
    tmp_path,
    enable_custom_integrations,
    socket_enabled,
    live_credentials,
    monkeypatch,
):
    """Exercise real API and native HA while restricting writes to a new list."""
    from homeassistant.helpers import entity_registry as er
    from homeassistant.setup import async_setup_component
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from pytest_socket import _remove_restrictions

    # HA restricts socket.connect as well as socket creation; socket_enabled
    # restores only creation. This explicit live test runs in its own process.
    _remove_restrictions()

    # The HA test plugin's session-scoped DNS resolver belongs to another loop.
    # Resolve real hosts on this test's loop while retaining HA's HTTP session.
    def live_resolver(hass):
        resolver = aiohttp.ThreadedResolver()
        resolver.real_close = resolver.close
        return resolver

    monkeypatch.setattr(
        "homeassistant.helpers.aiohttp_client._async_make_resolver",
        live_resolver,
    )
    hass.config.config_dir = str(tmp_path)
    async with aiohttp.ClientSession() as session:
        client = ListonicClient(session)
        await client.login(*live_credentials)
        name = f"HA bridge test {datetime.now(UTC):%Y%m%d-%H%M%S} {uuid4().hex[:8]}"
        created = await client.create_list(name)
        list_id = identifier(created["Id"])
        print("PASS created disposable cloud list", flush=True)
        entry = None
        try:
            native = MockConfigEntry(
                domain="shopping_list", unique_id="shopping_list", data={}
            )
            native.add_to_hass(hass)
            assert await hass.config_entries.async_setup(native.entry_id), (
                "native setup"
            )
            assert await async_setup_component(hass, "homeassistant", {}), "core setup"
            assert await async_setup_component(hass, "conversation", {}), (
                "conversation setup"
            )
            await hass.async_block_till_done()
            local_id = er.async_get(hass).async_get_entity_id(
                "todo", "shopping_list", native.entry_id
            )
            entry = MockConfigEntry(
                domain=DOMAIN,
                title="Live bridge probe",
                unique_id="live-probe",
                data={
                    "tokens": dict(client.tokens),
                    CONF_LISTS: [list_id],
                    CONF_BRIDGE: list_id,
                    CONF_BRIDGE_CONFIRMED: list_id,
                },
            )
            entry.add_to_hass(hass)
            assert await hass.config_entries.async_setup(entry.entry_id), (
                "Listonic setup"
            )
            await hass.async_block_till_done()
            bridge = hass.data[DOMAIN][entry.entry_id].bridge

            async def sync():
                await hass.async_block_till_done()
                await asyncio.wait_for(bridge.async_wait_idle(), timeout=90)
                assert bridge.status == "synchronized"

            async def local():
                result = await hass.services.async_call(
                    "todo",
                    "get_items",
                    {"entity_id": local_id},
                    blocking=True,
                    return_response=True,
                )
                return result[local_id]["items"]

            await sync()
            response = await hass.services.async_call(
                "conversation",
                "process",
                {
                    "text": "Dodaj mleko do listy zakupów",
                    "language": "pl",
                },
                blocking=True,
                return_response=True,
            )
            assert response["response"]["response_type"] != "error"
            await sync()
            products = (await client.lists())[list_id]["Items"]
            assert len(products) == 1 and products[0]["Name"] == "mleko"
            item_id = identifier(products[0]["Id"])
            print(
                "PASS Polish Assist -> native Shopping List -> live Listonic",
                flush=True,
            )

            await client.update_item(
                list_id,
                item_id,
                amount="2.5",
                unit="l",
                price=3.49,
                description="Disposable bridge test",
            )
            await bridge.coordinator.async_refresh()
            await sync()
            uid = (await local())[0]["uid"]
            await hass.services.async_call(
                "todo",
                "update_item",
                {
                    "entity_id": local_id,
                    "item": uid,
                    "rename": "HA bridge renamed",
                    "status": "completed",
                },
                blocking=True,
            )
            await sync()
            product = (await client.lists())[list_id]["Items"][0]
            assert product["Name"] == "HA bridge renamed" and product["Checked"] == 1
            assert str(product["Amount"]) == "2.5" and product["Unit"] == "l"
            assert float(product["Price"]) == 3.49
            assert product["Description"] == "Disposable bridge test"
            print("PASS HA rename/check -> live API, metadata preserved", flush=True)

            await client.update_item(
                list_id, item_id, name="Cloud bridge renamed", checked=0
            )
            await bridge.coordinator.async_refresh()
            await sync()
            items = await local()
            assert (
                items[0]["uid"] == uid and items[0]["summary"] == "Cloud bridge renamed"
            )
            assert items[0]["status"] == "needs_action"
            print("PASS live API rename/uncheck -> native Shopping List", flush=True)
            assert await hass.config_entries.async_unload(entry.entry_id)
            assert await hass.config_entries.async_setup(entry.entry_id)
            await hass.async_block_till_done()
            bridge = hass.data[DOMAIN][entry.entry_id].bridge
            await sync()
            assert len((await client.lists())[list_id]["Items"]) == 1
            assert len(await local()) == 1
            print(
                "PASS HA integration unload/reload without duplicate import", flush=True
            )

            await hass.services.async_call(
                "todo",
                "remove_item",
                {
                    "entity_id": local_id,
                    "item": uid,
                },
                blocking=True,
            )
            await sync()
            assert not (await client.lists())[list_id]["Items"]
            print("PASS HA deletion -> live API", flush=True)

            async def action(service, response=False, **fields):
                return await hass.services.async_call(
                    DOMAIN,
                    service,
                    {"config_entry_id": entry.entry_id, **fields},
                    blocking=True,
                    return_response=response,
                )

            # Exercise the new public HA actions on this disposable list only.
            await action("rename_list", list_id=list_id, name=name + " actions")
            result = await action(
                "add_item",
                response=True,
                list_id=list_id,
                name="Actions product",
                amount="2,5",
                unit="l",
                price=3.49,
                description="Actions metadata",
            )
            action_id = result["item_id"]
            await action(
                "update_item",
                list_id=list_id,
                item_id=action_id,
                name="Actions renamed",
                checked=True,
            )
            result = await action("get_lists", response=True)
            saved = next(
                value for value in result["lists"] if str(value["Id"]) == list_id
            )
            product = saved["Items"][0]
            assert product["Name"] == "Actions renamed" and product["Checked"] == 1
            assert str(product["Amount"]) == "2.5" and product["Unit"] == "l"
            assert float(product["Price"]) == 3.49
            assert product["Description"] == "Actions metadata"
            await action(
                "update_item",
                list_id=list_id,
                item_id=action_id,
                amount="",
                unit="",
                description="",
                price=0,
            )
            product = (await client.lists())[list_id]["Items"][0]
            assert not product["Amount"] and not product["Unit"]
            assert not product["Description"] and float(product["Price"]) == 0
            await action("delete_item", list_id=list_id, item_id=action_id)
            await sync()
            assert not (await client.lists())[list_id]["Items"]
            assert not await local()
            await action("delete_list", list_id=list_id)
            assert list_id not in await client.lists()
            print(
                "PASS HA account actions: rename, item CRUD/metadata/clear, deactivate",
                flush=True,
            )

            # Create through HA, then clean this second list even if assertions fail.
            result = await action("create_list", response=True, name=name + " created")
            extra_id = result["list_id"]
            try:
                assert extra_id in await client.lists()
                assert extra_id not in bridge.coordinator.data
                await action("delete_list", list_id=extra_id)
                assert extra_id not in await client.lists()
                print(
                    "PASS HA create_list response ID and explicit selection", flush=True
                )
            finally:
                if extra_id in await client.lists():
                    await client.update_list(extra_id, Active=0)
        finally:
            if entry and entry.entry_id in hass.data.get(DOMAIN, {}):
                await hass.config_entries.async_unload(entry.entry_id)
            if list_id in await client.lists():
                await client.update_list(list_id, Active=0)
            assert list_id not in await client.lists()
            print("PASS cleanup: disposable cloud list deactivated", flush=True)


class CredentialsPlugin:
    """Pass credentials into pytest in memory, without argv, environment or files."""

    def __init__(self, email, password):
        self.credentials = (email, password)

    @pytest.fixture
    def live_credentials(self):
        return self.credentials


def safe_log_handle(logger, record):
    """Show error location without log arguments, exception messages or locals."""
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
    user = input("Listonic email: ")
    secret = getpass.getpass("Listonic password: ")
    plugin = CredentialsPlugin(user, secret)
    # Never dump authenticated config-entry data or captured output on failure.
    logging.disable(logging.NOTSET)
    result = 1
    try:
        # HA's fixture Store logs serialized entry data at DEBUG. Replace handles
        # even if pytest temporarily changes the logging level during fixtures.
        with patch("logging.Logger.handle", safe_log_handle):
            result = pytest.main(
                ["-q", "-s", __file__, "--tb=no", "--show-capture=no", "--timeout=180"],
                plugins=[plugin],
            )
    except Exception as error:
        print(f"FAIL live bridge probe: {type(error).__name__}", flush=True)
    finally:
        plugin.credentials = None
        secret = None
    raise SystemExit(result)
