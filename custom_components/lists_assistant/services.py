"""Account-scoped list and product actions using the serialized API client."""

import math

import voluptuous as vol
from homeassistant.core import SupportsResponse, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .api import AmbiguousWrite, ApiError, amount_text, identifier
from .const import DOMAIN

SERVICES = (
    "get_lists",
    "create_list",
    "rename_list",
    "delete_list",
    "add_item",
    "update_item",
    "delete_item",
)


def resource_id(value):
    """Accept numeric IDs, never names or request paths."""
    try:
        return identifier(value)
    except ApiError as err:
        raise vol.Invalid("Use a numeric Listonic ID") from err


def nonempty(value):
    if not isinstance(value, str) or not value.strip():
        raise vol.Invalid("A nonempty name is required")
    return value.strip()


def quantity(value):
    try:
        return amount_text(value)
    except ValueError as err:
        raise vol.Invalid("Quantity must be nonnegative or empty") from err


def price(value):
    try:
        if isinstance(value, bool):
            raise ValueError
        result = float(value)
        if not math.isfinite(result) or result < 0:
            raise ValueError
        return result
    except (TypeError, ValueError) as err:
        raise vol.Invalid("Price must be finite and nonnegative") from err


def created_id(created):
    """A successful POST with no usable ID must not encourage a second POST."""
    try:
        return identifier(created["Id"])
    except (ApiError, KeyError, TypeError) as err:
        raise AmbiguousWrite("Created resource ID is unknown") from err


@callback
def async_register_services(hass):
    """Register once; resolve the requested loaded account on each call."""
    if hass.services.has_service(DOMAIN, SERVICES[0]):
        return
    account = {vol.Required("config_entry_id"): str}
    target = {**account, vol.Required("list_id"): resource_id}
    product = {**target, vol.Required("item_id"): resource_id}
    fields = {
        vol.Optional("amount"): quantity,
        vol.Optional("unit"): str,
        vol.Optional("description"): str,
        vol.Optional("price"): price,
    }
    schemas = {
        "get_lists": account,
        "create_list": {**account, vol.Required("name"): nonempty},
        "rename_list": {**target, vol.Required("name"): nonempty},
        "delete_list": target,
        "add_item": {**target, vol.Required("name"): nonempty, **fields},
        "update_item": {
            **product,
            vol.Optional("name"): nonempty,
            vol.Optional("checked"): bool,
            **fields,
        },
        "delete_item": product,
    }

    async def handle(call):
        coordinator = hass.data.get(DOMAIN, {}).get(call.data["config_entry_id"])
        if coordinator is None:
            raise ServiceValidationError("Select a loaded Listonic account")
        action = call.service
        data = dict(call.data)
        data.pop("config_entry_id")
        if action == "update_item" and not set(data).difference({"list_id", "item_id"}):
            raise ServiceValidationError("Provide at least one field to update")

        async def operation():
            client = coordinator.client
            # Use a fresh account snapshot, including lists not selected as entities.
            # Names are never used to find a list/product, so duplicates are safe.
            if action != "create_list":
                snapshot = await client.lists()
                if action == "get_lists":
                    return {"lists": list(snapshot.values())}
                list_id = data.pop("list_id")
                if list_id not in snapshot:
                    raise ServiceValidationError(
                        "List is absent or inaccessible on this account"
                    )
                if "item_id" in data:
                    item_id = data.pop("item_id")
                    if not any(
                        str(item["Id"]) == item_id
                        for item in snapshot[list_id]["Items"]
                    ):
                        raise ServiceValidationError("Product is absent from this list")
            if action == "create_list":
                created = await client.create_list(data["name"])
                return {"list_id": created_id(created)}
            if action == "rename_list":
                await client.update_list(list_id, Name=data["name"])
            elif action == "delete_list":
                # Active:0 is the removal verified on the live API. Do not guess DELETE.
                await client.update_list(list_id, Active=0)
            elif action == "add_item":
                created = await client.add_item(list_id, **data)
                return {"item_id": created_id(created)}
            elif action == "update_item":
                if "checked" in data:
                    data["checked"] = int(data["checked"])
                await client.update_item(list_id, item_id, **data)
            elif action == "delete_item":
                await client.delete_item(list_id, item_id)

        try:
            return await coordinator.async_mutate(operation)
        except AmbiguousWrite as err:
            raise HomeAssistantError(
                "Listonic write result is unknown. "
                "Inspect get_lists before repeating the action."
            ) from err
        except ApiError as err:
            raise HomeAssistantError(
                "Listonic action failed; check connectivity and account access"
            ) from err

    for name, schema in schemas.items():
        response = SupportsResponse.NONE
        if name == "get_lists":
            response = SupportsResponse.ONLY
        elif name in ("create_list", "add_item"):
            response = SupportsResponse.OPTIONAL
        hass.services.async_register(
            DOMAIN, name, handle, schema=vol.Schema(schema), supports_response=response
        )


@callback
def async_remove_services(hass):
    for service in SERVICES:
        hass.services.async_remove(DOMAIN, service)
