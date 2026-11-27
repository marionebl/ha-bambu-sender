"""Second safety layer after the G-code check: pause a Druckwerk print that runs without filament.

Reads the vendor integration's sensors (unique_id <serial>_print_status / _stage / _active_tray), not the add-on's
bridge status, which can be hours stale. Only prints started through `started()` are watched.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable

from homeassistant.components import persistent_notification
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval

from .externals import VENDOR_DOMAIN

_LOGGER = logging.getLogger(__name__)
CHECK_EVERY = timedelta(seconds=10)
AIR_FOR = timedelta(seconds=120)  # no filament this long while "printing": pause
GIVE_UP_WAITING = timedelta(minutes=15)  # a print that never reached running was not started
NO_FILAMENT_STAGES = {"printing", "idle"}
NOT_A_TRAY = {"none", "unknown", "unavailable"}


@dataclass
class _Watch:
    serial: str
    filename: str
    started_at: datetime
    ids: dict[str, str]  # key -> the vendor integration entity_id
    seen_running: bool = False
    changing_filament: bool = False
    air_since: datetime | None = None
    unsub: Callable[[], None] | None = field(default=None, repr=False)


class PrintWatchdog:
    def __init__(self, hass: HomeAssistant, coordinator) -> None:
        self.hass = hass
        self.coordinator = coordinator
        self._watches: dict[str, _Watch] = {}
        self._unsub_timer = async_track_time_interval(hass, self._tick, CHECK_EVERY)

    def started(self, serial: str, filename: str) -> None:
        """Call when Druckwerk has sent a print with use_ams=true."""
        reg = er.async_get(self.hass)
        ids = {
            key: reg.async_get_entity_id("sensor", VENDOR_DOMAIN, f"{serial}_{key}")
            for key in ("print_status", "stage", "active_tray")
        }
        if not all(ids.values()):
            _LOGGER.warning("Air-print watchdog off for %s: the vendor integration sensors not found", serial)
            return
        self._stop(serial)
        watch = _Watch(serial, filename, self._now(), ids)
        watch.unsub = async_track_state_change_event(self.hass, [ids["stage"]], self._on_stage(watch))
        self._watches[serial] = watch

    def async_stop(self) -> None:
        self._unsub_timer()
        for serial in list(self._watches):
            self._stop(serial)

    def _stop(self, serial: str) -> None:
        if watch := self._watches.pop(serial, None):
            watch.unsub()

    @staticmethod
    def _now() -> datetime:
        return dt_util.utcnow()

    def _value(self, watch: _Watch, key: str) -> str | None:
        state = self.hass.states.get(watch.ids[key])
        return state.state if state else None

    def _on_stage(self, watch: _Watch):
        @callback
        def listener(event) -> None:
            # changing_filament lasts about a second, shorter than the check interval.
            new = event.data.get("new_state")
            if new and new.state == "changing_filament":
                watch.changing_filament = True

        return listener

    async def _tick(self, now: datetime) -> None:
        for watch in list(self._watches.values()):
            await self._check(watch, now)

    async def _check(self, watch: _Watch, now: datetime) -> None:
        status = self._value(watch, "print_status")
        if status == "running":
            watch.seen_running = True
        elif watch.seen_running and status != "pause":
            return self._stop(watch.serial)  # finished, failed or cancelled
        elif not watch.seen_running and now - watch.started_at > GIVE_UP_WAITING:
            return self._stop(watch.serial)  # sent, but never started
        if status != "running":
            watch.air_since = None
            return

        tray = self._value(watch, "active_tray")
        if watch.changing_filament and tray not in NOT_A_TRAY and tray is not None:
            return self._stop(watch.serial)  # filament is loaded

        # `idle` counts like `printing`: the bad print never left stage idle (it printed air for an
        # hour with stage idle and tray none), while a good print passes through other stages.
        # Only the literal "none" counts: unknown/unavailable just means the vendor integration lost the printer.
        if tray == "none" and self._value(watch, "stage") in NO_FILAMENT_STAGES:
            watch.air_since = watch.air_since or now
            if now - watch.air_since >= AIR_FOR:
                self._stop(watch.serial)  # pause once, never again for this print
                await self._pause(watch)
        else:
            watch.air_since = None

    async def _pause(self, watch: _Watch) -> None:
        name = (self.coordinator.data.printers.get(watch.serial, {}) if self.coordinator.data else {}).get("name")
        printer = f"{name} ({watch.serial})" if name else watch.serial
        try:
            await self.coordinator.async_command(f"/api/pause/{watch.serial}", {})
            outcome = "The print was paused."
        except HomeAssistantError as err:
            outcome = f"Pausing FAILED ({err}); stop the print yourself."
        persistent_notification.async_create(
            self.hass,
            f"{printer} is printing {watch.filename} without filament: for 120 s no filament was "
            f"loaded (active tray none, stage printing or idle). {outcome}",
            title="Print without filament",
            notification_id=f"druckwerk_air_{watch.serial}",
        )
