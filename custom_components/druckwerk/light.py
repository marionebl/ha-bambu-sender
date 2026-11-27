"""Chamber light through the add-on (the printer firmware rejects unsigned commands)."""

from __future__ import annotations

from homeassistant.components.light import ColorMode, LightEntity
from homeassistant.core import callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event

from .externals import VENDOR_DOMAIN
from .entity import SenderEntity
from .platform_helper import add_per_printer


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    add_per_printer(entry, async_add_entities, lambda c, s: [ChamberLight(c, s)])


class ChamberLight(SenderEntity, LightEntity):
    _attr_color_mode = ColorMode.ONOFF
    _attr_supported_color_modes = {ColorMode.ONOFF}

    def __init__(self, coordinator, serial) -> None:
        super().__init__(coordinator, serial, "chamber_light")
        self._mirror: str | None = None
        self._attr_is_on = None

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        # the vendor integration still reads the light state correctly; only its commands are blocked.
        reg = er.async_get(self.hass)
        device_id = self.device_entry.id if self.device_entry else None
        for ent in er.async_entries_for_device(reg, device_id) if device_id else []:
            if ent.platform == VENDOR_DOMAIN and ent.domain == "light" and ent.unique_id.endswith("chamber_light"):
                self._mirror = ent.entity_id
        if self._mirror:
            self._sync()
            self.async_on_remove(
                async_track_state_change_event(self.hass, [self._mirror], self._on_mirror_change)
            )

    @callback
    def _on_mirror_change(self, _event) -> None:
        self._sync(write=True)

    @callback
    def _sync(self, write: bool = False) -> None:
        state = self.hass.states.get(self._mirror)
        self._attr_is_on = None if state is None or state.state not in ("on", "off") else state.state == "on"
        if write:
            self.async_write_ha_state()

    async def async_turn_on(self, **kwargs) -> None:
        await self.coordinator.async_command(f"/api/light/{self.serial}", {"on": True})

    async def async_turn_off(self, **kwargs) -> None:
        await self.coordinator.async_command(f"/api/light/{self.serial}", {"on": False})
