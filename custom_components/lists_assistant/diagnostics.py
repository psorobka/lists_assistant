"""Anonymized synchronization counts; never include shopping names or tokens."""

from .const import DOMAIN


async def async_get_config_entry_diagnostics(hass, entry):
    coordinator = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if coordinator is None:
        return {"loaded": False}
    result = {"loaded": True, "last_update_success": coordinator.last_update_success}
    if coordinator.bridge:
        bridge = coordinator.bridge
        result["synchronization"] = {
            "status": bridge.status,
            "pending_count": len(bridge.state["pending"]),
            "conflict_count": len(bridge.state["conflicts"]),
            "mapped_count": len(bridge.state["records"]),
            "shelved_item_count": len(bridge.held_local_ids()),
        }
    return result
