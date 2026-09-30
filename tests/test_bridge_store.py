"""Corrupt state and failed persistence never trigger a clean slate or a write."""

from copy import deepcopy
from unittest.mock import AsyncMock

import pytest

from custom_components.lists_assistant.bridge_store import (
    BridgeStorageError,
    BridgeStore,
    validate,
)
from custom_components.lists_assistant.reconcile import plan_changes

BASE = {"name": "Milk", "checked": False}
VALID = {
    "bindings": {
        "1": {
            "local_list_id": "native",
            "records": {"h1": {"remote_id": "1", "base": BASE}},
            "pending": {},
            "conflicts": {},
        }
    }
}


@pytest.mark.parametrize(
    "corruption",
    [
        "root",
        "binding",
        "local_id",
        "records",
        "item",
        "quantity_id",
        "duplicate_ids",
        "operation_state",
        "operation_direction",
        "operation_action",
        "operation_payload",
        "operation_remote_id",
        "operation_ha_id",
        "operation_source",
        "missing_before_ids",
    ],
)
def test_invalid_store_is_rejected(corruption):
    data = deepcopy(VALID)
    state = data["bindings"]["1"]
    if corruption.startswith("operation") or corruption == "missing_before_ids":
        state.update(plan_changes({}, {"h1": BASE}, {}))
        operation = next(iter(state["pending"].values()))
        if corruption == "missing_before_ids":
            operation["state"] = "uncertain"
        else:
            field = corruption.removeprefix("operation_")
            operation[field if field != "source" else "ha_id"] = {
                "state": "bad",
                "direction": "bad",
                "action": "bad",
                "payload": [],
                "remote_id": "../wrong",
                "ha_id": "",
                "source": None,
            }[field]
    elif corruption == "root":
        data = []
    elif corruption == "binding":
        data["bindings"]["x"] = data["bindings"].pop("1")
    elif corruption == "local_id":
        state["local_list_id"] = []
    elif corruption == "records":
        state["records"] = []
    elif corruption == "item":
        state["records"]["h1"]["base"]["checked"] = "false"
    elif corruption == "quantity_id":
        state["records"]["h1"]["remote_id"] = "../item"
    else:
        state["records"]["h2"] = deepcopy(state["records"]["h1"])
    with pytest.raises(BridgeStorageError):
        validate(data)


async def test_unreadable_existing_file_is_not_overwritten(hass, tmp_path):
    hass.config.config_dir = str(tmp_path)
    store = BridgeStore(hass, "entry")
    directory = tmp_path / ".storage"
    directory.mkdir()
    path = directory / store.key
    path.write_text("not-json")
    store.store.async_load = AsyncMock(return_value=None)
    with pytest.raises(BridgeStorageError):
        await store.load("1")
    assert path.read_text() == "not-json"


async def test_load_and_save_failures_propagate(hass):
    store = BridgeStore(hass, "entry")
    store.store.async_load = AsyncMock(side_effect=OSError())
    with pytest.raises(BridgeStorageError):
        await store.load("1")
    store.store.async_save = AsyncMock(side_effect=OSError())
    with pytest.raises(BridgeStorageError):
        await store.save()
