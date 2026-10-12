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
    from .const import (
        BRIDGE_OWNER,
        CONF_BRIDGE,
        CONF_KEEP_LIST_ID,
        CONF_KEEP_LISTS,
        CONF_KEEP_TOKEN,
        CONF_KEEP_USERNAME,
        CONF_SOURCES,
        DOMAIN,
        SOURCE_LISTONIC,
        integration_title,
        settings,
    )
    from .coordinator import ListonicCoordinator

    async def save_tokens(tokens: dict) -> None:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, "tokens": tokens}
        )

    if entry.title.startswith(("Listonic (", "Google Keep (")):
        title = integration_title(settings(entry).get(CONF_SOURCES, [SOURCE_LISTONIC]))
        if entry.title != title:
            hass.config_entries.async_update_entry(entry, title=title)

    client = None
    if SOURCE_LISTONIC in settings(entry).get(CONF_SOURCES, [SOURCE_LISTONIC]):
        client = ListonicClient(
            async_get_clientsession(hass),
            entry.data["tokens"],
            on_tokens=save_tokens,
        )
    coordinator = ListonicCoordinator(hass, client, entry)
    await coordinator.async_config_entry_first_refresh()
    bridge_id = settings(entry).get(CONF_BRIDGE, "none")
    keep_bridge_id = settings(entry).get(CONF_KEEP_LIST_ID, "none")
    if bridge_id != "none" or keep_bridge_id != "none":
        if hass.data.get(BRIDGE_OWNER, entry.entry_id) != entry.entry_id:
            raise ConfigEntryError("Shopping List is already bound to another account")
        hass.data[BRIDGE_OWNER] = entry.entry_id
        if bridge_id != "none":
            coordinator.bridge = ShoppingListBridge(hass, entry, coordinator, bridge_id)
            await coordinator.bridge.async_start()
    if keep_bridge_id != "none":
        from .keep_bridge import KeepBridge

        coordinator.keep_bridge = KeepBridge(
            hass,
            entry,
            coordinator,
            settings(entry)[CONF_KEEP_USERNAME],
            settings(entry)[CONF_KEEP_TOKEN],
            keep_bridge_id,
        )
        await coordinator.keep_bridge.async_start()
    if settings(entry).get(CONF_KEEP_LISTS):
        from .keep_api import GoogleKeepClient

        coordinator.keep_todo_client = GoogleKeepClient(
            hass,
            settings(entry)[CONF_KEEP_USERNAME],
            settings(entry)[CONF_KEEP_TOKEN],
        )
        try:
            coordinator.keep_todo_client.state = (
                await coordinator.keep_todo_client.async_authenticate()
            )
            coordinator.keep_todo_names = {
                item["id"]: item.get("title") or item["id"]
                for item in await coordinator.keep_todo_client.async_lists()
            }
        except Exception:
            coordinator.keep_todo_names = {}
            _LOGGER.warning("Google Keep todo lists could not authenticate")
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    try:
        await hass.config_entries.async_forward_entry_setups(
            entry, [Platform.TODO, Platform.SENSOR]
        )
    except Exception:
        if coordinator.bridge:
            await coordinator.bridge.async_stop()
        if coordinator.keep_bridge:
            await coordinator.keep_bridge.async_stop()
        if coordinator.bridge or coordinator.keep_bridge:
            hass.data.pop(BRIDGE_OWNER, None)
        hass.data[DOMAIN].pop(entry.entry_id)
        raise
    from .bridge_services import async_register_services
    from .services import async_register_services as register_account_services

    async_register_services(hass)
    if coordinator.client is not None:
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
    coordinator = hass.data.get(DOMAIN, {}).pop(entry.entry_id, None)
    if coordinator is None:
        return True
    if coordinator.bridge:
        await coordinator.bridge.async_stop()
    if coordinator.keep_bridge:
        await coordinator.keep_bridge.async_stop()
    if coordinator.bridge or coordinator.keep_bridge:
        hass.data.pop(BRIDGE_OWNER, None)
    if not hass.data[DOMAIN]:
        async_remove_services(hass)
        remove_account_services(hass)
    _LOGGER.info("Listonic account unloaded (config entry %s)", entry.entry_id)
    return True
