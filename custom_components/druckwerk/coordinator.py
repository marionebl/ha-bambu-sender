"""Polling coordinator and the shared send logic."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from . import api
from .gcode_guard import check_gcode_3mf
from .const import (
    BUSY_STATUSES,
    CONF_URL,
    DOMAIN,
    OK_RESULTS,
    SCAN_INTERVAL,
    SEND_TIMEOUT,
)

_LOGGER = logging.getLogger(__name__)


@dataclass
class PrinterState:
    """Per printer state shared between the entities and the send logic."""

    file: str | None = None
    timelapse: bool = False
    bed_leveling: bool = True
    result: str = "idle"  # idle / sent / error
    last_file: str | None = None
    last_path: str | None = None  # file of the last successful send
    last_time: str | None = None
    last_error: str | None = None


@dataclass
class SenderData:
    printers: dict[str, dict[str, Any]] = field(default_factory=dict)
    health: dict[str, Any] = field(default_factory=dict)
    files: list[str] = field(default_factory=list)


def _error_text(status: int, body: Any) -> str:
    if isinstance(body, dict):
        for key in ("error", "message", "detail"):
            if body.get(key):
                return str(body[key])
    return f"Add-on answered HTTP {status}: {body}"


class SenderCoordinator(DataUpdateCoordinator[SenderData]):
    """Polls the add-on and rescans the media folder."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=SCAN_INTERVAL
        )
        self.url: str = entry.data[CONF_URL]
        self.states: dict[str, PrinterState] = {}

    def state_for(self, serial: str) -> PrinterState:
        return self.states.setdefault(serial, PrinterState())

    async def _async_update_data(self) -> SenderData:
        session = async_get_clientsession(self.hass)
        try:
            printers = await api.async_get_json(session, self.url, "/api/printers")
            health = await api.async_get_json(session, self.url, "/api/health")
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise UpdateFailed(f"Druckwerk bridge not reachable: {err}") from err
        if not isinstance(printers, list):
            raise UpdateFailed("Unexpected /api/printers answer")
        files = await self.hass.async_add_executor_job(
            api.scan_files, api.base_dir(self.hass)
        )
        return SenderData(
            printers={p["serial"]: p for p in printers if p.get("serial")},
            health=health if isinstance(health, dict) else {},
            files=files,
        )

    async def async_print(
        self,
        serial: str,
        path: str,
        *,
        timelapse: bool,
        bed_leveling: bool,
        use_ams: bool,
        force: bool = False,
    ) -> dict[str, Any]:
        """Send a validated file to the add-on; returns its answer."""
        # Fresh status so the RUNNING guard does not act on a 30 s old poll.
        await self.async_refresh()
        status = str(
            ((self.data.printers if self.data else {}).get(serial) or {}).get(
                "print_status"
            )
            or ""
        ).upper()
        if status in BUSY_STATUSES and not force:
            raise HomeAssistantError(
                f"Printer {serial} is {status}; refusing to send a new print"
            )

        filename = path.rsplit("/", 1)[-1]
        # Not bypassed by force: that only overrides the busy check. A file that cannot
        # load filament prints air.
        printer = (self.data.printers if self.data else {}).get(serial) or {}
        model = printer.get("dev_product_name") or printer.get("dev_model_name")
        problems = await self.hass.async_add_executor_job(
            check_gcode_3mf, path, model, use_ams
        )
        if problems:
            text = f"Refusing to send {filename}: " + " ".join(problems)
            self._record(serial, filename, text)
            raise HomeAssistantError(text)
        b64 = await self.hass.async_add_executor_job(api.read_b64, path)
        payload = {
            "serial": serial,
            "filename": filename,
            "file_b64": b64,
            "timelapse": timelapse,
            "bed_leveling": bed_leveling,
            "use_ams": use_ams,
        }
        session = async_get_clientsession(self.hass)
        try:
            async with session.post(
                f"{self.url}/api/print",
                json=payload,
                timeout=aiohttp.ClientTimeout(total=SEND_TIMEOUT),
            ) as resp:
                try:
                    body = await resp.json(content_type=None)
                except ValueError:
                    body = await resp.text()
                status_code = resp.status
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            self._record(serial, filename, f"Add-on not reachable: {err}")
            raise HomeAssistantError(f"Add-on not reachable: {err}") from err

        if (
            status_code == 200
            and isinstance(body, dict)
            and body.get("result") in OK_RESULTS
        ):
            self.state_for(serial).last_path = path
            self._record(serial, filename, None)
            if use_ams and getattr(self, "watchdog", None):
                self.watchdog.started(serial, filename)
            return body
        text = _error_text(status_code, body)
        self._record(serial, filename, text)
        raise HomeAssistantError(text)

    def _record(self, serial: str, filename: str, error: str | None) -> None:
        st = self.state_for(serial)
        st.result = "error" if error else "sent"
        st.last_error = error
        st.last_file = filename
        st.last_time = dt_util.utcnow().isoformat()
        self.async_update_listeners()

    async def async_command(self, path: str, body: dict[str, Any]) -> None:
        """POST a printer command (light, RFID re-read) to the add-on."""
        session = async_get_clientsession(self.hass)
        try:
            async with session.post(
                f"{self.url}{path}", json=body, timeout=aiohttp.ClientTimeout(total=60)
            ) as resp:
                try:
                    answer = await resp.json(content_type=None)
                except ValueError:
                    answer = await resp.text()
                if resp.status != 200:
                    raise HomeAssistantError(_error_text(resp.status, answer))
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise HomeAssistantError(f"Add-on not reachable: {err}") from err
