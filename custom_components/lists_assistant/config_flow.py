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
from .const import (
    CONF_BRIDGE,
    CONF_BRIDGE_CONFIRMED,
    CONF_INTERVAL,
    CONF_KEEP_ANDROID_ID,
    CONF_KEEP_ENABLED,
    CONF_KEEP_LIST_ID,
    CONF_KEEP_LISTS,
    CONF_KEEP_OAUTH_TOKEN,
    CONF_KEEP_TOKEN,
    CONF_KEEP_USERNAME,
    CONF_LISTS,
    CONF_SOURCES,
    DEFAULT_INTERVAL,
    DOMAIN,
    SOURCE_KEEP,
    SOURCE_LISTONIC,
    integration_title,
    settings,
)
from .keep_api import (
    GoogleKeepClient,
    GoogleKeepError,
    async_exchange_master_token,
)

CONF_EMAIL = "email"


def _interval_selector():
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=1,
            max=1440,
            step=1,
            mode=selector.NumberSelectorMode.BOX,
        )
    )


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
        self._sources = []
        self._keep_lists = []

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        return ListonicOptionsFlow()

    async def async_step_user(self, user_input: dict[str, Any] | None = None):
        """Choose which accounts to connect before requesting credentials."""
        if user_input is not None:
            self._sources = list(user_input.get(CONF_SOURCES, []))
            if not self._sources or any(
                source not in (SOURCE_LISTONIC, SOURCE_KEEP) for source in self._sources
            ):
                return self.async_show_form(
                    step_id="user",
                    data_schema=self._source_schema(),
                    errors={"base": "select_source"},
                )
            self._selection[CONF_SOURCES] = self._sources
            self._selection[CONF_INTERVAL] = user_input.get(
                CONF_INTERVAL, DEFAULT_INTERVAL
            )
            if SOURCE_LISTONIC in self._sources:
                return await self.async_step_listonic_auth()
            return await self.async_step_keep_auth()
        return self.async_show_form(step_id="user", data_schema=self._source_schema())

    @staticmethod
    def _source_schema():
        return vol.Schema(
            {
                vol.Required(CONF_SOURCES): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"value": SOURCE_LISTONIC, "label": "Listonic"},
                            {"value": SOURCE_KEEP, "label": "Google Keep"},
                        ],
                        multiple=True,
                        mode=selector.SelectSelectorMode.LIST,
                    )
                ),
                vol.Required(
                    CONF_INTERVAL, default=DEFAULT_INTERVAL
                ): _interval_selector(),
            }
        )

    async def async_step_listonic_auth(self, user_input: dict[str, Any] | None = None):
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
            step_id="reauth_confirm" if self.source == "reauth" else "listonic_auth",
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
        return await self.async_step_listonic_auth(user_input)

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
            if not selected and bridge == "none":
                return self.async_show_form(
                    step_id="lists",
                    data_schema=self._schema(options),
                    errors={"base": "select_list"},
                )
            if bridge != "none" and bridge_in_use(self.hass):
                return self.async_show_form(
                    step_id="lists",
                    data_schema=self._schema(options),
                    errors={"base": "bridge_in_use"},
                )
            self._selection = {
                CONF_SOURCES: self._sources,
                "username": self._username,
                "tokens": self._tokens,
                CONF_LISTS: selected,
                CONF_BRIDGE: bridge,
                CONF_INTERVAL: self._selection.get(CONF_INTERVAL, DEFAULT_INTERVAL),
            }
            if bridge != "none":
                return await self.async_step_bridge()
            if SOURCE_KEEP in self._sources:
                return await self.async_step_keep_auth()
            return self._finish(self._selection)
        return self.async_show_form(step_id="lists", data_schema=self._schema(options))

    async def async_step_keep_auth(self, user_input=None):
        """Authenticate Google Keep and load its checkbox lists."""
        if user_input is not None:
            username = user_input.get(CONF_KEEP_USERNAME, "").strip()
            try:
                if not username:
                    raise GoogleKeepError("Google account email is required")
                token = await async_exchange_master_token(
                    self.hass,
                    username,
                    user_input[CONF_KEEP_OAUTH_TOKEN].strip(),
                    user_input[CONF_KEEP_ANDROID_ID].strip(),
                )
                self._keep_client = GoogleKeepClient(self.hass, username, token)
                self._keep_client.state = await self._keep_client.async_authenticate()
                self._keep_lists = await self._keep_client.async_lists()
                if not self._keep_lists:
                    return self.async_abort(reason="keep_no_lists")
                self._selection.update(
                    {
                        CONF_KEEP_ENABLED: True,
                        CONF_KEEP_USERNAME: username,
                        CONF_KEEP_TOKEN: token,
                    }
                )
                if SOURCE_LISTONIC not in self._sources:
                    await self.async_set_unique_id(f"keep:{username.casefold()}")
                    self._abort_if_unique_id_configured()
                return await self.async_step_keep_list()
            except (GoogleKeepError, KeyError):
                return self.async_show_form(
                    step_id="keep_auth",
                    data_schema=self._keep_auth_schema(),
                    errors={"base": "keep_auth_failed"},
                )
        return self.async_show_form(
            step_id="keep_auth", data_schema=self._keep_auth_schema()
        )

    @staticmethod
    def _keep_auth_schema():
        return vol.Schema(
            {
                vol.Required(CONF_KEEP_USERNAME): str,
                vol.Required(CONF_KEEP_ANDROID_ID): str,
                vol.Required(
                    CONF_KEEP_OAUTH_TOKEN
                ): ListonicOptionsFlow._token_selector(),
            }
        )

    async def async_step_keep_list(self, user_input=None):
        """Select Keep todo entities and one optional Shopping List source."""
        options = {
            item["id"]: item.get("title") or item["id"] for item in self._keep_lists
        }
        choices = [{"value": key, "label": label} for key, label in options.items()]
        schema = vol.Schema(
            {
                vol.Required(CONF_KEEP_LISTS): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=choices,
                        multiple=True,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_KEEP_LIST_ID, default="none"
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[{"value": "none", "label": "No bridge"}, *choices],
                        translation_key="shopping_list",
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
            }
        )
        if user_input is not None:
            selected = list(user_input.get(CONF_KEEP_LISTS, []))
            bridge = user_input.get(CONF_KEEP_LIST_ID, "none")
            if any(item_id not in options for item_id in selected) or (
                bridge != "none" and bridge not in options
            ):
                return self.async_show_form(
                    step_id="keep_list",
                    data_schema=schema,
                    errors={"base": "invalid_selection"},
                )
            if not selected and bridge == "none":
                return self.async_show_form(
                    step_id="keep_list",
                    data_schema=schema,
                    errors={"base": "select_keep_list"},
                )
            if bridge != "none" and bridge_in_use(self.hass):
                return self.async_show_form(
                    step_id="keep_list",
                    data_schema=schema,
                    errors={"base": "bridge_in_use"},
                )
            self._selection[CONF_KEEP_LISTS] = selected
            self._selection[CONF_KEEP_LIST_ID] = bridge
            if bridge != "none":
                return await self.async_step_keep_confirm()
            return self._finish(self._selection)
        return self.async_show_form(step_id="keep_list", data_schema=schema)

    async def async_step_keep_confirm(self, user_input=None):
        """Confirm retaining all items during the first Keep bridge merge."""
        native = shopping_list_entity(self.hass)
        if native is None:
            return self.async_abort(reason="shopping_list_missing")
        try:
            response = await self.hass.services.async_call(
                "todo",
                "get_items",
                {"entity_id": native[0]},
                blocking=True,
                return_response=True,
            )
            keep_items = await self._keep_client.async_items(
                self._selection[CONF_KEEP_LIST_ID]
            )
        except (HomeAssistantError, GoogleKeepError):
            return self.async_abort(reason="cannot_connect")
        keep_name = next(
            (
                item.get("title") or item["id"]
                for item in self._keep_lists
                if item["id"] == self._selection[CONF_KEEP_LIST_ID]
            ),
            self._selection[CONF_KEEP_LIST_ID],
        )
        placeholders = {
            "local_count": str(len(response[native[0]]["items"])),
            "keep_count": str(len(keep_items)),
            "keep_list_name": keep_name,
        }
        schema = vol.Schema({vol.Required("confirm", default=False): bool})
        if user_input is not None:
            if not user_input.get("confirm"):
                return self.async_show_form(
                    step_id="keep_confirm",
                    data_schema=schema,
                    description_placeholders=placeholders,
                    errors={"base": "confirm_merge"},
                )
            return self._finish(self._selection)
        return self.async_show_form(
            step_id="keep_confirm",
            data_schema=schema,
            description_placeholders=placeholders,
        )

    def _finish(self, data):
        return self.async_create_entry(
            title=integration_title(data.get(CONF_SOURCES, self._sources)), data=data
        )

    async def async_step_bridge(self, user_input=None):
        if bridge_in_use(self.hass):
            return self.async_abort(reason="bridge_in_use")
        result = await confirm_merge(
            self, self._selection, self._lists, "bridge", user_input, lambda data: data
        )
        if result is self._selection:
            if SOURCE_KEEP in self._sources:
                return await self.async_step_keep_auth()
            return self._finish(self._selection)
        return result

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
        self._keep_client = None
        self._keep_lists = []

    async def async_step_init(self, user_input=None):
        if not self.config_entry.data.get("tokens"):
            return await self.async_step_keep_only(user_input)
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
        current = settings(self.config_entry)
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
            if (
                not errors
                and bridge != "none"
                and bridge_in_use(self.hass, self.config_entry.entry_id)
            ):
                errors["base"] = "bridge_in_use"
            if not errors:
                keep_enabled = user_input.get(CONF_KEEP_ENABLED, False)
                if keep_enabled and bridge == "none":
                    errors["base"] = "keep_requires_bridge"
                else:
                    self._selection = {
                        CONF_LISTS: selected,
                        CONF_BRIDGE: bridge,
                        CONF_KEEP_ENABLED: keep_enabled,
                        CONF_INTERVAL: user_input.get(
                            CONF_INTERVAL,
                            current.get(CONF_INTERVAL, DEFAULT_INTERVAL),
                        ),
                    }
                    if keep_enabled:
                        return await self.async_step_keep_auth()
                    if bridge != "none":
                        return await self.async_step_bridge()
                    return self._finish(self._selection)
        schema = ListonicConfigFlow._schema(options)
        schema = vol.Schema(
            {
                **schema.schema,
                vol.Optional(CONF_KEEP_ENABLED, default=False): bool,
                vol.Required(
                    CONF_INTERVAL,
                    default=current.get(CONF_INTERVAL, DEFAULT_INTERVAL),
                ): _interval_selector(),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(schema, current),
            errors=errors,
        )

    async def async_step_keep_only(self, user_input=None):
        """Edit selected Keep todo lists and its optional Shopping List bridge."""
        current = settings(self.config_entry)
        if not self._keep_lists:
            self._keep_client = GoogleKeepClient(
                self.hass,
                current[CONF_KEEP_USERNAME],
                current[CONF_KEEP_TOKEN],
            )
            try:
                self._keep_client.state = await self._keep_client.async_authenticate()
                self._keep_lists = await self._keep_client.async_lists()
            except GoogleKeepError:
                return self.async_abort(reason="keep_auth_failed")
        options = {
            item["id"]: item.get("title") or item["id"] for item in self._keep_lists
        }
        if not options:
            return self.async_abort(reason="keep_no_lists")
        errors = {}
        if user_input is not None:
            selected = list(user_input.get(CONF_KEEP_LISTS, []))
            bridge = user_input.get(CONF_KEEP_LIST_ID, "none")
            if any(item_id not in options for item_id in selected) or (
                bridge != "none" and bridge not in options
            ):
                errors["base"] = "invalid_selection"
            elif not selected and bridge == "none":
                errors["base"] = "select_keep_list"
            elif bridge != "none" and bridge_in_use(
                self.hass, self.config_entry.entry_id
            ):
                errors["base"] = "bridge_in_use"
            else:
                self._selection = {
                    CONF_KEEP_ENABLED: True,
                    CONF_KEEP_LISTS: selected,
                    CONF_KEEP_LIST_ID: bridge,
                    CONF_INTERVAL: user_input.get(
                        CONF_INTERVAL, current.get(CONF_INTERVAL, DEFAULT_INTERVAL)
                    ),
                }
                old_bridge = current.get(CONF_KEEP_LIST_ID, "none")
                if bridge != "none" and bridge != old_bridge:
                    return await self.async_step_keep_only_confirm()
                return self._finish(self._selection)
        choices = [{"value": key, "label": label} for key, label in options.items()]
        schema = vol.Schema(
            {
                vol.Required(CONF_KEEP_LISTS): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=choices,
                        multiple=True,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_KEEP_LIST_ID, default="none"
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[{"value": "none", "label": "No bridge"}, *choices],
                        translation_key="shopping_list",
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(
                    CONF_INTERVAL,
                    default=current.get(CONF_INTERVAL, DEFAULT_INTERVAL),
                ): _interval_selector(),
            }
        )
        return self.async_show_form(
            step_id="keep_only",
            data_schema=self.add_suggested_values_to_schema(schema, current),
            errors=errors,
        )

    async def async_step_keep_only_confirm(self, user_input=None):
        """Confirm a newly selected Keep-to-Shopping-List merge."""
        native = shopping_list_entity(self.hass)
        if native is None:
            return self.async_abort(reason="shopping_list_missing")
        bridge = self._selection[CONF_KEEP_LIST_ID]
        try:
            response = await self.hass.services.async_call(
                "todo",
                "get_items",
                {"entity_id": native[0]},
                blocking=True,
                return_response=True,
            )
            keep_items = await self._keep_client.async_items(bridge)
        except (HomeAssistantError, GoogleKeepError):
            return self.async_abort(reason="cannot_connect")
        list_name = next(
            (
                item.get("title") or bridge
                for item in self._keep_lists
                if item["id"] == bridge
            ),
            bridge,
        )
        placeholders = {
            "local_count": str(len(response[native[0]]["items"])),
            "keep_count": str(len(keep_items)),
            "keep_list_name": list_name,
        }
        schema = vol.Schema({vol.Required("confirm", default=False): bool})
        if user_input is not None:
            if user_input.get("confirm"):
                return self._finish(self._selection)
            return self.async_show_form(
                step_id="keep_only_confirm",
                data_schema=schema,
                description_placeholders=placeholders,
                errors={"base": "confirm_merge"},
            )
        return self.async_show_form(
            step_id="keep_only_confirm",
            data_schema=schema,
            description_placeholders=placeholders,
        )

    async def async_step_keep_auth(self, user_input=None):
        """Exchange a browser OAuth cookie and authenticate with Google Keep."""
        current = settings(self.config_entry)
        if user_input is None and current.get(CONF_KEEP_ENABLED):
            user_input = {
                CONF_KEEP_USERNAME: current.get(CONF_KEEP_USERNAME, ""),
                CONF_KEEP_TOKEN: current.get(CONF_KEEP_TOKEN, ""),
            }
            try:
                return await self._async_authenticate_keep(user_input)
            except GoogleKeepError:
                user_input = None
        if user_input is not None:
            try:
                return await self._async_authenticate_keep(user_input)
            except GoogleKeepError:
                return self.async_show_form(
                    step_id="keep_auth",
                    data_schema=vol.Schema(
                        {
                            vol.Required(CONF_KEEP_USERNAME): str,
                            vol.Required(CONF_KEEP_ANDROID_ID): str,
                            vol.Required(CONF_KEEP_OAUTH_TOKEN): self._token_selector(),
                        }
                    ),
                    errors={"base": "keep_auth_failed"},
                )
        return self.async_show_form(
            step_id="keep_auth",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_KEEP_USERNAME): str,
                    vol.Required(CONF_KEEP_ANDROID_ID): str,
                    vol.Required(CONF_KEEP_OAUTH_TOKEN): self._token_selector(),
                }
            ),
        )

    @staticmethod
    def _token_selector():
        return selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
        )

    async def _async_authenticate_keep(self, user_input):
        username = user_input[CONF_KEEP_USERNAME].strip()
        token = user_input.get(CONF_KEEP_TOKEN, "").strip()
        if not token:
            android_id = user_input.get(CONF_KEEP_ANDROID_ID, "").strip()
            oauth_token = user_input.get(CONF_KEEP_OAUTH_TOKEN, "").strip()
            if not username or not android_id or not oauth_token:
                raise GoogleKeepError("Google Keep OAuth details are required")
            token = await async_exchange_master_token(
                self.hass, username, oauth_token, android_id
            )
        if not username or not token:
            raise GoogleKeepError("Google Keep authentication details are required")
        self._keep_client = GoogleKeepClient(self.hass, username, token)
        self._keep_client.state = await self._keep_client.async_authenticate()
        self._keep_lists = await self._keep_client.async_lists()
        if not self._keep_lists:
            raise GoogleKeepError("No active Google Keep checkbox lists found")
        self._selection[CONF_KEEP_USERNAME] = username
        self._selection[CONF_KEEP_TOKEN] = token
        return await self.async_step_keep_list()

    async def async_step_keep_list(self, user_input=None):
        if user_input is not None:
            selected_id = user_input[CONF_KEEP_LIST_ID]
            if not any(item["id"] == selected_id for item in self._keep_lists):
                return self.async_show_form(
                    step_id="keep_list",
                    data_schema=self._keep_list_schema(),
                    errors={"base": "invalid_selection"},
                )
            self._selection[CONF_KEEP_LIST_ID] = selected_id
            return await self.async_step_keep_confirm()
        return self.async_show_form(
            step_id="keep_list", data_schema=self._keep_list_schema()
        )

    def _keep_list_schema(self):
        return vol.Schema(
            {
                vol.Required(CONF_KEEP_LIST_ID): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=[
                            {"value": item["id"], "label": item["title"] or item["id"]}
                            for item in self._keep_lists
                        ],
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )

    async def async_step_keep_confirm(self, user_input=None):
        native = shopping_list_entity(self.hass)
        if native is None:
            return self.async_abort(reason="shopping_list_missing")
        try:
            response = await self.hass.services.async_call(
                "todo",
                "get_items",
                {"entity_id": native[0]},
                blocking=True,
                return_response=True,
            )
            local_count = len(response[native[0]]["items"])
            keep_items = await self._keep_client.async_items(
                self._selection[CONF_KEEP_LIST_ID]
            )
        except (HomeAssistantError, GoogleKeepError):
            return self.async_abort(reason="cannot_connect")
        if user_input is not None:
            if not user_input.get("confirm"):
                return self.async_show_form(
                    step_id="keep_confirm",
                    data_schema=vol.Schema(
                        {vol.Required("confirm", default=False): bool}
                    ),
                    description_placeholders={
                        "local_count": str(local_count),
                        "keep_count": str(len(keep_items)),
                    },
                    errors={"base": "confirm_merge"},
                )
            if self._selection[CONF_BRIDGE] != "none":
                return await self.async_step_bridge()
            return self._finish(self._selection)
        return self.async_show_form(
            step_id="keep_confirm",
            data_schema=vol.Schema({vol.Required("confirm", default=False): bool}),
            description_placeholders={
                "local_count": str(local_count),
                "keep_count": str(len(keep_items)),
            },
        )

    def _finish(self, data):
        return self.async_create_entry(title="", data=data)

    async def async_step_bridge(self, user_input=None):
        if bridge_in_use(self.hass, self.config_entry.entry_id):
            return self.async_abort(reason="bridge_in_use")
        return await confirm_merge(
            self, self._selection, self._lists, "bridge", user_input, self._finish
        )
