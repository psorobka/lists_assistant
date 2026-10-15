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
from .conflicts import (
    apply_policy,
    commit,
    freeze_peer,
    preview,
    remap_local,
    save_plan,
    update_issue,
    validate_resolutions,
    verify_authority,
)
from .const import CONF_INTERVAL, DEFAULT_INTERVAL, settings
from .keep_api import GoogleKeepClient, GoogleKeepError
from .reconcile import product_key

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
    validate_resolutions(state)
    if not isinstance(state.get("local_list_id"), (str, type(None))):
        raise BridgeStorageError("Invalid Google Keep local list identity")


class KeepBridge:
    """Synchronize stable Keep item IDs with Shopping List item IDs."""

    source = "google_keep"

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
        update_issue(self, status)
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
            _LOGGER.info(
                "Google Keep sync initialized: mapped=%d pending=%d",
                len(self.state["records"]),
                len(self.state["pending"]),
            )
        except BridgeStorageError:
            _LOGGER.error("Google Keep bridge storage could not be loaded")
            self._storage_failed = True
            self._set_status("storage_error")
            return
        try:
            self.client.state = await self.client.async_authenticate()
        except GoogleKeepError:
            _LOGGER.warning("Google Keep synchronization authentication failed")
            self._set_status("auth_failed")
            return
        _LOGGER.info("Google Keep synchronization authenticated")
        self._unsubscribers = [
            self.hass.bus.async_listen(EVENT_SHOPPING_LIST_UPDATED, self._local_event),
            self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._state_event),
        ]
        self.request_sync()

    @callback
    def _local_event(self, event):
        _LOGGER.debug(
            "Google Keep sync triggered by Shopping List event: action=%s",
            event.data.get("action", "unknown"),
        )
        self.request_sync()

    @callback
    def _state_event(self, event):
        if self.local_entity_id is None and shopping_list_entity(self.hass):
            _LOGGER.debug(
                "Google Keep sync triggered because Shopping List became available"
            )
            self.request_sync()

    @callback
    def request_sync(self, *, force=False):
        if self._stopped or self._storage_failed:
            _LOGGER.debug(
                "Google Keep sync request ignored: stopped=%s storage_failed=%s",
                self._stopped,
                self._storage_failed,
            )
            return
        if force:
            self._retry_at = 0
        self._dirty = True
        _LOGGER.debug(
            "Google Keep synchronization requested: force=%s task_running=%s",
            force,
            self._task is not None,
        )
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
        _LOGGER.info("Google Keep synchronization started")
        try:
            for iteration in range(1, 21):
                if not self._dirty or self._stopped:
                    _LOGGER.debug(
                        "Google Keep sync loop stopped: iteration=%d dirty=%s "
                        "stopped=%s",
                        iteration,
                        self._dirty,
                        self._stopped,
                    )
                    break
                self._dirty = False
                _LOGGER.debug("Google Keep sync loop iteration %d started", iteration)
                async with self._lock:
                    await self.coordinator.async_mutate(self._sync_once, refresh=False)
                _LOGGER.debug(
                    "Google Keep sync loop iteration %d finished: dirty=%s",
                    iteration,
                    self._dirty,
                )
            if self._dirty and not self._stopped:
                _LOGGER.debug(
                    "Google Keep sync loop reached its iteration limit; "
                    "another run will be scheduled"
                )
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
            _LOGGER.info(
                "Google Keep synchronization finished: status=%s mapped=%d "
                "pending=%d conflicts=%d",
                self.status,
                len(self.state["records"]),
                len(self.state["pending"]),
                len(self.state["conflicts"]),
            )
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
        _LOGGER.debug("Google Keep next periodic sync scheduled in %.0f seconds", delay)

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
            _LOGGER.debug("Google Keep sync skipped: Shopping List is unavailable")
            self._set_status("shopping_list_missing")
            return
        self.local_entity_id, local_list_id = native
        if self.state["local_list_id"] not in (None, local_list_id):
            _LOGGER.warning("Google Keep sync stopped: Shopping List identity changed")
            self._set_status("target_changed")
            return
        self.state["local_list_id"] = local_list_id
        uncertain_count = sum(
            op.get("state") == "uncertain" for op in self.state["pending"].values()
        )
        if uncertain_count:
            _LOGGER.warning(
                "Google Keep sync paused: %d write(s) have an uncertain result",
                uncertain_count,
            )
            self._set_status("conflict")
            return
        if self.client.state is None:
            _LOGGER.debug("Google Keep sync authenticating before reading items")
            self.client.state = await self.client.async_authenticate()
        self.client.state = await self.client.async_refresh()
        _LOGGER.debug("Google Keep session refreshed; fetching both snapshots")
        local = await self._local()
        remote = await self.client.async_items(self.list_id)
        _LOGGER.info(
            "Google Keep sync snapshot: shopping_list_items=%d keep_items=%d mapped=%d",
            len(local),
            len(remote),
            len(self.state["records"]),
        )
        mapped_keep_ids = self._mapped_keep_ids()
        _LOGGER.debug(
            "Google Keep snapshots fetched: shopping_list_unmapped=%d keep_unmapped=%d",
            sum(item_id not in self.state["records"] for item_id in local),
            sum(item_id not in mapped_keep_ids for item_id in remote),
        )
        planned = await apply_policy(self, local, remote)
        planned = await freeze_peer(self, planned)
        known_pairs = {
            (ha_id, record["remote_id"])
            for ha_id, record in self.state["records"].items()
        }
        matched_pairs = [
            (ha_id, record["remote_id"])
            for ha_id, record in planned["records"].items()
            if (ha_id, record["remote_id"]) not in known_pairs
            and ha_id in local
            and record["remote_id"] in remote
            and product_key(local[ha_id]["name"])
            == product_key(remote[record["remote_id"]]["name"])
        ]
        if matched_pairs:
            _LOGGER.info(
                "Google Keep sync matched %d existing product(s) by normalized name",
                len(matched_pairs),
            )
        await save_plan(self, planned)
        operation_counts = {}
        for operation in self.state["pending"].values():
            operation_type = f"{operation['direction']}_{operation['action']}"
            operation_counts[operation_type] = (
                operation_counts.get(operation_type, 0) + 1
            )
        _LOGGER.debug(
            "Google Keep sync operation breakdown: %s",
            operation_counts or "none",
        )
        _LOGGER.info(
            "Google Keep sync plan: pending=%d conflicts=%d",
            len(self.state["pending"]),
            len(self.state["conflicts"]),
        )
        if not self.state["pending"]:
            _LOGGER.debug("Google Keep sync has no operations to apply")
            self._set_status("conflict" if self.state["conflicts"] else "synchronized")
            return
        self._set_status("conflict" if self.state["conflicts"] else "pending")
        if monotonic() < self._retry_at:
            _LOGGER.debug(
                "Google Keep sync waiting for retry window: seconds_remaining=%.1f",
                self._retry_at - monotonic(),
            )
            return
        key, operation = next(iter(self.state["pending"].items()))
        _LOGGER.info(
            "Google Keep sync applying %s %s; operations_remaining=%d",
            operation["direction"],
            operation["action"],
            len(self.state["pending"]),
        )
        _LOGGER.debug(
            "Google Keep sync queue head: state=%s remaining_after_this=%d",
            operation["state"],
            max(0, len(self.state["pending"]) - 1),
        )
        operation["state"] = "in_flight"
        target = remote if operation["direction"] == "remote" else local
        operation["before_ids"] = list(target)
        await self._save()
        try:
            await self._apply(operation, local, remote)
        except StaleKeepPlan:
            _LOGGER.debug(
                "Google Keep sync operation became stale; it will be replanned"
            )
            operation["state"] = "queued"
            await self._save()
            self._dirty = True
            return
        except (GoogleKeepError, HomeAssistantError):
            operation["state"] = "uncertain"
            await self._save()
            self._set_status("conflict")
            _LOGGER.error(
                "Google Keep sync write result is uncertain; automatic retry paused"
            )
            return
        self.state["pending"].pop(key)
        await self._save()
        _LOGGER.debug(
            "Google Keep sync operation completed: %s %s; remaining=%d",
            operation["direction"],
            operation["action"],
            len(self.state["pending"]),
        )
        self._dirty = True

    async def _apply(self, operation, local, remote):
        if not await verify_authority(self, operation):
            raise StaleKeepPlan
        to_keep = operation["direction"] == "remote"
        target = remote if to_keep else local
        source = local if to_keep else remote
        target_id = operation["remote_id"] if to_keep else operation["ha_id"]
        source_id = operation["ha_id"] if to_keep else operation["remote_id"]
        _LOGGER.debug(
            "Google Keep sync operation validation: direction=%s action=%s "
            "source_present=%s target_present=%s",
            operation["direction"],
            operation["action"],
            source_id in source,
            target_id in target,
        )
        if (
            source.get(source_id) != operation["source"]
            or target.get(target_id) != operation["before"]
        ):
            _LOGGER.debug(
                "Google Keep sync freshness check failed: source_matches=%s "
                "target_matches=%s",
                source.get(source_id) == operation["source"],
                target.get(target_id) == operation["before"],
            )
            raise StaleKeepPlan
        action, payload = operation["action"], operation["payload"]
        if to_keep:
            if action == "add":
                matches = [
                    item_id
                    for item_id, item in remote.items()
                    if item_id not in self._mapped_keep_ids()
                    if product_key(item["name"]) == product_key(payload["name"])
                ]
                if len(matches) == 1:
                    self.state["records"][source_id] = {
                        "remote_id": matches[0],
                        "base": source[source_id],
                    }
                    _LOGGER.info(
                        "Google Keep sync matched an existing product by name; "
                        "skipped add"
                    )
                    return
            new_id = await self.client.async_apply(
                self.list_id, action, target_id, payload
            )
            _LOGGER.debug(
                "Google Keep API operation returned: action=%s identity_received=%s",
                action,
                bool(new_id),
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
            matches = [
                item_id
                for item_id, item in local.items()
                if item_id not in self.state["records"]
                if product_key(item["name"]) == product_key(payload["name"])
            ]
            if len(matches) == 1:
                await remap_local(self, matches[0], source_id)
                self.state["records"][matches[0]] = {
                    "remote_id": source_id,
                    "base": local[matches[0]],
                }
                _LOGGER.info(
                    "Google Keep sync matched an existing Shopping List product "
                    "by name; skipped add"
                )
                return
            item_id = await self._async_add_local(payload["name"])
            await remap_local(self, item_id, source_id)
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
        _LOGGER.debug("Google Keep import is adding one item to Shopping List")

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
            _LOGGER.warning(
                "Google Keep import could not identify one new Shopping List item: "
                "matching_events=%d",
                len(added),
            )
            raise HomeAssistantError("Shopping List item identity is uncertain")
        _LOGGER.debug("Google Keep import identified one new Shopping List item")
        return added[0]

    def _mapped_keep_ids(self):
        """Return Keep IDs already paired with Shopping List items."""
        return {record["remote_id"] for record in self.state["records"].values()}

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
                            or target[target_id]["name"] != operation["payload"]["name"]
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
                        if operation["direction"] == "local":
                            await remap_local(self, ha_id, remote_id)
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
        """Persist a single explicit decision through the same path as Repairs."""
        expected = await preview(self, choose)
        if ha_id not in expected["conflicts"]:
            raise HomeAssistantError("Synchronization conflict not found")
        if await commit(self, choose, expected, ids=[ha_id]) is not None:
            raise HomeAssistantError("Conflict changed; inspect it again")
