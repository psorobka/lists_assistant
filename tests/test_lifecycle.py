"""Exercise real HA setup, todo services, recovery and unload with a fake API."""

import asyncio
from copy import deepcopy
from datetime import timedelta
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.lists_assistant.api import AmbiguousWrite, ApiError, AuthError
from custom_components.lists_assistant.const import CONF_LISTS, DOMAIN


@pytest.fixture
def entry(hass):
    account = MockConfigEntry(
        domain=DOMAIN,
        title="Listonic test",
        unique_id="demo@example.test",
        data={"tokens": {"access_token": "fake"}, CONF_LISTS: ["1"]},
    )
    account.add_to_hass(hass)
    return account


@pytest.fixture
def remote():
    return {
        "1": {
            "Id": "1",
            "Name": "Zakupy",
            "Items": [
                {
                    "Id": "2",
                    "Name": "Mleko",
                    "Checked": 0,
                    "Description": "Notatka",
                    "Amount": "2.5",
                    "Unit": "l",
                    "Price": 3.49,
                }
            ],
        },
        "3": {"Id": "3", "Name": "Unselected", "Items": []},
    }


@pytest.fixture
def api(remote):
    client = AsyncMock()
    client.lists.side_effect = lambda: deepcopy(remote)

    async def add(list_id, name, **fields):
        item = {"Id": "4", "Name": name, "Checked": 0}
        item.update({key.capitalize(): value for key, value in fields.items()})
        remote[list_id]["Items"].append(item)
        return deepcopy(item)

    async def update(list_id, item_id, **fields):
        item = next(i for i in remote[list_id]["Items"] if i["Id"] == item_id)
        item.update({key.capitalize(): value for key, value in fields.items()})

    async def delete(list_id, item_id):
        remote[list_id]["Items"] = [
            i for i in remote[list_id]["Items"] if i["Id"] != item_id
        ]

    client.add_item.side_effect = add
    client.update_item.side_effect = update
    client.delete_item.side_effect = delete
    with patch(
        "custom_components.lists_assistant.api.ListonicClient", return_value=client
    ) as client_type:
        client.client_type = client_type
        yield client


async def setup(hass, entry):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id("todo", DOMAIN, "demo@example.test:1")
    assert entity_id is not None
    return entity_id, hass.data[DOMAIN][entry.entry_id]


async def test_services_atomic_update_and_metadata(hass, entry, api, remote):
    entity_id, coordinator = await setup(hass, entry)
    assert coordinator.update_interval == timedelta(seconds=30)
    assert set(coordinator.data) == {"1"}
    assert hass.states.get(entity_id).state == "1"
    assert hass.states.get(entity_id).attributes["list_id"] == "1"

    await hass.services.async_call(
        "todo",
        "update_item",
        {
            "entity_id": entity_id,
            "item": "Mleko",
            "rename": "Mleko nowe",
            "status": "completed",
        },
        blocking=True,
    )
    api.update_item.assert_awaited_once_with("1", "2", name="Mleko nowe", checked=1)
    saved = remote["1"]["Items"][0]
    assert (saved["Amount"], saved["Unit"], saved["Price"], saved["Description"]) == (
        "2.5",
        "l",
        3.49,
        "Notatka",
    )
    assert hass.states.get(entity_id).state == "0"

    await hass.services.async_call(
        "todo",
        "update_item",
        {"entity_id": entity_id, "item": "2", "description": None},
        blocking=True,
    )
    assert saved["Description"] == ""
    api.update_item.assert_awaited_with("1", "2", description="")

    await hass.services.async_call(
        "todo",
        "add_item",
        {"entity_id": entity_id, "item": "Chleb", "description": "Test"},
        blocking=True,
    )
    api.add_item.assert_awaited_once_with("1", "Chleb", description="Test")
    response = await hass.services.async_call(
        "todo",
        "get_items",
        {"entity_id": entity_id},
        blocking=True,
        return_response=True,
    )
    assert [i["summary"] for i in response[entity_id]["items"]] == [
        "Mleko nowe",
        "Chleb",
    ]

    await hass.services.async_call(
        "todo",
        "remove_item",
        {"entity_id": entity_id, "item": ["2", "4"]},
        blocking=True,
    )
    assert remote["1"]["Items"] == []
    assert hass.states.get(entity_id).state == "0"


async def test_availability_rename_and_recovery_keep_entity(hass, entry, api, remote):
    entity_id, coordinator = await setup(hass, entry)
    before = deepcopy(coordinator.data)
    api.lists.side_effect = ApiError("offline")
    await coordinator.async_refresh()
    assert hass.states.get(entity_id).state == "unavailable"
    assert coordinator.data == before
    api.lists.side_effect = lambda: deepcopy(remote)
    del remote["1"]
    await coordinator.async_refresh()
    assert hass.states.get(entity_id).state == "unavailable"
    remote["1"] = {"Id": "1", "Name": "New remote name", "Items": []}
    await coordinator.async_refresh()
    assert hass.states.get(entity_id).state == "0"
    assert hass.states.get(entity_id).attributes["friendly_name"] == "New remote name"
    assert (
        er.async_get(hass).async_get_entity_id("todo", DOMAIN, "demo@example.test:1")
        == entity_id
    )


@pytest.mark.parametrize(
    "error,state",
    [
        (ApiError("offline"), ConfigEntryState.SETUP_RETRY),
        (AuthError("expired"), ConfigEntryState.SETUP_ERROR),
    ],
)
async def test_first_refresh_failure(hass, entry, api, error, state):
    api.lists.side_effect = error
    assert not await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is state
    assert entry.entry_id not in hass.data.get(DOMAIN, {})
    if isinstance(error, AuthError):
        assert any(
            f["context"]["source"] == "reauth"
            for f in hass.config_entries.flow.async_progress()
        )


async def test_confirmed_write_readback_failure_does_not_report_failed_write(
    hass, entry, api, remote
):
    entity_id, coordinator = await setup(hass, entry)
    api.lists.side_effect = ApiError("read failed after successful write")
    await hass.services.async_call(
        "todo",
        "add_item",
        {"entity_id": entity_id, "item": "Chleb"},
        blocking=True,
    )
    api.add_item.assert_awaited_once()
    assert remote["1"]["Items"][-1]["Name"] == "Chleb"
    assert not coordinator.last_update_success


async def test_ambiguous_write_is_not_replayed(hass, entry, api):
    entity_id, coordinator = await setup(hass, entry)
    api.add_item.side_effect = AmbiguousWrite("unknown outcome")
    with pytest.raises(HomeAssistantError, match="Could not add"):
        await hass.services.async_call(
            "todo",
            "add_item",
            {"entity_id": entity_id, "item": "Chleb"},
            blocking=True,
        )
    api.add_item.assert_awaited_once()
    assert not coordinator.last_update_success


async def test_poll_is_serialized_before_write(hass, entry, api, remote):
    _, coordinator = await setup(hass, entry)
    started, release = asyncio.Event(), asyncio.Event()

    async def delayed_read():
        result = deepcopy(remote)
        started.set()
        await release.wait()
        return result

    api.lists.side_effect = delayed_read
    read = asyncio.create_task(coordinator.async_refresh())
    await started.wait()
    write = asyncio.create_task(
        coordinator.async_mutate(lambda: api.add_item("1", "Chleb"))
    )
    await asyncio.sleep(0)
    api.add_item.assert_not_awaited()
    api.lists.side_effect = lambda: deepcopy(remote)
    release.set()
    await asyncio.gather(read, write)
    assert coordinator.data["1"]["Items"][-1]["Name"] == "Chleb"


async def test_tokens_persist_and_unload_reload(hass, entry, api):
    entity_id, coordinator = await setup(hass, entry)
    from custom_components.lists_assistant import async_unload_entry

    save_tokens = api.client_type.call_args.kwargs["on_tokens"]
    await save_tokens({"access_token": "rotated", "refresh_token": "new-refresh"})
    assert entry.data["tokens"]["refresh_token"] == "new-refresh"
    assert entry.data[CONF_LISTS] == ["1"]
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.entry_id not in hass.data[DOMAIN]
    assert not coordinator.async_update_listeners()
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(entity_id).state == "1"
    with patch.object(
        hass.config_entries, "async_unload_platforms", return_value=False
    ):
        assert not await async_unload_entry(hass, entry)
    assert entry.entry_id in hass.data[DOMAIN]
