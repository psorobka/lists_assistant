"""Synchronize the native Shopping List through public todo actions/events."""

import asyncio
import logging
from copy import deepcopy
from time import monotonic

from homeassistant.const import EVENT_SHOPPING_LIST_UPDATED, EVENT_STATE_CHANGED
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import async_call_later

from .api import AmbiguousWrite, ApiError, identifier
from .bridge_store import BridgeStorageError, BridgeStore
from .const import CONF_BRIDGE_CONFIRMED, DOMAIN, settings
from .reconcile import plan_changes, value

_LOGGER = logging.getLogger(__name__)
LOCAL_IMPORT_BATCH_SIZE = 25


class StalePlan(Exception):
    """A snapshot changed before the operation; recompute without writing."""


def shopping_list_entity(hass):
    """Find the native Shopping List by registry identity, not its display name."""
    for entity in er.async_get(hass).entities.values():
        if entity.domain == "todo" and entity.platform == "shopping_list":
            state = hass.states.get(entity.entity_id)
            if state and state.state not in ("unavailable", "unknown"):
                return entity.entity_id, entity.config_entry_id
    return None


class ShoppingListBridge:
    """Durable mappings, three-way merge and conservative ambiguous-write recovery."""

    def __init__(self, hass, entry, coordinator, list_id):
        self.hass, self.entry, self.coordinator = hass, entry, coordinator
        self.list_id = list_id
        self.store = BridgeStore(hass, entry.entry_id)
        self.state = {
            "records": {},
            "pending": {},
            "conflicts": {},
            "local_list_id": None,
        }
        self.status = "pending"
        self.local_entity_id = None
        self._task = None
        self._dirty = False
        self._stopped = False
        self._storage_failed = False
        self._lock = asyncio.Lock()
        self._unsubscribers = []
        self._cancel_retry = None
        self._retry_at = 0
        self._failures = 0

    @property
    def signal(self):
        return f"lists_assistant_bridge_{self.entry.entry_id}"

    async def async_start(self):
        """Load the write-ahead log before registering anything that could write."""
        try:
            self.state = await self.store.load(self.list_id)
            for operation in self.state["pending"].values():
                if operation["state"] == "in_flight":
                    operation["state"] = "uncertain"
            await self.store.save()
        except BridgeStorageError:
            _LOGGER.error("Shopping List bridge storage could not be loaded")
            self._storage_failed = True
            self._set_status("storage_error")
            return
        self._unsubscribers = [
            self.coordinator.async_add_listener(self.request_sync),
            self.hass.bus.async_listen(EVENT_SHOPPING_LIST_UPDATED, self._local_event),
            self.hass.bus.async_listen(EVENT_STATE_CHANGED, self._state_event),
        ]
        self.request_sync()
        _LOGGER.info(
            "Shopping List bridge started for config entry %s", self.entry.entry_id
        )

    @callback
    def _local_event(self, event):
        # Bulk events and Assist's empty payload both require a full snapshot.
        self.request_sync()

    @callback
    def _state_event(self, event):
        if self.local_entity_id is None and shopping_list_entity(self.hass):
            self.request_sync()

    @callback
    def request_sync(self):
        if self._stopped or self._storage_failed:
            return
        self._dirty = True
        if self._task is None:
            self._task = self.entry.async_create_background_task(
                self.hass,
                self._run(),
                "Listonic Shopping List synchronization",
                eager_start=False,
            )

    async def async_wait_idle(self):
        """Wait for scheduled work without waiting for future polling/retry timers."""
        while self._task is not None:
            await asyncio.shield(self._task)

    async def _run(self):
        try:
            for _ in range(100):
                if not self._dirty or self._stopped:
                    break
                self._dirty = False
                async with self._lock:
                    await self._sync()
            if self._dirty:
                self._schedule_retry(0.1)
        except BridgeStorageError:
            _LOGGER.error("Shopping List bridge stopped because its storage failed")
            self._storage_failed = True
            self._set_status("storage_error")
        except HomeAssistantError:
            _LOGGER.warning(
                "Shopping List bridge cannot access the native Shopping List"
            )
            self._set_status("shopping_list_missing")
        finally:
            self._task = None

    @callback
    def _set_status(self, status):
        if status != self.status:
            _LOGGER.debug("Shopping List bridge status changed: %s", status)
        self.status = status
        issue_id = f"shopping_list_{self.entry.entry_id}"
        if status in (
            "conflict",
            "storage_error",
            "confirmation_required",
            "target_changed",
            "list_unavailable",
            "shopping_list_missing",
        ):
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key=status,
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
        async_dispatcher_send(self.hass, self.signal)

    @callback
    def _schedule_retry(self, delay):
        if self._cancel_retry:
            self._cancel_retry()
        self._cancel_retry = async_call_later(
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

    def _remote(self):
        return {
            str(item["Id"]): value(item)
            for item in self.coordinator.data[self.list_id]["Items"]
        }

    def held_local_ids(self):
        """Pending changes belonging to a shelved target never become new imports."""
        held = set()
        for list_id, state in self.store.data["bindings"].items():
            if list_id == self.list_id:
                continue
            held.update(op["ha_id"] for op in state["pending"].values() if op["ha_id"])
            held.update(state["conflicts"])
        return held

    async def _sync(self):
        _LOGGER.debug("Reconciling Listonic and Shopping List snapshots")
        if settings(self.entry).get(CONF_BRIDGE_CONFIRMED) != self.list_id:
            self._set_status("confirmation_required")
            return
        native = shopping_list_entity(self.hass)
        if native is None:
            self.local_entity_id = None
            self._set_status("shopping_list_missing")
            return
        self.local_entity_id, local_list_id = native
        if self.state["local_list_id"] not in (None, local_list_id):
            self._set_status("target_changed")
            return
        self.state["local_list_id"] = local_list_id
        local = {
            uid: item
            for uid, item in (await self._local()).items()
            if uid not in self.held_local_ids()
        }
        if self.list_id not in self.coordinator.data:
            self._set_status("list_unavailable")
            return
        remote = self._remote()
        if not self.coordinator.last_update_success:
            # A confirmed create can be newer than the last successful readback.
            # Its absence from a stale snapshot is not evidence of deletion.
            for record in self.state["records"].values():
                remote.setdefault(record["remote_id"], record["base"])
        uncertain = {}
        for key, operation in self.state["pending"].items():
            if operation["state"] != "uncertain":
                continue
            target = remote if operation["direction"] == "remote" else local
            target_id = (
                operation["remote_id"]
                if operation["direction"] == "remote"
                else operation["ha_id"]
            )
            if (
                operation["action"] != "add"
                and self.coordinator.last_update_success
                and target.get(target_id) == operation["payload"]
            ):
                continue  # An explicit-ID update/delete has been confirmed by readback.
            uncertain[key] = operation
        planned = plan_changes(self.state["records"], local, remote)
        self.state.update(planned)
        self.state["pending"].update(uncertain)
        await self.store.save()
        if uncertain or self.state["conflicts"]:
            _LOGGER.warning(
                "Shopping List bridge has %s conflicts and %s uncertain writes",
                len(self.state["conflicts"]),
                len(uncertain),
            )
            self._set_status("conflict")
            return
        if not self.coordinator.last_update_success:
            self._set_status("pending")
            return
        if not self.state["pending"]:
            self._failures = 0
            self._set_status("shelved" if self.held_local_ids() else "synchronized")
            return
        self._set_status("pending")
        if monotonic() < self._retry_at:
            self._schedule_retry(self._retry_at - monotonic())
            return
        key, operation = next(iter(self.state["pending"].items()))
        if operation["direction"] == "local" and operation["action"] == "add":
            try:
                snapshot_valid = await self.coordinator.async_mutate(
                    self._apply_local_import_batch,
                    refresh=False,
                )
            except ApiError:
                _LOGGER.warning("Shopping List import failed; scheduling a retry")
                self._failures += 1
                self._retry_at = monotonic() + min(300, 5 * 2 ** min(self._failures, 6))
                self._schedule_retry(self._retry_at - monotonic())
            else:
                if not snapshot_valid:
                    _LOGGER.debug(
                        "Shopping List import snapshot became stale; recalculating"
                    )
                    await self.coordinator.async_refresh()
                else:
                    self._failures = 0
                self._dirty = True
            await self.store.save()
            return

        operation["state"] = "in_flight"
        operation["before_ids"] = list(
            remote if operation["direction"] == "remote" else local
        )
        await self.store.save()  # Write-ahead checkpoint precedes every side effect.
        try:
            await self.coordinator.async_mutate(
                lambda: self._apply(operation),
                refresh=operation["direction"] != "local",
            )
        except StalePlan:
            _LOGGER.debug("Shopping List bridge plan became stale; recalculating")
            operation["state"] = "queued"
            await self.coordinator.async_refresh()
            self._dirty = True
        except (AmbiguousWrite, HomeAssistantError):
            _LOGGER.error("Shopping List bridge write result is uncertain")
            operation["state"] = "uncertain"
            self._set_status("conflict")
        except ApiError:
            _LOGGER.warning("Shopping List bridge operation failed; scheduling a retry")
            operation["state"] = "queued"
            self._failures += 1
            self._retry_at = monotonic() + min(300, 5 * 2 ** min(self._failures, 6))
            self._schedule_retry(self._retry_at - monotonic())
        else:
            self.state["pending"].pop(key)
            self._dirty = True
        await self.store.save()

    async def _apply_local_import_batch(self):
        """Import a bounded group from one verified snapshot via public todo actions."""
        batch = []
        for key, operation in self.state["pending"].items():
            if (
                operation["direction"] != "local"
                or operation["action"] != "add"
                or operation["state"] != "queued"
            ):
                break
            batch.append((key, operation))
            if len(batch) == LOCAL_IMPORT_BATCH_SIZE:
                break

        lists = await self.coordinator.client.lists()
        if self.list_id not in lists:
            raise ApiError("Bridge target is unavailable")
        remote = {str(item["Id"]): value(item) for item in lists[self.list_id]["Items"]}
        local = await self._local()
        if any(
            remote.get(operation["remote_id"]) != operation["source"]
            for _, operation in batch
        ):
            return False

        for key, operation in batch:
            operation["state"] = "in_flight"
            operation["before_ids"] = list(local)
            await self.store.save()
            try:
                uid = await self._async_add_local_item(operation["payload"]["name"])
            except (AmbiguousWrite, HomeAssistantError):
                _LOGGER.error("Shopping List import result is uncertain")
                operation["state"] = "uncertain"
                self._set_status("conflict")
                await self.store.save()
                return True

            self.state["records"][uid] = {
                "remote_id": operation["remote_id"],
                "base": {"name": operation["payload"]["name"], "checked": False},
            }
            self.state["pending"].pop(key)
            local[uid] = {
                "name": operation["payload"]["name"],
                "checked": False,
            }
            await self.store.save()
        return True

    async def _async_add_local_item(self, name):
        """Create through todo.add_item and read the new ID from HA's update event."""
        added = []

        @callback
        def capture_added_item(event):
            item = event.data.get("item")
            if (
                event.data.get("action") == "add"
                and isinstance(item, dict)
                and item.get("name") == name
                and item.get("complete") is False
                and item.get("id")
            ):
                added.append(str(item["id"]))

        unsubscribe = self.hass.bus.async_listen(
            EVENT_SHOPPING_LIST_UPDATED, capture_added_item
        )
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
            raise AmbiguousWrite("Local item identity is uncertain")
        return added[0]

    async def _apply(self, operation):
        """Check both sides again, holding the coordinator's operation lock."""
        lists = await self.coordinator.client.lists()
        if self.list_id not in lists:
            raise ApiError("Bridge target is unavailable")
        remote = {str(i["Id"]): value(i) for i in lists[self.list_id]["Items"]}
        local = await self._local()
        to_remote = operation["direction"] == "remote"
        target = remote if to_remote else local
        source = local if to_remote else remote
        target_id = operation["remote_id"] if to_remote else operation["ha_id"]
        source_id = operation["ha_id"] if to_remote else operation["remote_id"]
        if (
            source.get(source_id) != operation["source"]
            or target.get(target_id) != operation["before"]
        ):
            raise StalePlan
        operation["before_ids"] = list(target)
        await self.store.save()
        action, payload = operation["action"], operation["payload"]
        if to_remote:
            if action == "add":
                created = await self.coordinator.client.add_item(
                    self.list_id, payload["name"]
                )
                try:
                    remote_id = identifier(created["Id"])
                except (ApiError, KeyError, TypeError) as err:
                    raise AmbiguousWrite("Created item identity is unknown") from err
                if remote_id in remote:
                    raise AmbiguousWrite("Created item identity was already present")
                self.state["records"][source_id] = {
                    "remote_id": remote_id,
                    "base": {"name": payload["name"], "checked": False},
                }
            elif action == "update":
                await self.coordinator.client.update_item(
                    self.list_id,
                    target_id,
                    name=payload["name"],
                    checked=int(payload["checked"]),
                )
            else:
                await self.coordinator.client.delete_item(self.list_id, target_id)
        else:
            data = {"entity_id": self.local_entity_id}
            if action == "add":
                await self.hass.services.async_call(
                    "todo",
                    "add_item",
                    {**data, "item": payload["name"]},
                    blocking=True,
                )
                after = await self._local()
                candidates = [
                    uid
                    for uid in after
                    if uid not in local
                    and after[uid] == {"name": payload["name"], "checked": False}
                ]
                if len(candidates) != 1:
                    raise AmbiguousWrite("Local item identity is uncertain")
                self.state["records"][candidates[0]] = {
                    "remote_id": source_id,
                    "base": {"name": payload["name"], "checked": False},
                }
            elif action == "update":
                await self.hass.services.async_call(
                    "todo",
                    "update_item",
                    {
                        **data,
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
                    {**data, "item": target_id},
                    blocking=True,
                )

    async def async_resolve_conflict(self, ha_id, choose):
        """Explicitly choose a side for the current edit/delete conflict."""
        async with self._lock:
            if ha_id not in self.state["conflicts"]:
                raise ServiceValidationError("Synchronization conflict not found")
            await self.coordinator.async_refresh()
            if (
                not self.coordinator.last_update_success
                or self.list_id not in self.coordinator.data
            ):
                raise ServiceValidationError("Listonic target is unavailable")
            local, remote = await self._local(), self._remote()
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
                del self.state["records"][
                    ha_id
                ]  # Explicitly recreate the chosen surviving item.
            elif other is not None:
                record["base"] = other
            self.state["conflicts"].pop(ha_id)
            await self.store.save()
        self.request_sync()

    async def async_resolve_write(self, operation_id, outcome, target_id=None):
        """User verifies an uncertain write; never guess identity from names."""
        async with self._lock:
            operation = self.state["pending"].get(operation_id)
            if operation is None or operation["state"] != "uncertain":
                raise ServiceValidationError("Uncertain write not found")
            if outcome == "applied":
                await self.coordinator.async_refresh()
                if (
                    not self.coordinator.last_update_success
                    or self.list_id not in self.coordinator.data
                ):
                    raise ServiceValidationError("Listonic target is unavailable")
                target = (
                    self._remote()
                    if operation["direction"] == "remote"
                    else await self._local()
                )
                if operation["action"] == "add":
                    if target_id not in target or target_id in operation["before_ids"]:
                        raise ServiceValidationError(
                            "Select a newly created target item"
                        )
                    if target[target_id]["name"] != operation["payload"]["name"]:
                        raise ServiceValidationError(
                            "The target item does not match this write"
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
                    if ha_id in self.state["records"] or any(
                        r["remote_id"] == remote_id
                        for r in self.state["records"].values()
                    ):
                        raise ServiceValidationError(
                            "The target item is already mapped"
                        )
                    self.state["records"][ha_id] = {
                        "remote_id": remote_id,
                        "base": target[target_id],
                    }
                else:
                    target_id = (
                        operation["remote_id"]
                        if operation["direction"] == "remote"
                        else operation["ha_id"]
                    )
                    if target.get(target_id) != operation["payload"]:
                        raise ServiceValidationError(
                            "The write has not been confirmed by readback"
                        )
            self.state["pending"].pop(operation_id)
            await self.store.save()
        self.request_sync()

    async def async_stop(self):
        self._stopped = True
        ir.async_delete_issue(self.hass, DOMAIN, f"shopping_list_{self.entry.entry_id}")
        for unsubscribe in self._unsubscribers:
            unsubscribe()
        if self._cancel_retry:
            self._cancel_retry()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        _LOGGER.info(
            "Shopping List bridge stopped for config entry %s", self.entry.entry_id
        )

    def details(self):
        """Explicit troubleshooting action, separate from anonymized diagnostics."""
        return deepcopy(
            {
                "pending": self.state["pending"],
                "conflicts": self.state["conflicts"],
                "shelved": {
                    key: state
                    for key, state in self.store.data["bindings"].items()
                    if key != self.list_id and (state["pending"] or state["conflicts"])
                },
            }
        )
