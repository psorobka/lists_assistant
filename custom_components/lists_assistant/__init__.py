"""Listonic integration for Home Assistant."""

from __future__ import annotations

import logging

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry) -> bool:
    """Set up a configured Listonic account."""
    from homeassistant.const import Platform
    from homeassistant.exceptions import ConfigEntryError
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    from .api import ListonicClient
    from .bridge import ShoppingListBridge
    from .const import BRIDGE_OWNER, CONF_BRIDGE, DOMAIN, settings
    from .coordinator import ListonicCoordinator

    async def save_tokens(tokens: dict) -> None:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, "tokens": tokens}
        )

    client = ListonicClient(
        async_get_clientsession(hass),
        entry.data["tokens"],
        on_tokens=save_tokens,
    )
    coordinator = ListonicCoordinator(hass, client, entry)
    await coordinator.async_config_entry_first_refresh()
    bridge_id = settings(entry).get(CONF_BRIDGE, "none")
    if bridge_id != "none":
        if hass.data.get(BRIDGE_OWNER, entry.entry_id) != entry.entry_id:
            raise ConfigEntryError("Shopping List is already bound to another account")
        hass.data[BRIDGE_OWNER] = entry.entry_id
        coordinator.bridge = ShoppingListBridge(hass, entry, coordinator, bridge_id)
        await coordinator.bridge.async_start()
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    try:
        await hass.config_entries.async_forward_entry_setups(
            entry, [Platform.TODO, Platform.SENSOR]
        )
    except Exception:
        if coordinator.bridge:
            await coordinator.bridge.async_stop()
            hass.data.pop(BRIDGE_OWNER, None)
        hass.data[DOMAIN].pop(entry.entry_id)
        raise
    from .bridge_services import async_register_services
    from .services import async_register_services as register_account_services

    async_register_services(hass)
    register_account_services(hass)
    _LOGGER.info("Listonic account set up (config entry %s)", entry.entry_id)
    return True


async def async_unload_entry(hass, entry) -> bool:
    """Unload a Listonic account."""
    from homeassistant.const import Platform

    from .bridge_services import async_remove_services
    from .const import BRIDGE_OWNER, DOMAIN
    from .services import async_remove_services as remove_account_services

    if not await hass.config_entries.async_unload_platforms(
        entry, [Platform.TODO, Platform.SENSOR]
    ):
        return False
    coordinator = hass.data[DOMAIN].pop(entry.entry_id)
    if coordinator.bridge:
        await coordinator.bridge.async_stop()
        hass.data.pop(BRIDGE_OWNER, None)
    if not hass.data[DOMAIN]:
        async_remove_services(hass)
        remove_account_services(hass)
    _LOGGER.info("Listonic account unloaded (config entry %s)", entry.entry_id)
    return True
