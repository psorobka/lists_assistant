"""Three-way reconciliation, independent of HA and network side effects."""

import unicodedata
from copy import deepcopy
from typing import Any


def value(item: dict[str, Any]) -> dict[str, Any]:
    """Only synchronize the fields the native Shopping List can represent."""
    return {"name": item["Name"], "checked": bool(item.get("Checked"))}


def product_key(name: str) -> str:
    """Normalize product names for matching independent sources on first sync."""
    return " ".join(unicodedata.normalize("NFKC", name).casefold().split())


def plan_changes(records: dict, local: dict, remote: dict, resolutions=None) -> dict:
    """Preserve distinct IDs, combine independent edits and surface conflicts.

    Initial merge pairs unique, normalized names across sources. Ambiguous names
    remain separate; a deletion propagates only when the other side equals base.
    """
    records = deepcopy(records)
    decisions = deepcopy(resolutions or {})
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
        if ha_id in decisions:
            chosen = decisions[ha_id]["value"]
            if left == right == chosen:
                decisions.pop(ha_id)
            else:
                for direction, current, source in (
                    ("local", left, right),
                    ("remote", right, left),
                ):
                    if current != chosen:
                        action = (
                            "delete"
                            if chosen is None
                            else ("add" if current is None else "update")
                        )
                        operation(
                            direction,
                            action,
                            None if direction == "local" and action == "add" else ha_id,
                            None
                            if direction == "remote" and action == "add"
                            else remote_id,
                            chosen,
                            current,
                            source,
                        )
                continue
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
    unmatched_local = [
        (ha_id, item) for ha_id, item in local.items() if ha_id not in records
    ]
    unmatched_remote = [
        (remote_id, item)
        for remote_id, item in remote.items()
        if remote_id not in mapped_remote
    ]
    local_by_name, remote_by_name = {}, {}
    for ha_id, item in unmatched_local:
        local_by_name.setdefault(product_key(item["name"]), []).append((ha_id, item))
    for remote_id, item in unmatched_remote:
        remote_by_name.setdefault(product_key(item["name"]), []).append(
            (remote_id, item)
        )
    paired = False
    for name in local_by_name.keys() & remote_by_name.keys():
        local_matches, remote_matches = local_by_name[name], remote_by_name[name]
        # Only an unambiguous one-to-one name match can establish a mapping.
        if len(local_matches) == len(remote_matches) == 1:
            ha_id, item = local_matches[0]
            remote_id, _ = remote_matches[0]
            records[ha_id] = {"remote_id": remote_id, "base": item}
            paired = True
    if paired:
        return plan_changes(
            records, local, remote, decisions if resolutions is not None else None
        )

    mapped_remote = {r["remote_id"] for r in records.values()}
    for ha_id, item in local.items():
        if ha_id not in records:
            operation("remote", "add", ha_id, None, item, None, item)
    for remote_id, item in remote.items():
        if remote_id not in mapped_remote:
            operation("local", "add", None, remote_id, item, None, item)
    result = {"records": records, "pending": pending, "conflicts": conflicts}
    if resolutions is not None:
        result["resolutions"] = {
            key: decision for key, decision in decisions.items() if key in records
        }
    return result
