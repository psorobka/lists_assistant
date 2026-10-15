"""Native Home Assistant repair forms for grouped shopping-list conflicts."""

import html

import voluptuous as vol
from homeassistant.components.repairs import RepairsFlow
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector

from .api import ApiError
from .bridge_store import BridgeStorageError
from .conflicts import commit, policy_choices, preview
from .const import DOMAIN, settings
from .keep_api import GoogleKeepError


class ConflictRepairFlow(RepairsFlow):
    """Review a batch before durably queuing its decisions."""

    def __init__(self):
        self._preview = None
        self._choose = None

    def _bridge(self):
        coordinator = self.hass.data.get(DOMAIN, {}).get(self.data["entry_id"])
        if coordinator is None:
            raise HomeAssistantError("Integration is unavailable")
        bridge = (
            coordinator.bridge
            if self.data["source"] == "listonic"
            else coordinator.keep_bridge
        )
        if bridge is None:
            raise HomeAssistantError("Bridge is unavailable")
        return bridge

    async def async_step_init(self, user_input=None):
        return await self.async_step_choose()

    async def async_step_choose(self, user_input=None):
        errors = {}
        try:
            bridge = self._bridge()
        except HomeAssistantError:
            return self.async_abort(reason="unavailable")
        choices = [
            choice
            for choice in policy_choices(settings(bridge.entry))
            if choice != "manual"
        ]
        if user_input is not None:
            self._choose = user_input["choose"]
            try:
                self._preview = await preview(bridge, self._choose)
            except (HomeAssistantError, ApiError, GoogleKeepError):
                errors["base"] = "unavailable"
            else:
                if not self._preview["conflicts"]:
                    bridge.request_sync()
                    return self.async_create_entry(data={})
                return await self.async_step_confirm()
        return self.async_show_form(
            step_id="choose",
            errors=errors,
            data_schema=vol.Schema(
                {
                    vol.Required("choose"): selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=choices,
                            translation_key="conflict_policy",
                            mode=selector.SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )

    async def async_step_confirm(self, user_input=None):
        errors = {}
        if user_input is not None:
            if not user_input.get("confirm"):
                errors["base"] = "confirm_required"
            else:
                try:
                    changed = await commit(self._bridge(), self._choose, self._preview)
                except BridgeStorageError:
                    bridge = self._bridge()
                    bridge._storage_failed = True
                    bridge._set_status("storage_error")
                    return self.async_abort(reason="storage_error")
                except (HomeAssistantError, ApiError, GoogleKeepError):
                    errors["base"] = "unavailable"
                else:
                    if changed is None:
                        return self.async_create_entry(data={})
                    self._preview = changed
                    errors["base"] = "changed"
        lines = []
        source_name = "Listonic" if self.data["source"] == "listonic" else "Google Keep"
        for uid, conflict in self._preview["conflicts"].items():

            def describe(item):
                if item is None:
                    return "∅"
                name = html.escape(item["name"])
                for character in "\\`*_{}[]()#+!|":
                    name = name.replace(character, "\\" + character)
                return f"{'[x]' if item['checked'] else '[ ]'} {name}"

            lines.append(
                f"- HA: {describe(conflict['local'])}; "
                f"{source_name}: {describe(conflict['remote'])} "
                f"→ {describe(self._preview['chosen'][uid])}"
            )
        return self.async_show_form(
            step_id="confirm",
            errors=errors,
            data_schema=vol.Schema({vol.Required("confirm", default=False): bool}),
            description_placeholders={
                "preview": "\n".join(lines),
                "count": str(len(lines)),
            },
        )


async def async_create_fix_flow(hass, issue_id, data):
    """The flow manager supplies the issue data after creating the flow."""
    return ConflictRepairFlow()
