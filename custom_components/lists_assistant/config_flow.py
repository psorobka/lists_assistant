"""Config flow for Listonic accounts and shopping-list selection."""

from __future__ import annotations

import re
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import ApiError, AuthError, ListonicClient
from .bridge import shopping_list_entity
from .bridge_store import BridgeStorageError, BridgeStore
from .const import CONF_BRIDGE, CONF_BRIDGE_CONFIRMED, CONF_LISTS, DOMAIN, settings

CONF_EMAIL = "email"


def bridge_in_use(hass, exclude=None):
    return any(
        entry.entry_id != exclude and settings(entry).get(CONF_BRIDGE, "none") != "none"
        for entry in hass.config_entries.async_entries(DOMAIN)
    )


async def merge_preview(hass):
    """Read the public Shopping List snapshot without changing anything."""
    native = shopping_list_entity(hass)
    if native is None:
        return None
    try:
        response = await hass.services.async_call(
            "todo",
            "get_items",
            {"entity_id": native[0]},
            blocking=True,
            return_response=True,
        )
    except HomeAssistantError:
        return None
    return len(response[native[0]]["items"])


async def confirm_merge(flow, data, lists, step_id, user_input, finish):
    """Require explicit consent to retaining every record, including equal names."""
    list_id = data[CONF_BRIDGE]
    local_count = await merge_preview(flow.hass)
    held = set()
    uncertain = False
    if isinstance(flow, ListonicOptionsFlow):
        store = BridgeStore(flow.hass, flow.config_entry.entry_id)
        try:
            await store.load(list_id)
        except BridgeStorageError:
            return flow.async_abort(reason="storage_error")
        for old_id, state in store.data["bindings"].items():
            if old_id != list_id:
                held.update(
                    op["ha_id"] for op in state["pending"].values() if op["ha_id"]
                )
                held.update(state["conflicts"])
                uncertain |= any(
                    op["state"] != "queued" for op in state["pending"].values()
                )
    errors = {}
    if user_input is not None:
        if uncertain:
            errors["base"] = "resolve_before_switch"
        elif local_count is None:
            errors["base"] = "shopping_list_missing"
        elif not user_input.get("confirm"):
            errors["base"] = "confirm_merge"
        else:
            data[CONF_BRIDGE_CONFIRMED] = list_id
            return finish(data)
    return flow.async_show_form(
        step_id=step_id,
        data_schema=vol.Schema({vol.Required("confirm", default=False): bool}),
        description_placeholders={
            "list_name": str(lists[list_id].get("Name", list_id)),
            "local_count": str(local_count if local_count is not None else "—"),
            "remote_count": str(len(lists[list_id].get("Items", []))),
            "held_count": str(len(held)),
        },
        errors=errors,
    )


class ListonicConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Configure a Listonic account and choose its Home Assistant list."""

    VERSION = 1

    def __init__(self) -> None:
        self._tokens: dict[str, Any] = {}
        self._lists: dict[str, dict[str, Any]] = {}
        self._username = ""
        self._selection = {}

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return ListonicOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Authenticate to Listonic and load available lists."""
        errors: dict[str, str] = {}
        email = (user_input or {}).get(CONF_EMAIL, "")
        if self.source == "reauth" and not email:
            entry = self._get_reauth_entry()
            email = entry.data.get("username", entry.unique_id or "")
        if user_input is not None and not re.fullmatch(
            r"[^@\s]+@[^@\s]+\.[^@\s]+", email.strip()
        ):
            errors[CONF_EMAIL] = "invalid_email"
        elif user_input is not None:
            client = ListonicClient(async_get_clientsession(self.hass))
            try:
                await client.login(email.strip(), user_input[CONF_PASSWORD])
                profile = await client.profile()
                if self.source != "reauth":
                    self._lists = await client.lists()
            except AuthError:
                errors["base"] = "invalid_auth"
            except ApiError:
                errors["base"] = "cannot_connect"
            else:
                self._tokens = client.tokens
                self._username = str(profile["Username"])
                if self.source == "reauth":
                    entry = self._get_reauth_entry()
                    if self._username.casefold() != entry.unique_id:
                        errors["base"] = "wrong_account"
                    else:
                        return self.async_update_reload_and_abort(
                            entry, data_updates={"tokens": self._tokens}
                        )
                else:
                    await self.async_set_unique_id(self._username.casefold())
                    self._abort_if_unique_id_configured()
                    return await self.async_step_lists()
        return self.async_show_form(
            step_id="reauth_confirm" if self.source == "reauth" else "user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_EMAIL, default=email): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]):
        """Recover the existing account without changing lists or entity IDs."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """Request credentials for the same account."""
        return await self.async_step_user(user_input)

    async def async_step_lists(self, user_input: dict[str, Any] | None = None):
        """Select Listonic lists and the optional native HA shopping-list bridge."""
        options = {
            list_id: str(value.get("Name") or f"List {list_id}")
            for list_id, value in self._lists.items()
        }
        if not options:
            return self.async_abort(reason="no_lists")
        if user_input is not None:
            selected = list(user_input[CONF_LISTS])
            bridge = user_input.get(CONF_BRIDGE, "none")
            if any(key not in options for key in selected) or (
                bridge != "none" and bridge not in options
            ):
                return self.async_show_form(
                    step_id="lists",
                    data_schema=self._schema(options),
                    errors={"base": "invalid_selection"},
                )
            if not selected:
                if bridge == "none":
                    return self.async_show_form(
                        step_id="lists",
                        data_schema=self._schema(options),
                        errors={"base": "select_list"},
                    )
                selected.append(bridge)
            elif bridge != "none" and bridge not in selected:
                selected.append(bridge)
            if bridge != "none" and bridge_in_use(self.hass):
                return self.async_show_form(
                    step_id="lists",
                    data_schema=self._schema(options),
                    errors={"base": "bridge_in_use"},
                )
            self._selection = {
                "username": self._username,
                "tokens": self._tokens,
                CONF_LISTS: selected,
                CONF_BRIDGE: bridge,
            }
            if bridge != "none":
                return await self.async_step_bridge()
            return self._finish(self._selection)
        return self.async_show_form(step_id="lists", data_schema=self._schema(options))

    def _finish(self, data):
        return self.async_create_entry(title=f"Listonic ({self._username})", data=data)

    async def async_step_bridge(self, user_input=None):
        if bridge_in_use(self.hass):
            return self.async_abort(reason="bridge_in_use")
        return await confirm_merge(
            self, self._selection, self._lists, "bridge", user_input, self._finish
        )

    @staticmethod
    def _schema(options: dict[str, str]) -> vol.Schema:
        """Build the list picker schema."""
        return vol.Schema(
            {
                vol.Required(CONF_LISTS): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"value": key, "label": label}
                            for key, label in options.items()
                        ],
                        multiple=True,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(CONF_BRIDGE, default="none"): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"value": "none", "label": "No bridge"},
                            *[
                                {"value": key, "label": label}
                                for key, label in options.items()
                            ],
                        ],
                        translation_key="shopping_list",
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )


class ListonicOptionsFlow(config_entries.OptionsFlowWithReload):
    """Change selected lists; each target keeps its own durable queue."""

    def __init__(self):
        self._lists = None
        self._selection = {}

    async def async_step_init(self, user_input=None):
        if self._lists is None:
            coordinator = self.hass.data.get(DOMAIN, {}).get(self.config_entry.entry_id)

            async def save_tokens(tokens):
                self.hass.config_entries.async_update_entry(
                    self.config_entry,
                    data={**self.config_entry.data, "tokens": tokens},
                )

            client = (
                coordinator.client
                if coordinator
                else ListonicClient(
                    async_get_clientsession(self.hass),
                    self.config_entry.data["tokens"],
                    on_tokens=save_tokens,
                )
            )
            try:
                self._lists = await client.lists()
            except ApiError:
                return self.async_abort(reason="cannot_connect")
        options = {key: str(item.get("Name", key)) for key, item in self._lists.items()}
        if not options:
            return self.async_abort(reason="no_lists")
        errors = {}
        if user_input is not None:
            selected = list(user_input[CONF_LISTS])
            bridge = user_input.get(CONF_BRIDGE, "none")
            if any(key not in options for key in selected) or (
                bridge != "none" and bridge not in options
            ):
                errors["base"] = "invalid_selection"
            elif not selected and bridge == "none":
                errors["base"] = "select_list"
            elif bridge != "none" and bridge in options and bridge not in selected:
                selected.append(bridge)
            if (
                not errors
                and bridge != "none"
                and bridge_in_use(self.hass, self.config_entry.entry_id)
            ):
                errors["base"] = "bridge_in_use"
            if not errors:
                self._selection = {CONF_LISTS: selected, CONF_BRIDGE: bridge}
                if bridge != "none":
                    return await self.async_step_bridge()
                return self._finish(self._selection)
        schema = ListonicConfigFlow._schema(options)
        current = settings(self.config_entry)
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(schema, current),
            errors=errors,
        )

    def _finish(self, data):
        return self.async_create_entry(title="", data=data)

    async def async_step_bridge(self, user_input=None):
        if bridge_in_use(self.hass, self.config_entry.entry_id):
            return self.async_abort(reason="bridge_in_use")
        return await confirm_merge(
            self, self._selection, self._lists, "bridge", user_input, self._finish
        )
