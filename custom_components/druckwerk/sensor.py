"""Cloud sender result sensor."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity

from .entity import SenderEntity
from .platform_helper import add_per_printer


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    add_per_printer(entry, async_add_entities, lambda c, s: [CloudSenderSensor(c, s)])


class CloudSenderSensor(SenderEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["idle", "sent", "error"]

    def __init__(self, coordinator, serial) -> None:
        super().__init__(coordinator, serial, "cloud_sender")

    @property
    def native_value(self) -> str:
        return self.pstate.result

    @property
    def extra_state_attributes(self) -> dict:
        st = self.pstate
        return {
            "account_email": self.printer.get("email"),
            "last_file": st.last_file,
            "last_time": st.last_time,
            "last_error": st.last_error,
        }
