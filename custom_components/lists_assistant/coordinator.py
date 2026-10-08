"""Fetch configured Listonic lists and their products."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import ApiError, AuthError, ListonicClient
from .const import CONF_LISTS, DEFAULT_INTERVAL, DOMAIN, settings


class ListonicCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Keep selected remote lists fresh for their todo entities."""

    def __init__(self, hass: HomeAssistant, client: ListonicClient, entry: ConfigEntry):
        self.client = client
        self.entry = entry
        self.bridge = None
        self._operation_lock = asyncio.Lock()
        super().__init__(
            hass,
            logger=logging.getLogger(__name__),
            name=DOMAIN,
            config_entry=entry,
            update_interval=timedelta(seconds=DEFAULT_INTERVAL),
        )

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        """Fetch selected lists; API errors become coordinator failures."""
        self.logger.debug("Refreshing selected Listonic lists")
        async with self._operation_lock:
            return await self._fetch()

    async def _fetch(self) -> dict[str, dict[str, Any]]:
        """Read a full snapshot with the account's operation lock held."""
        try:
            lists = await self.client.lists()
        except AuthError as err:
            self.logger.error(
                "Listonic authentication expired; reauthentication is required"
            )
            raise ConfigEntryAuthFailed("Listonic session expired") from err
        except ApiError as err:
            self.logger.warning("Could not refresh Listonic lists: %s", err)
            raise UpdateFailed("Unable to retrieve Listonic lists") from err
        selected = settings(self.entry).get(CONF_LISTS, list(lists))
        result = {key: value for key, value in lists.items() if key in selected}
        self.logger.debug("Loaded %s selected Listonic lists", len(result))
        return result

    async def async_mutate(
        self,
        operation: Callable[[], Awaitable[Any]],
        *,
        refresh: bool = True,
    ) -> Any:
        """Serialize writes with polling and immediately publish their readback.

        A confirmed write stays successful even if the subsequent read fails.
        Reporting it as a failed write could encourage a duplicate POST. Local-only
        bridge operations can skip the unchanged Listonic readback.
        """
        async with self._operation_lock:
            try:
                result = await operation()
            except ApiError as err:
                self.logger.error("Listonic write failed: %s", err)
                self.async_set_update_error(UpdateFailed("Listonic write failed"))
                if isinstance(err, AuthError):
                    self.entry.async_start_reauth(self.hass)
                raise
            if refresh:
                try:
                    self.async_set_updated_data(await self._fetch())
                except (UpdateFailed, ConfigEntryAuthFailed) as err:
                    self.logger.warning(
                        "Listonic write succeeded, but its readback failed: %s", err
                    )
                    self.async_set_update_error(err)
                    if isinstance(err, ConfigEntryAuthFailed):
                        self.entry.async_start_reauth(self.hass)
            return result
