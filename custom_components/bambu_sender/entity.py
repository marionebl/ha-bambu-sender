"""Shared entity base."""

from __future__ import annotations

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import BAMBU_LAB_DOMAIN
from .coordinator import PrinterState, SenderCoordinator


def _bambu_lab_device(hass, identifiers):
    """The device registered by ha-bambulab for these identifiers, if any."""
    for device in dr.async_get(hass).async_get_devices(identifiers=identifiers):
        entry = hass.config_entries.async_get_entry(device.config_entry_id)
        if entry and entry.domain == BAMBU_LAB_DOMAIN:
            return device
    return None


class SenderEntity(CoordinatorEntity[SenderCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: SenderCoordinator, serial: str, key: str) -> None:
        super().__init__(coordinator)
        self.serial = serial
        self._attr_translation_key = key
        self._attr_unique_id = f"{serial}_{key}"
        identifiers = {(BAMBU_LAB_DOMAIN, serial)}
        existing = _bambu_lab_device(coordinator.hass, identifiers)
        if existing:
            # Since HA 2026.10 a device belongs to one config entry, so identifiers
            # no longer merge across integrations; attach to the existing device itself.
            self.device_entry = existing
        else:
            printer = coordinator.data.printers.get(serial, {})
            self._attr_device_info = DeviceInfo(
                identifiers=identifiers,
                name=printer.get("name") or serial,
                manufacturer="Bambu Lab",
                model=printer.get("dev_product_name") or printer.get("dev_model_name"),
                serial_number=serial,
            )

    @property
    def printer(self) -> dict:
        return self.coordinator.data.printers.get(self.serial, {})

    @property
    def pstate(self) -> PrinterState:
        return self.coordinator.state_for(self.serial)

    @property
    def available(self) -> bool:
        return super().available and self.serial in self.coordinator.data.printers
