"""Local option switches (no add-on state)."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import STATE_ON
from homeassistant.helpers.restore_state import RestoreEntity

from .entity import SenderEntity
from .platform_helper import add_per_printer


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    add_per_printer(
        entry,
        async_add_entities,
        lambda c, s: [
            OptionSwitch(c, s, "timelapse", False),
            OptionSwitch(c, s, "bed_leveling", True),
        ],
    )


class OptionSwitch(SenderEntity, SwitchEntity, RestoreEntity):
    def __init__(self, coordinator, serial, attr: str, default: bool) -> None:
        super().__init__(coordinator, serial, attr)
        self._opt = attr
        self._default = default

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        setattr(
            self.pstate,
            self._opt,
            last.state == STATE_ON if last and last.state in ("on", "off") else self._default,
        )

    @property
    def is_on(self) -> bool:
        return getattr(self.pstate, self._opt)

    async def async_turn_on(self, **kwargs) -> None:
        setattr(self.pstate, self._opt, True)
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        setattr(self.pstate, self._opt, False)
        self.async_write_ha_state()
