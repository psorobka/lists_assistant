"""Shared conflict decisions, identity checks and repair previews."""

from copy import deepcopy

from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir

from .api import ApiError
from .bridge_store import BridgeStorageError
from .const import (
    CONF_BRIDGE,
    CONF_CONFLICT_POLICY,
    CONF_KEEP_LIST_ID,
    DOMAIN,
    settings,
)
from .keep_api import GoogleKeepError
from .reconcile import plan_changes, value


def policy_choices(data):
    """Only offer sources actually bound to the shared Shopping List."""
    choices = ["manual", "home_assistant"]
    if data.get(CONF_BRIDGE, "none") != "none":
        choices.append("listonic")
    if data.get(CONF_KEEP_LIST_ID, "none") != "none":
        choices.append("google_keep")
    return choices


def validate_resolutions(state):
    """A malformed saved decision must stop writes, like a malformed queue."""
    decisions = state.get("resolutions", {})
    if not isinstance(decisions, dict):
        raise BridgeStorageError("Invalid conflict decisions")
    for key, decision in decisions.items():
        if (
            not isinstance(key, str)
            or key not in state["records"]
            or not isinstance(decision, dict)
        ):
            raise BridgeStorageError("Invalid conflict decision")
        chosen = decision.get("value")
        if (
            "value" not in decision
            or decision.get("source")
            not in ("manual", "home_assistant", "listonic", "google_keep")
            or (
                chosen is not None
                and (
                    not isinstance(chosen, dict)
                    or not isinstance(chosen.get("name"), str)
                    or not isinstance(chosen.get("checked"), bool)
                )
            )
        ):
            raise BridgeStorageError("Invalid conflict decision")


async def remap_local(bridge, ha_id, remote_id):
    """Move a recreation decision to its new local ID without duplicate mappings."""
    state = bridge.state
    for old_id, record in list(state["records"].items()):
        if old_id != ha_id and record["remote_id"] == remote_id:
            peer = (
                bridge.coordinator.keep_bridge
                if bridge.source == "listonic"
                else bridge.coordinator.bridge
            )
            if peer is not None and old_id in peer.state["records"]:
                peer.state["records"][ha_id] = peer.state["records"].pop(old_id)
                peer_decisions = peer.state.get("resolutions", {})
                if old_id in peer_decisions:
                    peer_decisions[ha_id] = peer_decisions.pop(old_id)
                conflict = peer.state["conflicts"].pop(old_id, None)
                if conflict is not None:
                    conflict["ha_id"] = ha_id
                    peer.state["conflicts"][ha_id] = conflict
                for operation in peer.state["pending"].values():
                    if operation["ha_id"] == old_id:
                        operation["ha_id"] = ha_id
                try:
                    await peer._save()
                except BridgeStorageError:
                    peer._storage_failed = True
                    peer._set_status("storage_error")
                    raise
                peer.request_sync()
            del state["records"][old_id]
            decisions = state.get("resolutions", {})
            if old_id in decisions:
                decisions[ha_id] = decisions.pop(old_id)


def update_issue(bridge, status):
    """Expose an actionable repair for either bridge's ordinary conflicts."""
    issue_id = f"{bridge.source}_{bridge.entry.entry_id}_conflicts"
    if status == "conflict" and bridge.state["conflicts"]:
        ir.async_create_issue(
            bridge.hass,
            DOMAIN,
            issue_id,
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key="sync_conflicts",
            translation_placeholders={
                "count": str(len(bridge.state["conflicts"])),
                "source": "Listonic" if bridge.source == "listonic" else "Google Keep",
            },
            data={"entry_id": bridge.entry.entry_id, "source": bridge.source},
        )
    else:
        ir.async_delete_issue(bridge.hass, DOMAIN, issue_id)


async def snapshots(bridge):
    """Fetch full snapshots with the account operation lock already held."""
    # Imported here because both bridge implementations use this module.
    from .bridge import shopping_list_entity

    native = shopping_list_entity(bridge.hass)
    if native is None or bridge.state["local_list_id"] != native[1]:
        raise HomeAssistantError("Shopping List identity is unavailable or changed")
    if bridge._storage_failed or bridge._stopped:
        raise HomeAssistantError("Bridge is unavailable")
    bridge.local_entity_id = native[0]
    local = await bridge._local()
    if bridge.source == "listonic":
        lists = await bridge.coordinator.client.lists()
        if bridge.list_id not in lists:
            raise HomeAssistantError("Listonic target is unavailable")
        remote = {
            str(item["Id"]): value(item) for item in lists[bridge.list_id]["Items"]
        }
    else:
        bridge.client.state = await bridge.client.async_refresh()
        remote = await bridge.client.async_items(bridge.list_id)
    return local, remote


async def chosen_values(bridge, choose, ids, local, remote):
    """Read authority through stable shared HA IDs; never infer it from names."""
    if choose == "home_assistant":
        return {uid: local.get(uid) for uid in ids}
    authority = bridge
    if choose != bridge.source:
        authority = (
            bridge.coordinator.bridge
            if choose == "listonic"
            else bridge.coordinator.keep_bridge
        )
        if authority is None:
            raise HomeAssistantError("Preferred source is not connected")
        _, remote = await snapshots(authority)
    if any(op["state"] != "queued" for op in authority.state["pending"].values()):
        raise HomeAssistantError("Preferred source has an uncertain write")
    if any(uid not in authority.state["records"] for uid in ids):
        raise HomeAssistantError("Preferred source has no stable mapping for this item")
    return {
        uid: remote.get(authority.state["records"][uid]["remote_id"]) for uid in ids
    }


async def apply_policy(bridge, local, remote):
    """Persist automatic choices before the executor produces any side effects."""
    planned = plan_changes(
        bridge.state["records"], local, remote, bridge.state.get("resolutions", {})
    )
    policy = settings(bridge.entry).get(CONF_CONFLICT_POLICY, "manual")
    if policy == "manual" or policy not in policy_choices(settings(bridge.entry)):
        return planned
    ids = set(planned["conflicts"]) | {
        uid
        for uid, decision in planned["resolutions"].items()
        if decision["source"] != "manual"
    }
    if not ids:
        return planned
    decisions = deepcopy(planned["resolutions"])
    deferred = set()
    for uid in ids:
        try:
            chosen = await chosen_values(bridge, policy, [uid], local, remote)
        except (HomeAssistantError, ApiError, GoogleKeepError):
            # Defer this item while leaving independent products free to sync.
            deferred.add(uid)
            continue
        decisions[uid] = {"value": chosen[uid], "source": policy}
    result = plan_changes(bridge.state["records"], local, remote, decisions)
    for uid in deferred:
        record = bridge.state["records"][uid]
        result["conflicts"][uid] = {
            "ha_id": uid,
            "remote_id": record["remote_id"],
            "base": record["base"],
            "local": local.get(uid),
            "remote": remote.get(record["remote_id"]),
        }
        result["pending"] = {
            key: operation
            for key, operation in result["pending"].items()
            if operation["ha_id"] != uid
            and operation["remote_id"] != record["remote_id"]
        }
    return result


async def verify_authority(bridge, operation):
    """Check a third source again before executing a planned automatic write."""
    uid = operation["ha_id"]
    if uid is None:
        uid = next(
            (
                key
                for key, record in bridge.state["records"].items()
                if record["remote_id"] == operation["remote_id"]
            ),
            None,
        )
    decision = bridge.state.get("resolutions", {}).get(uid)
    if decision is None or decision["source"] in (
        "manual",
        "home_assistant",
        bridge.source,
    ):
        return True
    try:
        local, remote = await snapshots(bridge)
        chosen = await chosen_values(bridge, decision["source"], [uid], local, remote)
    except (HomeAssistantError, ApiError, GoogleKeepError):
        return False
    return chosen[uid] == decision["value"]


async def freeze_peer(bridge, planned):
    """Hold shared IDs across both bridges while manual conflicts are unresolved."""
    peer = (
        bridge.coordinator.keep_bridge
        if bridge.source == "listonic"
        else bridge.coordinator.bridge
    )
    if peer is None:
        return planned
    if peer._storage_failed or any(
        op["state"] != "queued" for op in peer.state["pending"].values()
    ):
        planned["pending"] = {}
        return planned
    held = set(peer.state["conflicts"]) | set(peer.state.get("resolutions", {}))
    try:
        local, remote = await snapshots(peer)
        held.update(plan_changes(peer.state["records"], local, remote)["conflicts"])
    except (HomeAssistantError, ApiError, GoogleKeepError):
        pass
    planned["pending"] = {
        key: op
        for key, op in planned["pending"].items()
        if op["ha_id"] not in held or op["ha_id"] in planned["resolutions"]
    }
    return planned


async def save_plan(bridge, planned):
    """Publish a plan under the account lock and wake newly unblocked shared IDs."""
    held = set(bridge.state["conflicts"]) | set(bridge.state.get("resolutions", {}))
    bridge.state.update(planned)
    await bridge._save()
    remaining = set(planned["conflicts"]) | set(planned["resolutions"])
    if held - remaining:
        peer = (
            bridge.coordinator.keep_bridge
            if bridge.source == "listonic"
            else bridge.coordinator.bridge
        )
        if peer is not None:
            peer.request_sync()


async def preview_locked(bridge, choose):
    """Capture the exact inputs and decisions the user will confirm."""
    if choose not in policy_choices(settings(bridge.entry)) or choose == "manual":
        raise HomeAssistantError("Invalid source")
    if any(op["state"] != "queued" for op in bridge.state["pending"].values()):
        raise HomeAssistantError("Resolve uncertain writes first")
    local, remote = await snapshots(bridge)
    planned = plan_changes(
        bridge.state["records"], local, remote, bridge.state.get("resolutions", {})
    )
    chosen = await chosen_values(bridge, choose, planned["conflicts"], local, remote)
    return deepcopy(
        {
            "list_id": bridge.list_id,
            "local_list_id": bridge.state["local_list_id"],
            "records": bridge.state["records"],
            "local": local,
            "remote": remote,
            "conflicts": planned["conflicts"],
            "chosen": chosen,
        }
    )


async def preview(bridge, choose):
    async with bridge._lock:
        return await bridge.coordinator.async_mutate(
            lambda: preview_locked(bridge, choose), refresh=False
        )


async def commit(bridge, choose, expected, ids=None):
    """Save all reviewed decisions together, or return an updated preview."""
    async with bridge._lock:

        async def save():
            current = await preview_locked(bridge, choose)
            if current != expected:
                return current
            decisions = deepcopy(bridge.state.get("resolutions", {}))
            decisions.update(
                {
                    uid: {"value": item, "source": "manual"}
                    for uid, item in current["chosen"].items()
                    if ids is None or uid in ids
                }
            )
            bridge.state.update(
                plan_changes(
                    bridge.state["records"],
                    current["local"],
                    current["remote"],
                    decisions,
                )
            )
            await bridge._save()
            return None

        changed = await bridge.coordinator.async_mutate(save, refresh=False)
    if changed is None:
        bridge.request_sync()
    return changed
