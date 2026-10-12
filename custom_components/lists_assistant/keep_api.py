"""Google Keep client using the unofficial gkeepapi protocol."""

from __future__ import annotations

from functools import partial
from typing import Any

from homeassistant.core import HomeAssistant


class GoogleKeepError(Exception):
    """Google Keep request or authentication failed."""


async def async_exchange_master_token(
    hass: HomeAssistant, username: str, oauth_token: str, android_id: str
) -> str:
    """Exchange a browser OAuth cookie for a Keep master token in memory."""
    import gpsoauth

    try:
        response = await hass.async_add_executor_job(
            partial(gpsoauth.exchange_token, username, oauth_token, android_id)
        )
    except Exception as err:
        raise GoogleKeepError("Google Keep token exchange failed") from err
    token = response.get("Token") if isinstance(response, dict) else None
    if not isinstance(token, str) or not token:
        raise GoogleKeepError("Google Keep token exchange failed")
    return token


class GoogleKeepClient:
    """Small async wrapper around the blocking gkeepapi client."""

    def __init__(self, hass: HomeAssistant, username: str, token: str) -> None:
        import gkeepapi

        self.hass = hass
        self.username = username
        self.token = token
        self._gkeepapi = gkeepapi
        self.keep = gkeepapi.Keep()
        self.state: Any = None

    async def async_authenticate(self, state: str | None = None) -> Any:
        """Authenticate with a master token and restore cached state."""
        try:
            await self.hass.async_add_executor_job(
                partial(self.keep.authenticate, self.username, self.token, state)
            )
        except Exception as err:
            raise GoogleKeepError("Google Keep authentication failed") from err
        self.state = await self.hass.async_add_executor_job(self.keep.dump)
        return self.state

    async def async_lists(self) -> list[dict[str, Any]]:
        """Return active checkbox lists by stable Keep note ID."""
        try:
            notes = await self.hass.async_add_executor_job(self.keep.all)
        except Exception as err:
            raise GoogleKeepError("Could not read Google Keep lists") from err
        return [
            {"id": note.id, "title": note.title}
            for note in notes
            if isinstance(note, self._gkeepapi.node.List)
            and not note.deleted
            and not note.trashed
            and not note.archived
        ]

    async def async_refresh(self) -> Any:
        """Poll Keep for updates and return the updated client state."""
        try:
            await self.hass.async_add_executor_job(self.keep.sync)
            self.state = await self.hass.async_add_executor_job(self.keep.dump)
            return self.state
        except Exception as err:
            raise GoogleKeepError("Could not refresh Google Keep") from err

    async def async_items(self, list_id: str) -> dict[str, dict[str, Any]]:
        """Return the selected list's item state."""
        keep_list = self.keep.get(list_id)
        if not isinstance(keep_list, self._gkeepapi.node.List):
            raise GoogleKeepError("Selected Google Keep list is unavailable")
        return {
            str(item.id): {"name": item.text, "checked": bool(item.checked)}
            for item in keep_list.items
            if item.text
        }

    async def async_apply(
        self,
        list_id: str,
        action: str,
        item_id: str | None,
        payload: dict[str, Any] | None,
    ) -> str | None:
        """Apply one Keep mutation and return a created item's ID."""
        keep_list = self.keep.get(list_id)
        if not isinstance(keep_list, self._gkeepapi.node.List):
            raise GoogleKeepError("Selected Google Keep list is unavailable")
        try:
            created = None
            if action == "add":
                created = keep_list.add(payload["name"], payload["checked"])
            else:
                item = next(
                    (item for item in keep_list.items if item.id == item_id), None
                )
                if item is None:
                    raise GoogleKeepError("Google Keep item is unavailable")
                if action == "delete":
                    item.delete()
                else:
                    item.text = payload["name"]
                    item.checked = payload["checked"]
            await self.hass.async_add_executor_job(self.keep.sync)
            if created is not None:
                return str(created.id)
        except GoogleKeepError:
            raise
        except Exception as err:
            raise GoogleKeepError("Google Keep write failed") from err
        return None
