"""Bambu Sender: start cloud prints on Bambu Lab printers through the add-on."""

from __future__ import annotations

import logging
import os
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall, SupportsResponse
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType

from . import api, hub_api
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    BAMBU_LAB_DOMAIN,
    CONF_HUB_SECRET,
    CONF_HUB_URL,
    CONF_URL,
    DEFAULT_URL,
    DOMAIN,
    HUB_PORT,
)
from .coordinator import SenderCoordinator
from .hub import HubCoordinator
from .upload import UploadView
from .watchdog import PrintWatchdog

_LOGGER = logging.getLogger(__name__)
PLATFORMS = ["select", "switch", "button", "sensor", "camera", "light", "image", "todo"]

PRINT_FILE_SCHEMA = vol.All(
    vol.Schema(
        {
            vol.Optional("device_id"): cv.string,
            vol.Optional("serial"): cv.string,
            vol.Required("file"): cv.string,
            vol.Optional("timelapse", default=False): cv.boolean,
            vol.Optional("bed_leveling", default=True): cv.boolean,
            vol.Optional("use_ams", default=True): cv.boolean,
            vol.Optional("force", default=False): cv.boolean,
        }
    ),
    cv.has_at_least_one_key("device_id", "serial"),
)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    hass.http.register_view(UploadView())

    async def _print_file(call: ServiceCall) -> dict[str, Any]:
        entries = hass.config_entries.async_loaded_entries(DOMAIN)
        if not entries:
            raise HomeAssistantError("Bambu Sender is not set up")
        coordinator: SenderCoordinator = entries[0].runtime_data
        data = call.data
        serial = data.get("serial") or _serial_from_device(hass, data["device_id"])
        path = await hass.async_add_executor_job(api.resolve_file, hass, data["file"])
        return await coordinator.async_print(
            serial,
            path,
            timelapse=data["timelapse"],
            bed_leveling=data["bed_leveling"],
            use_ams=data["use_ams"],
            force=data["force"],
        )

    hass.services.async_register(
        DOMAIN,
        "print_file",
        _print_file,
        schema=PRINT_FILE_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True


def _serial_from_device(hass: HomeAssistant, device_id: str) -> str:
    device = dr.async_get(hass).async_get(device_id)
    if device:
        for domain, serial in device.identifiers:
            if domain == BAMBU_LAB_DOMAIN:
                return serial
    raise ServiceValidationError(f"Device {device_id} is not a Bambu Lab printer")


async def async_migrate_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """v1 -> v2: an entry still pointing at the old app moves to the Hub app if one is found."""
    if entry.version == 1:
        data = dict(entry.data)
        if data.get(CONF_URL) == DEFAULT_URL:
            found = await hub_api.discover_bridge_url(async_get_clientsession(hass))
            if found and found != DEFAULT_URL:
                data[CONF_URL] = found
                data[CONF_HUB_URL] = found.rsplit(":", 1)[0] + f":{HUB_PORT}"
        # Entities keep their unique IDs: only the data and the version change.
        hass.config_entries.async_update_entry(entry, data=data, version=2)
    return True


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    try:
        await hass.async_add_executor_job(
            lambda: os.makedirs(api.base_dir(hass), exist_ok=True)
        )
    except OSError as err:
        _LOGGER.warning("Cannot create %s: %s", api.base_dir(hass), err)
    coordinator = SenderCoordinator(hass, entry)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator
    # coordinator.async_print calls coordinator.watchdog.started() for use_ams prints.
    coordinator.watchdog = PrintWatchdog(hass, coordinator)
    entry.async_on_unload(coordinator.watchdog.async_stop)
    coordinator.hub = None
    if entry.data.get(CONF_HUB_SECRET):
        hub = coordinator.hub = HubCoordinator(hass, entry)
        entry.async_on_unload(hub.async_add_listener(lambda: None))  # keeps the 5 s polling alive
        await hub.async_refresh()  # a hub that is down must not block the printers
        entry.async_on_unload(
            hass.bus.async_listen("mobile_app_notification_action", hub.on_action)
        )
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
