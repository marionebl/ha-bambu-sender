"""Camera through the add-on: a live stream (go2rtc plays it) and stills through the snapshot route."""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path

import aiohttp
from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util

from .const import CAMERA_MAX_AGE, CAMERA_STALE, CAMERA_TIMEOUT
from .entity import SenderEntity
from .platform_helper import add_per_printer

_LOGGER = logging.getLogger(__name__)
OFFLINE_JPEG = Path(__file__).with_name("offline.jpg")
JPEG_PRODUCTS = ("P1", "A1")  # P1P, P1S, A1, A1 mini


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    add_per_printer(entry, async_add_entities, lambda c, s: [CloudCamera(c, s)])


class CloudCamera(SenderEntity, Camera):
    # Each snapshot opens a new cloud tunnel and waits for a keyframe (10-60 s under qemu).
    # So the entity serves the last image and refreshes it in the background.

    _unrecorded_attributes = frozenset({"frame_time"})

    def __init__(self, coordinator, serial) -> None:
        SenderEntity.__init__(self, coordinator, serial, "cloud_camera")
        Camera.__init__(self)
        self._image: bytes | None = None
        self._fetched = 0.0
        self._task: asyncio.Task | None = None
        self._offline_image: bytes | None = None
        self._frame_time: str | None = None
        self._last_error: str | None = None

    @property
    def online(self) -> bool:
        # Without the printer's MQTT status the add-on cannot mint a camera URL and answers 502.
        live = self.printer.get("live")
        return isinstance(live, dict) and bool(live.get("cached"))

    @property
    def supported_features(self) -> CameraEntityFeature:
        # Offline, the frontend shows the still image instead of a stream that can only fail.
        return CameraEntityFeature.STREAM if self.online else CameraEntityFeature(0)

    @property
    def extra_state_attributes(self) -> dict:
        return {"online": self.online, "frame_time": self._frame_time, "last_error": self._last_error}

    async def _fetch(self) -> None:
        session = async_get_clientsession(self.hass)
        url = f"{self.coordinator.url}/api/camera/{self.serial}/snapshot"
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=CAMERA_TIMEOUT)) as resp:
                body = await resp.read()
                if resp.status == 200 and resp.content_type == "image/jpeg":
                    self._image, error = body, None
                    self._frame_time = dt_util.utcnow().isoformat()
                else:
                    error = f"HTTP {resp.status}: {body[:300].decode(errors='replace')}"
        except (aiohttp.ClientError, TimeoutError) as err:
            error = f"{type(err).__name__}: {err}"
        self._fetched = time.monotonic()
        if error:
            _LOGGER.debug("Snapshot for %s failed: %s", self.serial, error)
        self._last_error = error
        self.async_write_ha_state()

    async def stream_source(self) -> str | None:
        if not self.online:
            return None
        # The add-on shares one cloud tunnel between all viewers, so go2rtc can open this at will.
        url = f"{self.coordinator.url}/api/camera/{self.serial}/live"
        if str(self.printer.get("dev_product_name") or "").startswith(JPEG_PRODUCTS):
            # These printers send JPEG frames, which WebRTC cannot carry: go2rtc's ffmpeg encodes H.264.
            return f"ffmpeg:{url}#video=h264"
        return url

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        if not self.online:
            # A fetch can only fail, and None makes HA answer 500. The last frame may no longer hold.
            if self._offline_image is None:
                self._offline_image = await self.hass.async_add_executor_job(OFFLINE_JPEG.read_bytes)
            return self._offline_image
        stale = time.monotonic() - self._fetched > CAMERA_MAX_AGE
        if stale and (self._task is None or self._task.done()):
            self._task = self.hass.async_create_background_task(self._fetch(), f"druckwerk camera {self.serial}")
        if (self._image is None or time.monotonic() - self._fetched > CAMERA_STALE) and self._task:
            # An old frame can show a state that no longer holds (light off), so wait for a new one.
            # The fetch continues if the caller times out; the card's next poll gets it.
            await asyncio.shield(self._task)
        return self._image
