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
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import slugify

from .api import ApiError
from .const import CONF_KEEP_LISTS, CONF_LISTS, DOMAIN, settings
from .coordinator import ListonicCoordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: entity_platform.AddEntitiesCallback,
) -> None:
    """Create one HA todo entity per selected Listonic list."""
    coordinator: ListonicCoordinator = hass.data[DOMAIN][entry.entry_id]
    registry = er.async_get(hass)
    list_entities = [
        ListonicTodoList(coordinator, list_id)
        for list_id in settings(entry).get(CONF_LISTS, coordinator.data)
    ]
    for entity in list_entities:
        old_entity_id = registry.async_get_entity_id("todo", DOMAIN, entity.unique_id)
        old_registry_entry = (
            registry.async_get(old_entity_id) if old_entity_id else None
        )
        generated_id = (
            f"todo.{slugify(old_registry_entry.original_name or entity.name)}"
            if old_registry_entry is not None
            else None
        )
        suggested_id = f"todo.{entity.suggested_object_id}"
        if (
            old_entity_id is not None
            and old_entity_id == generated_id
            and old_entity_id != suggested_id
            and registry.async_get(suggested_id) is None
        ):
            registry.async_update_entity(old_entity_id, new_entity_id=suggested_id)
    async_add_entities(list_entities)
    keep_client = getattr(coordinator, "keep_todo_client", None)
    if keep_client is not None:
        names = getattr(coordinator, "keep_todo_names", {})
        entities = []
        for list_id in settings(entry).get(CONF_KEEP_LISTS, []):
            entity = GoogleKeepTodoList(
                coordinator, keep_client, list_id, names.get(list_id)
            )
            entities.append(entity)

            # HA originally generated the entity ID from the opaque Keep ID.
            # Rename it only while it still has that exact generated ID; a
            # user-customized entity ID must remain untouched.
            old_entity_id = registry.async_get_entity_id(
                "todo", DOMAIN, entity.unique_id
            )
            generated_id = f"todo.{slugify(list_id)}"
            suggested_id = f"todo.{entity.suggested_object_id}"
            if (
                old_entity_id == generated_id
                and old_entity_id != suggested_id
                and registry.async_get(suggested_id) is None
            ):
                registry.async_update_entity(old_entity_id, new_entity_id=suggested_id)
        async_add_entities(entities)


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
        list_name = self.name
        self._attr_suggested_object_id = (
            f"lists_assistant_listonic_{slugify(list_name)}"
        )

    @property
    def suggested_object_id(self) -> str:
        """Return a provider-prefixed, readable entity ID for HA."""
        return self._attr_suggested_object_id

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


class GoogleKeepTodoList(TodoListEntity):
    """Expose a selected Google Keep checklist as a writable HA todo list."""

    _attr_has_entity_name = True
    _attr_should_poll = True
    _attr_supported_features = (
        TodoListEntityFeature.CREATE_TODO_ITEM
        | TodoListEntityFeature.DELETE_TODO_ITEM
        | TodoListEntityFeature.UPDATE_TODO_ITEM
    )

    def __init__(self, coordinator, client, list_id, title=None):
        self.coordinator = coordinator
        self.client = client
        self.list_id = list_id
        self.items = {}
        self.title = title or list_id
        self._attr_unique_id = f"{coordinator.entry.unique_id}:keep:{list_id}"
        self._attr_suggested_object_id = f"lists_assistant_gkeep_{slugify(self.title)}"

    @property
    def suggested_object_id(self):
        """Return a provider-prefixed, readable entity ID for HA."""
        return self._attr_suggested_object_id

    @property
    def name(self):
        return self.title

    @property
    def available(self):
        return self.client.state is not None

    @property
    def extra_state_attributes(self):
        return {"list_id": self.list_id, "provider": "google_keep"}

    @property
    def todo_items(self):
        return [
            TodoItem(
                uid=item_id,
                summary=value["name"],
                status=(
                    TodoItemStatus.COMPLETED
                    if value["checked"]
                    else TodoItemStatus.NEEDS_ACTION
                ),
            )
            for item_id, value in self.items.items()
        ]

    async def async_update(self):
        """Refresh Keep data while sharing the account mutation lock."""

        async def refresh():
            if self.client.state is None:
                self.client.state = await self.client.async_authenticate()
            else:
                self.client.state = await self.client.async_refresh()
            self.items = await self.client.async_items(self.list_id)
            lists = await self.client.async_lists()
            self.title = next(
                (
                    item["title"] or self.list_id
                    for item in lists
                    if item["id"] == self.list_id
                ),
                self.list_id,
            )

        try:
            await self.coordinator.async_mutate(refresh, refresh=False)
        except Exception as err:
            self.client.state = None
            raise HomeAssistantError("Could not refresh Google Keep todo list") from err

    async def async_create_todo_item(self, item):
        try:
            await self.coordinator.async_mutate(
                lambda: self.client.async_apply(
                    self.list_id,
                    "add",
                    None,
                    {
                        "name": item.summary,
                        "checked": item.status == TodoItemStatus.COMPLETED,
                    },
                ),
                refresh=False,
            )
            await self.async_update()
        except Exception as err:
            raise HomeAssistantError("Could not add Google Keep item") from err

    async def async_update_todo_item(self, item):
        try:
            uid = str(item.uid)
            existing = self.items.get(uid)
            if existing is None:
                raise HomeAssistantError("Google Keep item is unavailable")
            name = item.summary if item.summary is not None else existing["name"]
            checked = (
                item.status == TodoItemStatus.COMPLETED
                if item.status is not None
                else existing["checked"]
            )
            await self.coordinator.async_mutate(
                lambda: self.client.async_apply(
                    self.list_id,
                    "update",
                    uid,
                    {"name": name, "checked": checked},
                ),
                refresh=False,
            )
            await self.async_update()
        except Exception as err:
            raise HomeAssistantError("Could not update Google Keep item") from err

    async def async_delete_todo_items(self, uids):
        try:
            for uid in uids:
                await self.coordinator.async_mutate(
                    lambda uid=uid: self.client.async_apply(
                        self.list_id, "delete", str(uid), None
                    ),
                    refresh=False,
                )
            await self.async_update()
        except Exception as err:
            raise HomeAssistantError("Could not delete Google Keep item") from err
