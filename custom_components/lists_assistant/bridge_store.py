"""Durable integration-owned bridge state; invalid state fails closed."""

from pathlib import Path

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .api import identifier


class BridgeStorageError(Exception):
    """Existing synchronization state cannot safely be used or replaced."""


def _item(item):
    return (
        isinstance(item, dict)
        and isinstance(item.get("name"), str)
        and isinstance(item.get("checked"), bool)
    )


def validate(data):
    """Do not silently reset mappings or a write-ahead log after corruption."""
    try:
        if not isinstance(data, dict) or not isinstance(data["bindings"], dict):
            raise ValueError
        for list_id, state in data["bindings"].items():
            identifier(list_id)
            if not isinstance(state["local_list_id"], (str, type(None))):
                raise ValueError
            if not all(
                isinstance(state[k], dict) for k in ("records", "pending", "conflicts")
            ):
                raise ValueError
            remote_ids = set()
            for ha_id, record in state["records"].items():
                if not isinstance(ha_id, str) or not ha_id or not _item(record["base"]):
                    raise ValueError
                remote_id = identifier(record["remote_id"])
                if remote_id in remote_ids:
                    raise ValueError
                remote_ids.add(remote_id)
            for operation_id, operation in state["pending"].items():
                if not isinstance(operation_id, str) or not operation_id:
                    raise ValueError
                if operation["state"] not in ("queued", "in_flight", "uncertain"):
                    raise ValueError
                if operation["direction"] not in ("local", "remote"):
                    raise ValueError
                if operation["action"] not in ("add", "update", "delete"):
                    raise ValueError
                for key in ("payload", "before", "source"):
                    if operation[key] is not None and not _item(operation[key]):
                        raise ValueError
                if operation["remote_id"] is not None:
                    identifier(operation["remote_id"])
                if operation["ha_id"] is not None and (
                    not isinstance(operation["ha_id"], str) or not operation["ha_id"]
                ):
                    raise ValueError
                source_id = (
                    operation["ha_id"]
                    if operation["direction"] == "remote"
                    else operation["remote_id"]
                )
                if source_id is None:
                    raise ValueError
                if operation["action"] == "add":
                    if (
                        not _item(operation["payload"])
                        or operation["before"] is not None
                    ):
                        raise ValueError
                    if operation["state"] != "queued" and (
                        not isinstance(operation.get("before_ids"), list)
                        or not all(
                            isinstance(uid, str) and uid
                            for uid in operation["before_ids"]
                        )
                    ):
                        raise ValueError
                elif operation["ha_id"] is None or operation["remote_id"] is None:
                    raise ValueError
    except Exception as err:
        # Only state-validation failures reach this block; never log private data.
        raise BridgeStorageError("Invalid synchronization state") from err


class BridgeStore:
    """Each target owns its mappings/queue; switching targets shelves the old state."""

    def __init__(self, hass: HomeAssistant, entry_id: str):
        self.hass = hass
        self.key = f"lists_assistant_bridge.{entry_id}"
        self.store = Store(hass, 1, self.key, atomic_writes=True)
        self.data = {"bindings": {}}

    async def load(self, list_id: str) -> dict:
        path = Path(self.hass.config.path(".storage", self.key))
        existed = await self.hass.async_add_executor_job(path.exists)
        try:
            loaded = await self.store.async_load()
            if loaded is None and existed:
                raise BridgeStorageError("Existing store could not be read")
            if loaded is not None:
                validate(loaded)
                self.data = loaded
        except Exception as err:
            raise BridgeStorageError("Could not load synchronization state") from err
        return self.data["bindings"].setdefault(
            list_id,
            {
                "local_list_id": None,
                "records": {},
                "pending": {},
                "conflicts": {},
            },
        )

    async def save(self) -> None:
        try:
            await self.store.async_save(self.data)
        except Exception as err:
            raise BridgeStorageError("Could not persist synchronization state") from err
