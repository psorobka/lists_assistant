"""Bidirectional Google Keep and native Shopping List synchronization."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from copy import deepcopy
from pathlib import Path
from time import monotonic

from homeassistant.const import EVENT_SHOPPING_LIST_UPDATED, EVENT_STATE_CHANGED
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store

from .bridge import shopping_list_entity
from .bridge_store import BridgeStorageError
from .const import CONF_INTERVAL, DEFAULT_INTERVAL, settings
from .keep_api import GoogleKeepClient, GoogleKeepError
from .reconcile import plan_changes

_LOGGER = logging.getLogger(__name__)


class StaleKeepPlan(Exception):
    """The local or Google Keep snapshot changed before an operation."""


def _valid_item(item):
    return (
        isinstance(item, dict)
        and isinstance(item.get("name"), str)
        and isinstance(item.get("checked"), bool)
    )


def _validate_state(state):
    """Reject malformed mappings and write-ahead data without resetting it."""
    if not isinstance(state, dict) or not all(
        isinstance(state.get(key), dict) for key in ("records", "pending", "conflicts")
    ):
        raise BridgeStorageError("Invalid Google Keep bridge state")
    for ha_id, record in state["records"].items():
        if (
            not isinstance(ha_id, str)
            or not ha_id
            or not isinstance(record, dict)
            or not isinstance(record.get("remote_id"), str)
            or not record["remote_id"]
            or not _valid_item(record.get("base"))
        ):
            raise BridgeStorageError("Invalid Google Keep item mapping")
    for key, operation in state["pending"].items():
        if (
            not isinstance(key, str)
            or not isinstance(operation, dict)
            or operation.get("state") not in ("queued", "in_flight", "uncertain")
            or operation.get("direction") not in ("local", "remote")
            or operation.get("action") not in ("add", "update", "delete")
            or (
                operation.get("action") != "delete"
                and not _valid_item(operation.get("payload"))
            )
            or (
                operation.get("action") == "delete"
                and operation.get("payload") is not None
            )
            or (
                operation.get("before") is not None
                and not _valid_item(operation["before"])
            )
            or (
                operation.get("source") is not None
                and not _valid_item(operation["source"])
            )
            or (
                operation.get("state") != "queued"
                and (
                    not isinstance(operation.get("before_ids"), list)
                    or not all(
                        isinstance(item_id, str) for item_id in operation["before_ids"]
                    )
                )
            )
        ):
            raise BridgeStorageError("Invalid Google Keep pending operation")
    if not isinstance(state.get("local_list_id"), (str, type(None))):
        raise BridgeStorageError("Invalid Google Keep local list identity")


class KeepBridge:
    """Synchronize stable Keep item IDs with Shopping List item IDs."""

    def __init__(self, hass, entry, coordinator, username, token, list_id):
        self.hass, self.entry, self.coordinator = hass, entry, coordinator
        self.username, self.token, self.list_id = username, token, list_id
        self.client = GoogleKeepClient(hass, username, token)
        target_key = hashlib.sha256(f"{username}\0{list_id}".encode()).hexdigest()[:20]
        self.storage_key = f"lists_assistant_keep_bridge.{entry.entry_id}.{target_key}"
        self.storage = Store(hass, 1, self.storage_key)
        self.state = {
            "records": {},
            "pending": {},
            "conflicts": {},
            "local_list_id": None,
        }
        self.local_entity_id = None
        self.status = "pending"
        self._unsubscribers = []
        self._cancel_poll = None
        self._task = None
        self._dirty = False
        self._stopped = False
        self._storage_failed = False
        self._lock = asyncio.Lock()
        self._retry_at = 0.0

    async def _save(self):
        try:
            await self.storage.async_save(self.state)
        except Exception as err:
            raise BridgeStorageError("Google Keep state could not be saved") from err

    @callback
    def _set_status(self, status):
        self.status = status
        async_dispatcher_send(self.hass, self.signal)

    @property
    def signal(self):
        return f"lists_assistant_keep_{self.entry.entry_id}"

    async def async_start(self):
        """Restore identity mappings before synchronizing either side."""
        try:
            store_path = Path(self.hass.config.path(".storage", self.storage_key))
            existed = await self.hass.async_add_executor_job(store_path.exists)
            saved = await self.storage.async_load()
            if saved is None and existed:
                raise BridgeStorageError("Existing Google Keep state could not be read")
            if saved is not None:
                _validate_state(saved)
                self.state.update(saved)
                for op in self.state["pending"].values():
                    if op.get("state") == "in_flight":
                        op["state"] = "uncertain"
                await self._save()
        except BridgeStorageError:
            _LOGGER.error("Google Keep bridge storage could not be loaded")
            self._storage_failed = True
            self._set_status("storage_error")
            return
        try:
            self.client.state = await self.client.async_authenticate()
        except GoogleKeepError:
            _LOGGER.warning("Google Keep authentication failed")
            self._set_status("auth_failed")
            return
        self._unsubscribers = [
            self.hass.bus.async_listen(EVENT_SHOPPING_LIST_UPDATED, self._local_event),
            self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._state_event),
        ]
        self.request_sync()

    @callback
    def _local_event(self, event):
        self.request_sync()

    @callback
    def _state_event(self, event):
        if self.local_entity_id is None and shopping_list_entity(self.hass):
            self.request_sync()

    @callback
    def request_sync(self, *, force=False):
        if self._stopped or self._storage_failed:
            return
        if force:
            self._retry_at = 0
        self._dirty = True
        if self._task is None:
            self._task = self.entry.async_create_background_task(
                self.hass,
                self._run(),
                "Google Keep list synchronization",
                eager_start=False,
            )

    async def async_wait_idle(self):
        """Wait for scheduled work without waiting for the next poll timer."""
        while self._task is not None:
            await asyncio.shield(self._task)

    async def _run(self):
        try:
            for _ in range(20):
                if not self._dirty or self._stopped:
                    break
                self._dirty = False
                async with self._lock:
                    await self.coordinator.async_mutate(self._sync_once, refresh=False)
        except BridgeStorageError:
            self._storage_failed = True
            self._set_status("storage_error")
            _LOGGER.error("Google Keep bridge stopped because its storage failed")
        except (GoogleKeepError, HomeAssistantError):
            self._set_status("pending")
            _LOGGER.warning("Google Keep synchronization failed; retrying later")
        except Exception:
            self._set_status("conflict")
            _LOGGER.exception("Google Keep synchronization stopped after an error")
        finally:
            self._task = None
            if not self._stopped and not self._storage_failed:
                interval = settings(self.entry).get(CONF_INTERVAL, DEFAULT_INTERVAL)
                self._schedule_poll(interval * 60)

    @callback
    def _schedule_poll(self, delay):
        if self._cancel_poll:
            self._cancel_poll()
        self._cancel_poll = async_call_later(
            self.hass, delay, lambda _: self.request_sync()
        )

    async def _local(self):
        result = await self.hass.services.async_call(
            "todo",
            "get_items",
            {"entity_id": self.local_entity_id},
            blocking=True,
            return_response=True,
        )
        return {
            item["uid"]: {
                "name": item["summary"],
                "checked": item["status"] == "completed",
            }
            for item in result[self.local_entity_id]["items"]
        }

    async def _sync_once(self):
        native = shopping_list_entity(self.hass)
        if native is None:
            self._set_status("shopping_list_missing")
            return
        self.local_entity_id, local_list_id = native
        if self.state["local_list_id"] not in (None, local_list_id):
            self._set_status("target_changed")
            return
        self.state["local_list_id"] = local_list_id
        if any(op.get("state") == "uncertain" for op in self.state["pending"].values()):
            self._set_status("conflict")
            return
        if self.client.state is None:
            self.client.state = await self.client.async_authenticate()
        self.client.state = await self.client.async_refresh()
        local = await self._local()
        remote = await self.client.async_items(self.list_id)
        planned = plan_changes(self.state["records"], local, remote)
        self.state.update(planned)
        await self._save()
        if self.state["conflicts"]:
            self._set_status("conflict")
            return
        if not self.state["pending"]:
            self._set_status("synchronized")
            return
        self._set_status("pending")
        if monotonic() < self._retry_at:
            return
        key, operation = next(iter(self.state["pending"].items()))
        operation["state"] = "in_flight"
        target = remote if operation["direction"] == "remote" else local
        operation["before_ids"] = list(target)
        await self._save()
        try:
            await self._apply(operation, local, remote)
        except StaleKeepPlan:
            operation["state"] = "queued"
            await self._save()
            self._dirty = True
            return
        except (GoogleKeepError, HomeAssistantError):
            operation["state"] = "uncertain"
            await self._save()
            self._set_status("conflict")
            _LOGGER.error("Google Keep write result is uncertain")
            return
        self.state["pending"].pop(key)
        await self._save()
        self._dirty = True

    async def _apply(self, operation, local, remote):
        to_keep = operation["direction"] == "remote"
        target = remote if to_keep else local
        source = local if to_keep else remote
        target_id = operation["remote_id"] if to_keep else operation["ha_id"]
        source_id = operation["ha_id"] if to_keep else operation["remote_id"]
        if (
            source.get(source_id) != operation["source"]
            or target.get(target_id) != operation["before"]
        ):
            raise StaleKeepPlan
        action, payload = operation["action"], operation["payload"]
        if to_keep:
            new_id = await self.client.async_apply(
                self.list_id, action, target_id, payload
            )
            if action == "add":
                if not new_id or new_id in remote:
                    raise GoogleKeepError("New Google Keep item identity is uncertain")
                self.state["records"][source_id] = {
                    "remote_id": new_id,
                    "base": payload,
                }
            return
        if action == "add":
            item_id = await self._async_add_local(payload["name"])
            self.state["records"][item_id] = {
                "remote_id": source_id,
                "base": payload,
            }
        elif action == "update":
            await self.hass.services.async_call(
                "todo",
                "update_item",
                {
                    "entity_id": self.local_entity_id,
                    "item": target_id,
                    "rename": payload["name"],
                    "status": "completed" if payload["checked"] else "needs_action",
                },
                blocking=True,
            )
        else:
            await self.hass.services.async_call(
                "todo",
                "remove_item",
                {"entity_id": self.local_entity_id, "item": target_id},
                blocking=True,
            )

    async def _async_add_local(self, name):
        added = []

        @callback
        def capture(event):
            item = event.data.get("item")
            if event.data.get("action") == "add" and isinstance(item, dict):
                if (
                    item.get("name") == name
                    and item.get("complete") is False
                    and item.get("id")
                ):
                    added.append(str(item["id"]))

        unsubscribe = self.hass.bus.async_listen(EVENT_SHOPPING_LIST_UPDATED, capture)
        try:
            await self.hass.services.async_call(
                "todo",
                "add_item",
                {"entity_id": self.local_entity_id, "item": name},
                blocking=True,
            )
        finally:
            unsubscribe()
        if len(added) != 1:
            raise HomeAssistantError("Shopping List item identity is uncertain")
        return added[0]

    async def async_stop(self):
        self._stopped = True
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        if self._cancel_poll:
            self._cancel_poll()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    def details(self):
        """Return explicitly requested status without account credentials."""
        return deepcopy(
            {
                "status": self.status,
                "pending": self.state["pending"],
                "conflicts": self.state["conflicts"],
                "mapped_count": len(self.state["records"]),
            }
        )

    async def async_resolve_write(self, operation_id, outcome, target_id=None):
        """Resolve a user-verified uncertain write without guessing item identity."""
        async with self._lock:
            operation = self.state["pending"].get(operation_id)
            if operation is None or operation.get("state") != "uncertain":
                raise HomeAssistantError("Uncertain Google Keep write not found")

            async def resolve():
                self.client = GoogleKeepClient(self.hass, self.username, self.token)
                self.client.state = await self.client.async_authenticate()
                self.client.state = await self.client.async_refresh()
                local = await self._local()
                remote = await self.client.async_items(self.list_id)
                target = remote if operation["direction"] == "remote" else local
                if outcome == "applied":
                    if operation["action"] == "add":
                        if (
                            target_id not in target
                            or target_id in operation.get("before_ids", [])
                            or target[target_id] != operation["payload"]
                        ):
                            raise HomeAssistantError(
                                "Select the newly created matching target item"
                            )
                        ha_id = (
                            operation["ha_id"]
                            if operation["direction"] == "remote"
                            else target_id
                        )
                        remote_id = (
                            target_id
                            if operation["direction"] == "remote"
                            else operation["remote_id"]
                        )
                        self.state["records"][ha_id] = {
                            "remote_id": remote_id,
                            "base": operation["payload"],
                        }
                    elif operation["action"] == "update":
                        target_item_id = (
                            operation["remote_id"]
                            if operation["direction"] == "remote"
                            else operation["ha_id"]
                        )
                        if target.get(target_item_id) != operation["payload"]:
                            raise HomeAssistantError("The update is not confirmed")
                    else:
                        target_item_id = (
                            operation["remote_id"]
                            if operation["direction"] == "remote"
                            else operation["ha_id"]
                        )
                        if target_item_id in target:
                            raise HomeAssistantError("The deletion is not confirmed")
                self.state["pending"].pop(operation_id)
                await self._save()

            await self.coordinator.async_mutate(resolve, refresh=False)
        self.request_sync()

    async def async_resolve_conflict(self, ha_id, choose):
        """Keep the selected side of an edit/delete conflict."""
        async with self._lock:
            if ha_id not in self.state["conflicts"]:
                raise HomeAssistantError("Google Keep conflict not found")

            async def resolve():
                native = shopping_list_entity(self.hass)
                if native is None:
                    raise HomeAssistantError(
                        "Home Assistant Shopping List is unavailable"
                    )
                self.local_entity_id = native[0]
                self.client.state = await self.client.async_refresh()
                local = await self._local()
                remote = await self.client.async_items(self.list_id)
                record = self.state["records"][ha_id]
                chosen = (
                    local.get(ha_id)
                    if choose == "home_assistant"
                    else remote.get(record["remote_id"])
                )
                other = (
                    remote.get(record["remote_id"])
                    if choose == "home_assistant"
                    else local.get(ha_id)
                )
                if chosen is not None and other is None:
                    del self.state["records"][ha_id]
                elif other is not None:
                    record["base"] = other
                self.state["conflicts"].pop(ha_id)
                await self._save()

            await self.coordinator.async_mutate(resolve, refresh=False)
        self.request_sync()
