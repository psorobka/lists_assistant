"""Explicit inspection/resolution actions for synchronization problems."""

import voluptuous as vol
from homeassistant.core import SupportsResponse, callback
from homeassistant.exceptions import ServiceValidationError

from .const import DOMAIN

SERVICES = ("get_sync_issues", "resolve_sync_conflict", "resolve_sync_write")


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
        return bridge_for(call).details()

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


@callback
def async_remove_services(hass):
    for service in SERVICES:
        hass.services.async_remove(DOMAIN, service)
