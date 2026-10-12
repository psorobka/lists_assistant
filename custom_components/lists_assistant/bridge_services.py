"""Explicit inspection/resolution actions for synchronization problems."""

import voluptuous as vol
from homeassistant.core import SupportsResponse, callback
from homeassistant.exceptions import ServiceValidationError

from .const import DOMAIN

SERVICES = (
    "get_sync_issues",
    "resolve_sync_conflict",
    "resolve_sync_write",
    "resolve_keep_write",
    "resolve_keep_conflict",
    "sync_all",
    "sync_listonic",
    "sync_gkeep",
)


@callback
def async_register_services(hass):
    def bridge_for(call):
        coordinator = hass.data.get(DOMAIN, {}).get(call.data["config_entry_id"])
        if coordinator is None or coordinator.bridge is None:
            raise ServiceValidationError("This account has no Shopping List binding")
        if coordinator.bridge._storage_failed:
            raise ServiceValidationError(
                "Synchronization storage must be repaired first"
            )
        return coordinator.bridge

    async def details(call):
        coordinator = hass.data.get(DOMAIN, {}).get(call.data["config_entry_id"])
        if coordinator is None or not (coordinator.bridge or coordinator.keep_bridge):
            raise ServiceValidationError("This account has no Shopping List binding")
        if any(
            bridge is not None and bridge._storage_failed
            for bridge in (coordinator.bridge, coordinator.keep_bridge)
        ):
            raise ServiceValidationError(
                "Synchronization storage must be repaired first"
            )
        result = coordinator.bridge.details() if coordinator.bridge else {}
        if coordinator.keep_bridge is not None:
            result["google_keep"] = coordinator.keep_bridge.details()
        return result

    async def conflict(call):
        await bridge_for(call).async_resolve_conflict(
            call.data["conflict_id"], call.data["choose"]
        )

    async def write(call):
        await bridge_for(call).async_resolve_write(
            call.data["operation_id"],
            call.data["outcome"],
            call.data.get("target_item_id"),
        )

    async def keep_write(call):
        coordinator = hass.data.get(DOMAIN, {}).get(call.data["config_entry_id"])
        if coordinator is None or coordinator.keep_bridge is None:
            raise ServiceValidationError("This account has no Google Keep binding")
        await coordinator.keep_bridge.async_resolve_write(
            call.data["operation_id"],
            call.data["outcome"],
            call.data.get("target_item_id"),
        )

    async def keep_conflict(call):
        coordinator = hass.data.get(DOMAIN, {}).get(call.data["config_entry_id"])
        if coordinator is None or coordinator.keep_bridge is None:
            raise ServiceValidationError("This account has no Google Keep binding")
        await coordinator.keep_bridge.async_resolve_conflict(
            call.data["conflict_id"], call.data["choose"]
        )

    def coordinator_for(call):
        coordinator = hass.data.get(DOMAIN, {}).get(call.data["config_entry_id"])
        if coordinator is None:
            raise ServiceValidationError("This account is not loaded")
        return coordinator

    def require_bridge(coordinator, name):
        bridge = getattr(coordinator, name)
        if bridge is None:
            provider = "Listonic" if name == "bridge" else "Google Keep"
            raise ServiceValidationError(f"This account has no {provider} binding")
        if bridge._storage_failed:
            raise ServiceValidationError(
                "Synchronization storage must be repaired first"
            )
        return bridge

    async def sync_listonic(coordinator):
        bridge = require_bridge(coordinator, "bridge")
        if coordinator.client is None:
            raise ServiceValidationError("This account has no Listonic connection")
        await coordinator.async_refresh()
        bridge.request_sync(force=True)
        await bridge.async_wait_idle()
        if coordinator.keep_bridge is not None:
            await coordinator.keep_bridge.async_wait_idle()

    async def sync_gkeep(coordinator):
        bridge = require_bridge(coordinator, "keep_bridge")
        bridge.request_sync(force=True)
        await bridge.async_wait_idle()
        if coordinator.bridge is not None:
            await coordinator.bridge.async_wait_idle()

    async def sync_all(call):
        coordinator = coordinator_for(call)
        listonic_bridge = coordinator.bridge
        keep_bridge = coordinator.keep_bridge
        if listonic_bridge is None and keep_bridge is None:
            raise ServiceValidationError("This account has no Shopping List binding")
        if listonic_bridge is not None:
            require_bridge(coordinator, "bridge")
        if keep_bridge is not None:
            require_bridge(coordinator, "keep_bridge")
        if listonic_bridge is not None:
            await sync_listonic(coordinator)
        if keep_bridge is not None:
            await sync_gkeep(coordinator)
        if listonic_bridge is not None:
            listonic_bridge.request_sync(force=True)
            await listonic_bridge.async_wait_idle()
        if keep_bridge is not None:
            await keep_bridge.async_wait_idle()

    async def sync_listonic_service(call):
        await sync_listonic(coordinator_for(call))

    async def sync_gkeep_service(call):
        await sync_gkeep(coordinator_for(call))

    account = {vol.Required("config_entry_id"): str}
    if not hass.services.has_service(DOMAIN, SERVICES[0]):
        hass.services.async_register(
            DOMAIN,
            SERVICES[0],
            details,
            schema=vol.Schema(account),
            supports_response=SupportsResponse.ONLY,
        )
        hass.services.async_register(
            DOMAIN,
            SERVICES[1],
            conflict,
            schema=vol.Schema(
                {
                    **account,
                    vol.Required("conflict_id"): str,
                    vol.Required("choose"): vol.In(("home_assistant", "listonic")),
                }
            ),
        )
        hass.services.async_register(
            DOMAIN,
            SERVICES[2],
            write,
            schema=vol.Schema(
                {
                    **account,
                    vol.Required("operation_id"): str,
                    vol.Required("outcome"): vol.In(("applied", "not_applied")),
                    vol.Optional("target_item_id"): str,
                }
            ),
        )
        hass.services.async_register(
            DOMAIN,
            SERVICES[3],
            keep_write,
            schema=vol.Schema(
                {
                    **account,
                    vol.Required("operation_id"): str,
                    vol.Required("outcome"): vol.In(("applied", "not_applied")),
                    vol.Optional("target_item_id"): str,
                }
            ),
        )
        hass.services.async_register(
            DOMAIN,
            SERVICES[4],
            keep_conflict,
            schema=vol.Schema(
                {
                    **account,
                    vol.Required("conflict_id"): str,
                    vol.Required("choose"): vol.In(("home_assistant", "google_keep")),
                }
            ),
        )
        for service, handler in (
            ("sync_all", sync_all),
            ("sync_listonic", sync_listonic_service),
            ("sync_gkeep", sync_gkeep_service),
        ):
            hass.services.async_register(
                DOMAIN, service, handler, schema=vol.Schema(account)
            )


@callback
def async_remove_services(hass):
    for service in SERVICES:
        hass.services.async_remove(DOMAIN, service)
