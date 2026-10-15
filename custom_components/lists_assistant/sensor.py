"""Visible local-vs-cloud synchronization status without shopping data."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.const import EntityCategory
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.util import slugify

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
    coordinator = hass.data[DOMAIN][entry.entry_id]
    bridge = coordinator.bridge
    entities = []
    if bridge is not None:
        entities.append(BridgeStatusSensor(bridge))
    if coordinator.keep_bridge is not None:
        entities.append(GoogleKeepStatusSensor(coordinator.keep_bridge))
    registry = er.async_get(hass)
    for entity in entities:
        old_entity_id = registry.async_get_entity_id("sensor", DOMAIN, entity.unique_id)
        old_registry_entry = (
            registry.async_get(old_entity_id) if old_entity_id else None
        )
        if isinstance(entity, BridgeStatusSensor):
            generated_id = "sensor.shopping_list_synchronization"
        elif (
            old_registry_entry is not None
            and old_registry_entry.original_name is not None
        ):
            generated_id = f"sensor.{slugify(old_registry_entry.original_name)}"
        else:
            generated_id = None
        suggested_id = (
            "sensor.lists_assistant_listonic_synchronization"
            if isinstance(entity, BridgeStatusSensor)
            else "sensor.lists_assistant_google_keep_synchronization"
        )
        entity.entity_id = suggested_id
        if (
            old_entity_id is not None
            and old_entity_id == generated_id
            and old_entity_id != suggested_id
            and registry.async_get(suggested_id) is None
        ):
            registry.async_update_entity(old_entity_id, new_entity_id=suggested_id)
    async_add_entities(entities)


class BridgeStatusSensor(SensorEntity):
    """Keep pending/conflict state visible even when the cloud is unavailable."""

    _attr_has_entity_name = True
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


class GoogleKeepStatusSensor(SensorEntity):
    """Expose Google Keep synchronization status without item data."""

    _attr_has_entity_name = True
    _attr_translation_key = "google_keep_sync"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_should_poll = False
    _attr_options = [
        "pending",
        "synchronized",
        "conflict",
        "auth_failed",
        "shopping_list_missing",
        "storage_error",
        "target_changed",
    ]

    def __init__(self, bridge):
        self.bridge = bridge
        self._attr_unique_id = f"{bridge.entry.unique_id}:google_keep_sync"

    @property
    def native_value(self):
        return self.bridge.status

    @property
    def extra_state_attributes(self):
        return {
            "mapped_count": len(self.bridge.state["records"]),
            "pending_count": len(self.bridge.state["pending"]),
            "conflict_count": len(self.bridge.state["conflicts"]),
        }

    async def async_added_to_hass(self):
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, self.bridge.signal, self.async_write_ha_state
            )
        )
