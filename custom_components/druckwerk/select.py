"""Print file select."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import callback
from homeassistant.helpers.restore_state import RestoreEntity

from .entity import SenderEntity
from .platform_helper import add_per_printer


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    add_per_printer(entry, async_add_entities, lambda c, s: [PrintFileSelect(c, s)])


class PrintFileSelect(SenderEntity, SelectEntity, RestoreEntity):
    def __init__(self, coordinator, serial) -> None:
        super().__init__(coordinator, serial, "print_file")
        self._attr_options = self._files

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last and last.state in self.coordinator.data.files:
            self.pstate.file = last.state

    # Capability attributes are cached by HA, so refresh _attr_options explicitly.
    @property
    def _files(self) -> list[str]:
        return list(self.coordinator.data.files)

    @callback
    def _handle_coordinator_update(self) -> None:
        self._attr_options = self._files
        super()._handle_coordinator_update()

    @property
    def current_option(self) -> str | None:
        # A selection whose file vanished is hidden but remembered if it returns.
        return self.pstate.file if self.pstate.file in self._files else None

    async def async_select_option(self, option: str) -> None:
        self.pstate.file = option
        self.async_write_ha_state()
