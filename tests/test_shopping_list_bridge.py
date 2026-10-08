"""Real Shopping List HA + fake cloud: CRUD, durable offline state and conflicts."""

from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.const import EVENT_SHOPPING_LIST_UPDATED
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.lists_assistant.api import AmbiguousWrite, ApiError
from custom_components.lists_assistant.bridge_store import BridgeStorageError
from custom_components.lists_assistant.const import (
    BRIDGE_OWNER,
    CONF_BRIDGE,
    CONF_BRIDGE_CONFIRMED,
    CONF_LISTS,
    DOMAIN,
)
from custom_components.lists_assistant.diagnostics import (
    async_get_config_entry_diagnostics,
)


@pytest.fixture
def cloud():
    return {
        "1": {"Id": "1", "Name": "Dom", "Items": []},
        "3": {"Id": "3", "Name": "Other list", "Items": []},
    }


@pytest.fixture
def api(cloud):
    client = MagicMock()
    client.offline = False
    client.ambiguous_add = False
    client.next_id = 100

    async def lists():
        if client.offline:
            raise ApiError("offline")
        return deepcopy(cloud)

    async def add(list_id, name, **fields):
        client.next_id += 1
        item = {
            "Id": str(client.next_id),
            "Name": name,
            "Checked": 0,
            "Amount": "2.5",
            "Unit": "l",
            "Price": 3.49,
            "Description": "Keep me",
        }
        item.update({k.capitalize(): v for k, v in fields.items()})
        cloud[list_id]["Items"].append(item)
        if client.ambiguous_add:
            raise AmbiguousWrite("Response lost after server write")
        return deepcopy(item)

    async def update(list_id, item_id, **fields):
        item = next(i for i in cloud[list_id]["Items"] if i["Id"] == item_id)
        item.update({k.capitalize(): v for k, v in fields.items()})

    async def delete(list_id, item_id):
        cloud[list_id]["Items"] = [
            i for i in cloud[list_id]["Items"] if i["Id"] != item_id
        ]

    client.lists = AsyncMock(side_effect=lists)
    client.add_item = AsyncMock(side_effect=add)
    client.update_item = AsyncMock(side_effect=update)
    client.delete_item = AsyncMock(side_effect=delete)
    with patch(
        "custom_components.lists_assistant.api.ListonicClient", return_value=client
    ):
        yield client


def account(hass, target="1", confirmed=True, unique_id="demo@example.test"):
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Listonic",
        unique_id=unique_id,
        data={
            "tokens": {"access_token": "test"},
            CONF_LISTS: ["1", "3"],
            CONF_BRIDGE: target,
            CONF_BRIDGE_CONFIRMED: target if confirmed else "none",
        },
    )
    entry.add_to_hass(hass)
    return entry


async def setup(hass, entry):
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    bridge = hass.data[DOMAIN][entry.entry_id].bridge
    await bridge.async_wait_idle()
    return bridge


async def todo(hass, entity_id, service, **data):
    return await hass.services.async_call(
        "todo",
        service,
        {"entity_id": entity_id, **data},
        blocking=True,
        return_response=service == "get_items",
    )


async def local_items(hass, entity_id):
    return (await todo(hass, entity_id, "get_items"))[entity_id]["items"]


async def settle(hass, bridge, refresh=False):
    await hass.async_block_till_done()
    if refresh:
        await bridge.coordinator.async_refresh()
    await bridge.async_wait_idle()


async def test_full_bidirectional_crud_and_bulk(hass, native_shopping, api, cloud):
    entry = account(hass)
    bridge = await setup(hass, entry)
    assert bridge.status == "synchronized"
    sensor_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{entry.unique_id}:shopping_list_sync"
    )
    assert sensor_id == "sensor.shopping_list_synchronization"
    assert hass.states.get(sensor_id).state == "synchronized"

    await todo(hass, native_shopping, "add_item", item="Mleko")
    await settle(hass, bridge)
    assert [i["Name"] for i in cloud["1"]["Items"]] == ["Mleko"]
    assert not cloud["3"]["Items"]
    uid = (await local_items(hass, native_shopping))[0]["uid"]
    await todo(
        hass,
        native_shopping,
        "update_item",
        item=uid,
        rename="Mleko nowe",
        status="completed",
    )
    await settle(hass, bridge)
    saved = cloud["1"]["Items"][0]
    assert saved["Name"] == "Mleko nowe" and saved["Checked"] == 1
    assert (saved["Amount"], saved["Unit"], saved["Price"], saved["Description"]) == (
        "2.5",
        "l",
        3.49,
        "Keep me",
    )
    saved["Name"], saved["Checked"] = "Phone rename", 0
    await settle(hass, bridge, refresh=True)
    items = await local_items(hass, native_shopping)
    assert items[0]["uid"] == uid and items[0]["summary"] == "Phone rename"
    assert items[0]["status"] == "needs_action"

    cloud["1"]["Items"].append({"Id": "222", "Name": "Chleb", "Checked": 1})
    await settle(hass, bridge, refresh=True)
    assert len(await local_items(hass, native_shopping)) == 2
    await hass.services.async_call("shopping_list", "complete_all", {}, blocking=True)
    await settle(hass, bridge)
    assert all(i["Checked"] == 1 for i in cloud["1"]["Items"])
    await hass.services.async_call(
        "shopping_list", "clear_completed_items", {}, blocking=True
    )
    await settle(hass, bridge)
    assert cloud["1"]["Items"] == []
    assert bridge.status == "synchronized"


async def test_first_merge_preserves_all_duplicates_and_restart(
    hass, native_shopping, api, cloud
):
    await todo(hass, native_shopping, "add_item", item="Mleko")
    await todo(hass, native_shopping, "add_item", item="Mleko")
    cloud["1"]["Items"] = [
        {"Id": "201", "Name": "Mleko", "Checked": 0},
        {"Id": "202", "Name": "Mleko", "Checked": 1},
    ]
    entry = account(hass)
    bridge = await setup(hass, entry)
    assert len(cloud["1"]["Items"]) == 4
    assert len(await local_items(hass, native_shopping)) == 4
    assert len(bridge.state["records"]) == 4
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert BRIDGE_OWNER not in hass.data
    bridge = await setup(hass, entry)
    assert len(cloud["1"]["Items"]) == 4
    assert len(await local_items(hass, native_shopping)) == 4
    assert bridge.status == "synchronized"


async def test_import_skips_unchanged_listonic_readback(
    hass, native_shopping, api, cloud
):
    cloud["1"]["Items"] = [
        {"Id": str(item_id), "Name": f"Item {item_id}", "Checked": 0}
        for item_id in range(201, 204)
    ]

    bridge = await setup(hass, account(hass))

    assert bridge.status == "synchronized"
    # One initial snapshot and one pre-write freshness check per imported item.
    # Local todo writes do not need the extra unchanged Listonic readback.
    assert api.lists.await_count == 4


async def test_offline_queue_survives_unload_and_recovery(
    hass, native_shopping, api, cloud
):
    entry = account(hass)
    bridge = await setup(hass, entry)
    api.offline = True
    await bridge.coordinator.async_refresh()
    await todo(hass, native_shopping, "add_item", item="Offline milk")
    await settle(hass, bridge)
    assert bridge.status == "pending"
    assert len(bridge.state["pending"]) == 1
    persisted = deepcopy(await bridge.store.store.async_load())
    assert len(persisted["bindings"]["1"]["pending"]) == 1
    assert not cloud["1"]["Items"]
    assert await hass.config_entries.async_unload(entry.entry_id)
    api.offline = False
    bridge = await setup(hass, entry)
    assert [i["Name"] for i in cloud["1"]["Items"]] == ["Offline milk"]
    assert bridge.status == "synchronized"
    api.add_item.assert_awaited_once()


async def test_uncertain_post_requires_explicit_link_never_replayed(
    hass, native_shopping, api, cloud
):
    entry = account(hass)
    bridge = await setup(hass, entry)
    api.ambiguous_add = True
    await todo(hass, native_shopping, "add_item", item="Mleko")
    await settle(hass, bridge)
    assert bridge.status == "conflict"
    api.ambiguous_add = False
    await settle(hass, bridge, refresh=True)
    assert len(cloud["1"]["Items"]) == 1
    api.add_item.assert_awaited_once()
    assert len(await local_items(hass, native_shopping)) == 1
    assert await hass.config_entries.async_unload(entry.entry_id)
    bridge = await setup(hass, entry)
    api.add_item.assert_awaited_once()
    issues = await hass.services.async_call(
        DOMAIN,
        "get_sync_issues",
        {"config_entry_id": entry.entry_id},
        blocking=True,
        return_response=True,
    )
    operation_id = next(
        k for k, op in issues["pending"].items() if op["state"] == "uncertain"
    )
    with pytest.raises(ServiceValidationError):
        await bridge.async_resolve_write(operation_id, "applied", "9999")
    await hass.services.async_call(
        DOMAIN,
        "resolve_sync_write",
        {
            "config_entry_id": entry.entry_id,
            "operation_id": operation_id,
            "outcome": "applied",
            "target_item_id": cloud["1"]["Items"][0]["Id"],
        },
        blocking=True,
    )
    await settle(hass, bridge)
    assert bridge.status == "synchronized"
    assert len(bridge.state["records"]) == 1
    assert len(await local_items(hass, native_shopping)) == 1


@pytest.mark.parametrize(
    "choose,expected", [("home_assistant", "HA name"), ("listonic", "Phone name")]
)
async def test_conflicting_edits_need_resolution(
    hass, native_shopping, api, cloud, choose, expected
):
    entry = account(hass)
    bridge = await setup(hass, entry)
    await todo(hass, native_shopping, "add_item", item="Mleko")
    await settle(hass, bridge)
    uid = (await local_items(hass, native_shopping))[0]["uid"]
    api.offline = True
    await bridge.coordinator.async_refresh()
    await todo(hass, native_shopping, "update_item", item=uid, rename="HA name")
    await settle(hass, bridge)
    cloud["1"]["Items"][0]["Name"] = "Phone name"
    api.offline = False
    await settle(hass, bridge, refresh=True)
    assert bridge.status == "conflict"
    assert cloud["1"]["Items"][0]["Name"] == "Phone name"
    assert (await local_items(hass, native_shopping))[0]["summary"] == "HA name"
    await hass.services.async_call(
        DOMAIN,
        "resolve_sync_conflict",
        {
            "config_entry_id": entry.entry_id,
            "conflict_id": uid,
            "choose": choose,
        },
        blocking=True,
    )
    await settle(hass, bridge)
    assert bridge.status == "synchronized"
    assert cloud["1"]["Items"][0]["Name"] == expected
    assert (await local_items(hass, native_shopping))[0]["summary"] == expected


async def test_missing_target_never_switches_to_another_list(
    hass, native_shopping, api, cloud
):
    entry = account(hass)
    bridge = await setup(hass, entry)
    del cloud["1"]
    await settle(hass, bridge, refresh=True)
    await todo(hass, native_shopping, "add_item", item="Stay local")
    await settle(hass, bridge)
    assert bridge.status == "list_unavailable"
    assert cloud["3"]["Items"] == []
    cloud["1"] = {"Id": "1", "Name": "Dom", "Items": []}
    await settle(hass, bridge, refresh=True)
    assert cloud["1"]["Items"][0]["Name"] == "Stay local"


async def test_binding_requires_confirmation_and_single_owner(
    hass, native_shopping, api, cloud
):
    entry = account(hass, confirmed=False)
    await todo(hass, native_shopping, "add_item", item="Local only")
    bridge = await setup(hass, entry)
    assert bridge.status == "confirmation_required" and not cloud["1"]["Items"]
    other = account(hass, target="3", unique_id="other@example.test")
    assert not await hass.config_entries.async_setup(other.entry_id)
    await hass.async_block_till_done()
    assert hass.data[BRIDGE_OWNER] == entry.entry_id
    assert not cloud["3"]["Items"]


async def test_diagnostics_and_unload_listeners(hass, native_shopping, api):
    entry = account(hass)
    bridge = await setup(hass, entry)
    result = await async_get_config_entry_diagnostics(hass, entry)
    assert result["synchronization"]["status"] == "synchronized"
    assert "demo@" not in str(result) and "test" not in str(result)
    assert await hass.config_entries.async_unload(entry.entry_id)
    hass.bus.async_fire(EVENT_SHOPPING_LIST_UPDATED)
    await hass.async_block_till_done()
    assert bridge._task is None
    assert not hass.services.has_service(DOMAIN, "get_sync_issues")
    assert await async_get_config_entry_diagnostics(hass, entry) == {"loaded": False}


async def test_missing_native_list_waits_then_recovers(hass, api, cloud):
    entry = account(hass)
    bridge = await setup(hass, entry)
    assert bridge.status == "shopping_list_missing"
    native = MockConfigEntry(domain="shopping_list", unique_id="shopping_list", data={})
    native.add_to_hass(hass)
    assert await hass.config_entries.async_setup(native.entry_id)
    await settle(hass, bridge)
    assert bridge.status == "synchronized"


async def test_storage_failure_blocks_side_effects(hass, native_shopping, api, cloud):
    entry = account(hass)
    bridge = await setup(hass, entry)
    with patch.object(
        bridge.store, "save", side_effect=BridgeStorageError("disk full")
    ):
        await todo(hass, native_shopping, "add_item", item="Must stay local")
        await settle(hass, bridge)
    assert bridge.status == "storage_error"
    assert not cloud["1"]["Items"]
    with pytest.raises(ServiceValidationError, match="storage"):
        await hass.services.async_call(
            DOMAIN,
            "get_sync_issues",
            {
                "config_entry_id": entry.entry_id,
            },
            blocking=True,
            return_response=True,
        )


async def test_invalid_store_blocks_start(hass, native_shopping, api, cloud):
    entry = account(hass)
    from homeassistant.helpers.storage import Store

    await Store(hass, 1, f"lists_assistant_bridge.{entry.entry_id}").async_save(
        {"bindings": []}
    )
    await todo(hass, native_shopping, "add_item", item="Do not import")
    bridge = await setup(hass, entry)
    assert bridge.status == "storage_error"
    assert not cloud["1"]["Items"]


async def test_in_flight_create_on_restart_is_uncertain(
    hass, native_shopping, api, cloud
):
    entry = account(hass)
    bridge = await setup(hass, entry)
    api.offline = True
    await bridge.coordinator.async_refresh()
    await todo(hass, native_shopping, "add_item", item="Mleko")
    await settle(hass, bridge)
    operation = next(iter(bridge.state["pending"].values()))
    operation.update(state="in_flight", before_ids=[])
    await bridge.store.save()
    assert await hass.config_entries.async_unload(entry.entry_id)
    api.offline = False
    bridge = await setup(hass, entry)
    assert bridge.status == "conflict"
    api.add_item.assert_not_awaited()
    operation_id = next(
        k for k, o in bridge.state["pending"].items() if o["state"] == "uncertain"
    )
    await bridge.async_resolve_write(operation_id, "not_applied")
    await settle(hass, bridge)
    assert len(cloud["1"]["Items"]) == 1
    assert bridge.status == "synchronized"


async def test_target_change_shelves_old_pending_ids(hass, native_shopping, api, cloud):
    entry = account(hass)
    bridge = await setup(hass, entry)
    api.offline = True
    await bridge.coordinator.async_refresh()
    await todo(hass, native_shopping, "add_item", item="For the previous target")
    await settle(hass, bridge)
    api.offline = False
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_LISTS: ["1", "3"],
            CONF_BRIDGE: "3",
        },
    )
    assert result["description_placeholders"]["held_count"] == "1"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"confirm": True}
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    new_bridge = hass.data[DOMAIN][entry.entry_id].bridge
    await new_bridge.async_wait_idle()
    assert bridge._stopped and new_bridge.list_id == "3"
    assert not cloud["1"]["Items"] and not cloud["3"]["Items"]
    assert new_bridge.status == "shelved"
    assert new_bridge.details()["shelved"]["1"]["pending"]
    await todo(hass, native_shopping, "add_item", item="For the new target")
    await settle(hass, new_bridge)
    assert [i["Name"] for i in cloud["3"]["Items"]] == ["For the new target"]
    assert not cloud["1"]["Items"]


async def test_disconnect_keeps_items_and_queue(hass, native_shopping, api, cloud):
    entry = account(hass)
    bridge = await setup(hass, entry)
    await todo(hass, native_shopping, "add_item", item="Mleko")
    await settle(hass, bridge)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_LISTS: ["1", "3"],
            CONF_BRIDGE: "none",
        },
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    assert hass.data[DOMAIN][entry.entry_id].bridge is None
    assert len(cloud["1"]["Items"]) == 1
    assert len(await local_items(hass, native_shopping)) == 1
    with pytest.raises(ServiceValidationError, match="no Shopping List"):
        await hass.services.async_call(
            DOMAIN,
            "get_sync_issues",
            {
                "config_entry_id": entry.entry_id,
            },
            blocking=True,
            return_response=True,
        )


async def test_options_offline_has_clear_error(hass, api):
    entry = account(hass, target="none")
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    api.offline = True
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "abort" and result["reason"] == "cannot_connect"


@pytest.mark.parametrize("choice", ["home_assistant", "listonic"])
async def test_delete_vs_edit_is_preserved_until_resolution(
    hass, native_shopping, api, cloud, choice
):
    entry = account(hass)
    bridge = await setup(hass, entry)
    await todo(hass, native_shopping, "add_item", item="Mleko")
    await settle(hass, bridge)
    uid = (await local_items(hass, native_shopping))[0]["uid"]
    api.offline = True
    await bridge.coordinator.async_refresh()
    await todo(hass, native_shopping, "remove_item", item=uid)
    await settle(hass, bridge)
    cloud["1"]["Items"][0]["Name"] = "Phone edit"
    api.offline = False
    await settle(hass, bridge, refresh=True)
    assert bridge.status == "conflict" and cloud["1"]["Items"]
    await bridge.async_resolve_conflict(uid, choice)
    await settle(hass, bridge)
    if choice == "home_assistant":
        assert not cloud["1"]["Items"]
        assert not await local_items(hass, native_shopping)
    else:
        assert cloud["1"]["Items"][0]["Name"] == "Phone edit"
        assert (await local_items(hass, native_shopping))[0]["summary"] == "Phone edit"
    assert bridge.status == "synchronized"


async def test_polish_conversation_process_reaches_cloud(
    hass, native_shopping, api, cloud
):
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "conversation", {})
    await hass.async_block_till_done()
    entry = account(hass)
    bridge = await setup(hass, entry)
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
    await settle(hass, bridge)
    assert [i["Name"] for i in cloud["1"]["Items"]] == ["mleko"]
    assert bridge.status == "synchronized"


async def test_options_reject_other_account_binding(hass, native_shopping, api):
    owner = account(hass)
    await setup(hass, owner)
    other = account(hass, target="none", unique_id="other@example.test")
    assert await hass.config_entries.async_setup(other.entry_id)
    await hass.async_block_till_done()
    result = await hass.config_entries.options.async_init(other.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_LISTS: ["3"],
            CONF_BRIDGE: "3",
        },
    )
    assert result["errors"] == {"base": "bridge_in_use"}
    assert hass.data[BRIDGE_OWNER] == owner.entry_id


async def test_unloaded_options_and_empty_cloud(hass, api, cloud):
    entry = account(hass, target="none")
    with patch(
        "custom_components.lists_assistant.config_flow.ListonicClient", return_value=api
    ) as client_type:
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["step_id"] == "init"
        await client_type.call_args.kwargs["on_tokens"](
            {
                "access_token": "rotated",
                "refresh_token": "renewed",
            }
        )
        assert entry.data["tokens"]["refresh_token"] == "renewed"
        result = await hass.config_entries.options.async_configure(
            result["flow_id"],
            {
                CONF_LISTS: [],
                CONF_BRIDGE: "none",
            },
        )
        assert result["errors"] == {"base": "select_list"}
        hass.config_entries.options.async_abort(result["flow_id"])
        cloud.clear()
        result = await hass.config_entries.options.async_init(entry.entry_id)
        assert result["reason"] == "no_lists"


async def test_uncertain_old_write_blocks_target_switch(hass, native_shopping, api):
    entry = account(hass)
    bridge = await setup(hass, entry)
    api.ambiguous_add = True
    await todo(hass, native_shopping, "add_item", item="Mleko")
    await settle(hass, bridge)
    api.ambiguous_add = False
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_LISTS: ["1", "3"],
            CONF_BRIDGE: "3",
        },
    )
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"confirm": True}
    )
    assert result["errors"] == {"base": "resolve_before_switch"}
    assert bridge.list_id == "1"


async def test_source_edit_during_preflight_recomputes_before_post(
    hass, native_shopping, api, cloud
):
    entry = account(hass)
    bridge = await setup(hass, entry)
    original = api.lists.side_effect
    raced = False

    async def change_source():
        nonlocal raced
        if not raced:
            raced = True
            uid = (await local_items(hass, native_shopping))[0]["uid"]
            await todo(
                hass, native_shopping, "update_item", item=uid, rename="New name"
            )
        return await original()

    api.lists.side_effect = change_source
    await todo(hass, native_shopping, "add_item", item="Old name")
    await settle(hass, bridge)
    assert [i["Name"] for i in cloud["1"]["Items"]] == ["New name"]
    api.add_item.assert_awaited_once_with("1", "New name")


async def test_cancelled_post_is_durable_and_not_replayed(
    hass, native_shopping, api, cloud
):
    import asyncio

    entry = account(hass)
    bridge = await setup(hass, entry)
    original = api.add_item.side_effect
    written = asyncio.Event()
    release = asyncio.Event()

    async def lost_response(*args, **kwargs):
        result = await original(*args, **kwargs)
        written.set()
        await release.wait()
        return result

    api.add_item.side_effect = lost_response
    await todo(hass, native_shopping, "add_item", item="Mleko")
    await asyncio.wait_for(written.wait(), timeout=5)
    saved = await bridge.store.store.async_load()
    assert (
        next(iter(saved["bindings"]["1"]["pending"].values()))["state"] == "in_flight"
    )
    assert await hass.config_entries.async_unload(entry.entry_id)
    api.add_item.side_effect = original
    bridge = await setup(hass, entry)
    assert bridge.status == "conflict"
    assert len(cloud["1"]["Items"]) == 1
    api.add_item.assert_awaited_once()


async def test_local_concurrent_duplicate_is_not_guessed(
    hass, native_shopping, api, cloud
):
    entry = account(hass)
    bridge = await setup(hass, entry)
    original = bridge._local
    inserted = False

    async def concurrent_user_addition():
        nonlocal inserted
        items = await original()
        if items and not inserted:
            inserted = True
            await todo(hass, native_shopping, "add_item", item="Mleko")
            items = await original()
        return items

    cloud["1"]["Items"].append({"Id": "222", "Name": "Mleko", "Checked": 0})
    with patch.object(bridge, "_local", side_effect=concurrent_user_addition):
        await settle(hass, bridge, refresh=True)
    assert bridge.status == "conflict"
    assert len(await local_items(hass, native_shopping)) == 2
    api.add_item.assert_not_awaited()
    operation_id = next(
        k for k, o in bridge.state["pending"].items() if o["state"] == "uncertain"
    )
    target_id = (await local_items(hass, native_shopping))[0]["uid"]
    await bridge.async_resolve_write(operation_id, "applied", target_id)
    await settle(hass, bridge)
    assert bridge.status == "synchronized"
    assert len(cloud["1"]["Items"]) == 2
    assert len(await local_items(hass, native_shopping)) == 2


async def test_remote_delete_propagates_without_recreation(
    hass, native_shopping, api, cloud
):
    entry = account(hass)
    bridge = await setup(hass, entry)
    await todo(hass, native_shopping, "add_item", item="Mleko")
    await settle(hass, bridge)
    cloud["1"]["Items"].clear()
    await settle(hass, bridge, refresh=True)
    assert not await local_items(hass, native_shopping)
    assert not bridge.state["records"]
    api.add_item.assert_awaited_once()


async def test_polish_assist_offline_confirms_local_queue(
    hass, native_shopping, api, cloud
):
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "homeassistant", {})
    assert await async_setup_component(hass, "conversation", {})
    await hass.async_block_till_done()
    entry = account(hass)
    bridge = await setup(hass, entry)
    api.offline = True
    await bridge.coordinator.async_refresh()
    response = await hass.services.async_call(
        "conversation",
        "process",
        {
            "text": "Dodaj chleb do listy zakupów",
            "language": "pl",
        },
        blocking=True,
        return_response=True,
    )
    assert response["response"]["response_type"] != "error"
    await settle(hass, bridge)
    assert bridge.status == "pending" and not cloud["1"]["Items"]
    assert len(bridge.state["pending"]) == 1
    api.offline = False
    await settle(hass, bridge, refresh=True)
    assert [i["Name"] for i in cloud["1"]["Items"]] == ["chleb"]


async def test_create_readback_failure_does_not_queue_false_local_delete(
    hass, native_shopping, api, cloud
):
    entry = account(hass)
    bridge = await setup(hass, entry)
    original = api.add_item.side_effect

    async def add_then_disconnect(*args, **kwargs):
        result = await original(*args, **kwargs)
        api.offline = True
        return result

    api.add_item.side_effect = add_then_disconnect
    await todo(hass, native_shopping, "add_item", item="Mleko")
    await settle(hass, bridge)
    assert bridge.status == "pending"
    assert not any(
        op["direction"] == "local" and op["action"] == "delete"
        for op in bridge.state["pending"].values()
    )
    assert len(await local_items(hass, native_shopping)) == 1
    api.offline = False
    await settle(hass, bridge, refresh=True)
    assert bridge.status == "synchronized"
    api.add_item.assert_awaited_once()
