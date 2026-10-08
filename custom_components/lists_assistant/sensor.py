"""Visible local-vs-cloud synchronization status without shopping data."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import DOMAIN

STATUSES = [
    "shelved",
    "synchronized",
    "pending",
    "conflict",
    "shopping_list_missing",
    "list_unavailable",
    "storage_error",
    "confirmation_required",
    "target_changed",
]


async def async_setup_entry(hass, entry, async_add_entities):
    bridge = hass.data[DOMAIN][entry.entry_id].bridge
    if bridge is not None:
        async_add_entities([BridgeStatusSensor(bridge)])


class BridgeStatusSensor(SensorEntity):
    """Keep pending/conflict state visible even when the cloud is unavailable."""

    _attr_has_entity_name = True
    _attr_suggested_object_id = "shopping_list_synchronization"
    _attr_translation_key = "shopping_list_sync"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False
    _attr_options = STATUSES

    def __init__(self, bridge):
        self.bridge = bridge
        self._attr_unique_id = f"{bridge.entry.unique_id}:shopping_list_sync"

    @property
    def native_value(self):
        return self.bridge.status

    @property
    def extra_state_attributes(self):
        return {
            "list_id": self.bridge.list_id,
            "pending_count": len(self.bridge.state["pending"]),
            "conflict_count": len(self.bridge.state["conflicts"]),
            "uncertain_write_count": sum(
                op["state"] == "uncertain"
                for op in self.bridge.state["pending"].values()
            ),
            "shelved_item_count": len(self.bridge.held_local_ids()),
        }

    async def async_added_to_hass(self):
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self.bridge.signal, self.async_write_ha_state
            )
        )
