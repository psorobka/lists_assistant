"""Todo entities backed by Listonic shopping lists."""

from __future__ import annotations

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_platform
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import ApiError
from .const import CONF_LISTS, DOMAIN, settings
from .coordinator import ListonicCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: entity_platform.AddEntitiesCallback,
) -> None:
    """Create one HA todo entity per selected Listonic list."""
    coordinator: ListonicCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        ListonicTodoList(coordinator, list_id)
        for list_id in settings(entry).get(CONF_LISTS, coordinator.data)
    )


class ListonicTodoList(CoordinatorEntity[ListonicCoordinator], TodoListEntity):
    """Expose a remote Listonic list as a native Home Assistant todo list."""

    _attr_has_entity_name = True
    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
        | TodoListEntityFeature.SET_DESCRIPTION_ON_ITEM
    )

    def __init__(self, coordinator: ListonicCoordinator, list_id: str) -> None:
        super().__init__(coordinator)
        self.list_id = list_id
        account_id = coordinator.entry.unique_id or coordinator.entry.entry_id
        self._attr_unique_id = f"{account_id}:{list_id}"

    @property
    def name(self) -> str:
        """Follow remote renames while retaining entity identity."""
        return self.coordinator.data.get(self.list_id, {}).get(
            "Name", f"List {self.list_id}"
        )

    @property
    def available(self) -> bool:
        """A missing remote list is unavailable rather than an empty list."""
        return super().available and self.list_id in self.coordinator.data

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        """Expose the stable remote ID for automations."""
        return {"list_id": self.list_id}

    @property
    def todo_items(self) -> list[TodoItem] | None:
        """Return Listonic products in Home Assistant's todo format."""
        data = self.coordinator.data.get(self.list_id)
        if data is None:
            return None
        return [
            TodoItem(
                uid=str(item["Id"]),
                summary=item.get("Name", ""),
                description=item.get("Description") or None,
                status=(
                    TodoItemStatus.COMPLETED
                    if item.get("Checked")
                    else TodoItemStatus.NEEDS_ACTION
                ),
            )
            for item in data.get("Items", [])
        ]

    async def async_create_todo_item(self, item: TodoItem) -> None:
        """Add a product to the remote list."""
        try:
            fields = {}
            if item.description is not None:
                fields["description"] = item.description
            await self.coordinator.async_mutate(
                lambda: self.coordinator.client.add_item(
                    self.list_id, item.summary, **fields
                )
            )
        except ApiError as err:
            raise HomeAssistantError("Could not add Listonic item") from err

    async def async_update_todo_item(self, item: TodoItem) -> None:
        """Rename or mark a remote product complete."""
        uid = str(item.uid)

        async def update() -> None:
            existing = next(
                (
                    value
                    for value in self.coordinator.data.get(self.list_id, {}).get(
                        "Items", []
                    )
                    if str(value["Id"]) == uid
                ),
                None,
            )
            if existing is None:
                raise ApiError("Listonic item no longer exists")
            fields = {}
            if item.summary is not None and existing.get("Name") != item.summary:
                fields["name"] = item.summary
            if item.status is not None:
                checked = item.status == TodoItemStatus.COMPLETED
                if bool(existing.get("Checked")) != checked:
                    fields["checked"] = int(checked)
            if (existing.get("Description") or "") != (item.description or ""):
                fields["description"] = item.description or ""
            if fields:
                await self.coordinator.client.update_item(self.list_id, uid, **fields)

        try:
            await self.coordinator.async_mutate(update)
        except ApiError as err:
            raise HomeAssistantError("Could not update Listonic item") from err

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        """Delete products from the remote list."""

        async def delete() -> None:
            for uid in uids:
                await self.coordinator.client.delete_item(self.list_id, uid)

        try:
            await self.coordinator.async_mutate(delete)
        except ApiError as err:
            raise HomeAssistantError("Could not delete Listonic item") from err
