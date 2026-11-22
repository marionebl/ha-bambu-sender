"""Start cloud print and AMS slot re-read buttons."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.exceptions import HomeAssistantError

from . import api
from .const import AMS_SLOTS
from .entity import SenderEntity
from .platform_helper import add_per_printer


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    add_per_printer(
        entry,
        async_add_entities,
        lambda c, s: [StartPrintButton(c, s)] + [RereadSlotButton(c, s, n) for n in range(AMS_SLOTS)],
    )


class StartPrintButton(SenderEntity, ButtonEntity):
    def __init__(self, coordinator, serial) -> None:
        super().__init__(coordinator, serial, "start_print")

    async def async_press(self) -> None:
        st = self.pstate
        if not st.file:
            raise HomeAssistantError("Select a print file first")
        path = await self.hass.async_add_executor_job(
            api.resolve_file, self.hass, st.file
        )
        await self.coordinator.async_print(
            self.serial,
            path,
            timelapse=st.timelapse,
            bed_leveling=st.bed_leveling,
            use_ams=True,
        )


class RereadSlotButton(SenderEntity, ButtonEntity):
    """Makes the AMS read the spool's RFID tag again, without pulling the spool."""

    _attr_icon = "mdi:nfc-search-variant"

    def __init__(self, coordinator, serial, slot: int) -> None:
        super().__init__(coordinator, serial, "reread_slot")
        self.slot = slot
        self._attr_unique_id = f"{serial}_reread_slot_{slot + 1}"
        self._attr_translation_placeholders = {"slot": str(slot + 1)}

    async def async_press(self) -> None:
        await self.coordinator.async_command(
            f"/api/rfid/{self.serial}", {"ams_id": 0, "slot_id": self.slot}
        )
