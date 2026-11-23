"""Preview of the plate that was last sent to the printer."""

from __future__ import annotations

from homeassistant.components.image import ImageEntity
from homeassistant.core import callback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from . import api
from .entity import SenderEntity
from .platform_helper import add_per_printer


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    add_per_printer(entry, async_add_entities, lambda c, s: [CurrentPrintImage(c, s)])


class CurrentPrintImage(SenderEntity, ImageEntity, RestoreEntity):
    # The printer's own cover needs a preview in the file and a cloud task, so take it from the file we sent.

    _attr_content_type = "image/png"

    def __init__(self, coordinator, serial) -> None:
        SenderEntity.__init__(self, coordinator, serial, "current_print")
        ImageEntity.__init__(self, coordinator.hass)
        self._path: str | None = None
        self._png: bytes | None = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if self.pstate.last_path is None and last and last.attributes.get("file_path"):
            # A restart mid-print must not lose the preview.
            self.pstate.last_path = last.attributes["file_path"]
        await self._load()

    async def _load(self) -> None:
        path = self.pstate.last_path
        if path == self._path:
            return
        self._path = path
        self._png = await self.hass.async_add_executor_job(api.plate_png, path) if path else None
        self._attr_image_last_updated = dt_util.utcnow() if self._png else None
        self.async_write_ha_state()

    @callback
    def _handle_coordinator_update(self) -> None:
        if self.pstate.last_path != self._path:
            self.hass.async_create_task(self._load())
        super()._handle_coordinator_update()

    @property
    def extra_state_attributes(self) -> dict:
        return {"file_path": self._path, "has_preview": self._png is not None}

    async def async_image(self) -> bytes | None:
        return self._png
