"""Preview of the plate the printer is printing."""

from __future__ import annotations

import os

from homeassistant.components.image import ImageEntity
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from . import api
from .externals import VENDOR_DOMAIN
from .entity import SenderEntity
from .platform_helper import add_per_printer


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    add_per_printer(entry, async_add_entities, lambda c, s: [CurrentPrintImage(c, s)])


class CurrentPrintImage(SenderEntity, ImageEntity, RestoreEntity):
    # The printer's own cover needs a preview in the file and a cloud task, so take it from the media file.

    _attr_content_type = "image/png"

    def __init__(self, coordinator, serial) -> None:
        SenderEntity.__init__(self, coordinator, serial, "current_print")
        ImageEntity.__init__(self, coordinator.hass)
        self._path: str | None = None
        self._png: bytes | None = None
        self._task_entity: str | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if self.pstate.last_path is None and last and last.attributes.get("file_path"):
            # A restart mid-print must not lose the preview.
            self.pstate.last_path = last.attributes["file_path"]
        self._task_entity = er.async_get(self.hass).async_get_entity_id(
            "sensor", VENDOR_DOMAIN, f"{self.serial}_subtask_name")
        if self._task_entity:
            self.async_on_remove(async_track_state_change_event(self.hass, [self._task_entity], self._task_changed))
        await self._load()

    @callback
    def _task_changed(self, _event) -> None:
        self.hass.async_create_task(self._load())

    def _wanted(self) -> str | None:
        """The file for the job the printer reports; the last sent file if it reports none."""
        last = self.pstate.last_path
        state = self.hass.states.get(self._task_entity) if self._task_entity else None
        task = state.state if state else None
        if task in (None, "", "unknown", "unavailable"):
            return last
        suffix = ".gcode.3mf"
        if last and os.path.basename(last).removesuffix(suffix) == task:
            return last
        # Prefer the top-level copy when the name exists in several folders.
        names = sorted(self.coordinator.data.files, key=lambda f: f.count("/"))
        match = next((f for f in names if os.path.basename(f).removesuffix(suffix) == task), None)
        return os.path.join(api.base_dir(self.hass), match) if match else None

    async def _load(self) -> None:
        path = self._wanted()
        if path == self._path:
            return
        self._path = path
        self._png = await self.hass.async_add_executor_job(api.plate_png, path) if path else None
        self._attr_image_last_updated = dt_util.utcnow() if self._png else None
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        if self._wanted() != self._path:
            self.hass.async_create_task(self._load())
        super()._handle_coordinator_update()

    @property
    def extra_state_attributes(self) -> dict:
        return {"file_path": self._path, "has_preview": self._png is not None}

    async def async_image(self) -> bytes | None:
        return self._png
