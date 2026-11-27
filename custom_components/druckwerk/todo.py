"""Queue as a todo list per printer: delete cancels, move reorders."""

from __future__ import annotations

from homeassistant.components.todo import (
    TodoItem,
    TodoItemStatus,
    TodoListEntity,
    TodoListEntityFeature,
)
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError

from .entity import SenderEntity
from .hub import WAITING
from .platform_helper import add_per_printer


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    hub = getattr(entry.runtime_data, "hub", None)
    if hub:
        add_per_printer(entry, async_add_entities, lambda c, s: [QueueTodo(c, s, hub)])


class QueueTodo(SenderEntity, TodoListEntity):
    # No CREATE: adding a job needs a sliced file.
    _attr_supported_features = (
        TodoListEntityFeature.DELETE_TODO_ITEM | TodoListEntityFeature.MOVE_TODO_ITEM
    )

    def __init__(self, coordinator, serial, hub) -> None:
        super().__init__(coordinator, serial, "queue")
        self.hub = hub

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.hub.async_add_listener(self.async_write_ha_state))

    @property
    def available(self) -> bool:
        return super().available and self.hub.last_update_success

    def _mine(self) -> list[dict]:
        return [
            j for j in (self.hub.data.jobs if self.hub.data else [])
            if j.get("state") in WAITING and (j.get("target") == self.serial or j.get("printer") == self.serial)
        ]

    @property
    def todo_items(self) -> list[TodoItem]:
        return [
            TodoItem(
                uid=j["id"],
                summary=str(j.get("file")),
                status=TodoItemStatus.NEEDS_ACTION,
                description=f"Owner: {j.get('owner_name') or 'unknown'}"
                + (" (needs approval)" if j.get("state") == "pending_approval" else ""),
            )
            for j in self._mine()
        ]

    async def async_delete_todo_items(self, uids: list[str]) -> None:
        for uid in uids:
            await self._call(f"/api/queue/{uid}/cancel", {})
        await self.hub.async_request_refresh()

    async def async_move_todo_item(self, uid: str, previous_uid: str | None) -> None:
        # position is 0-based among this printer's waiting jobs; scope names the printer.
        waiting = [j["id"] for j in self._mine() if j["id"] != uid]
        pos = 0 if previous_uid is None else waiting.index(previous_uid) + 1 if previous_uid in waiting else len(waiting)
        await self._call(f"/api/queue/{uid}/reorder", {"position": pos, "scope": self.serial})
        await self.hub.async_request_refresh()

    async def _call(self, path: str, body: dict) -> None:
        try:
            await self.hub.call("POST", path, body)
        except Exception as err:  # noqa: BLE001 - surface any hub failure to the user
            raise HomeAssistantError(f"Druckwerk: {err}") from err
