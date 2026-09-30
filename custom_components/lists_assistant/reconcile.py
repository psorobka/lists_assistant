"""Three-way reconciliation, independent of HA and network side effects."""

from copy import deepcopy
from typing import Any


def value(item: dict[str, Any]) -> dict[str, Any]:
    """Only synchronize the fields the native Shopping List can represent."""
    return {"name": item["Name"], "checked": bool(item.get("Checked"))}


def plan_changes(records: dict, local: dict, remote: dict) -> dict:
    """Preserve distinct IDs, combine independent edits and surface conflicts.

    Initial merge keeps every item. Equal names never establish identity.
    A deletion may propagate only if the other side still equals the common base.
    """
    records = deepcopy(records)
    pending, conflicts = {}, {}

    def operation(direction, action, ha_id, remote_id, payload, before, source):
        key = f"{direction}:{action}:{ha_id or remote_id}"
        pending[key] = {
            "direction": direction,
            "action": action,
            "ha_id": ha_id,
            "remote_id": remote_id,
            "payload": payload,
            "before": before,
            "source": source,
            "state": "queued",
        }

    for ha_id, record in list(records.items()):
        remote_id, base = record["remote_id"], record["base"]
        left, right = local.get(ha_id), remote.get(remote_id)
        if left is None and right is None:
            del records[ha_id]
            continue
        if left == right:
            record["base"] = left
            continue
        if left is None and right == base:
            operation("remote", "delete", ha_id, remote_id, None, right, None)
            continue
        if right is None and left == base:
            operation("local", "delete", ha_id, remote_id, None, left, None)
            continue
        merged = {}
        if left is not None and right is not None:
            for field in ("name", "checked"):
                if left[field] == right[field] or right[field] == base[field]:
                    merged[field] = left[field]
                elif left[field] == base[field]:
                    merged[field] = right[field]
                else:
                    break
        if len(merged) != 2:
            conflicts[ha_id] = {
                "ha_id": ha_id,
                "remote_id": remote_id,
                "base": base,
                "local": left,
                "remote": right,
            }
            continue
        if left != merged:
            operation("local", "update", ha_id, remote_id, merged, left, right)
        if right != merged:
            operation("remote", "update", ha_id, remote_id, merged, right, left)

    mapped_remote = {r["remote_id"] for r in records.values()}
    for ha_id, item in local.items():
        if ha_id not in records:
            operation("remote", "add", ha_id, None, item, None, item)
    for remote_id, item in remote.items():
        if remote_id not in mapped_remote:
            operation("local", "add", None, remote_id, item, None, item)
    return {"records": records, "pending": pending, "conflicts": conflicts}
