"""Camera through the add-on: live MJPEG proxy, and stills from its snapshot route."""

from __future__ import annotations

import asyncio
import logging
import time

import aiohttp
from aiohttp import web
from homeassistant.components.camera import Camera
from homeassistant.helpers.aiohttp_client import async_aiohttp_proxy_stream, async_get_clientsession

from .const import CAMERA_FRAME_INTERVAL, CAMERA_MAX_AGE, CAMERA_STALE, CAMERA_TIMEOUT
from .entity import SenderEntity
from .platform_helper import add_per_printer

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities) -> None:
    add_per_printer(entry, async_add_entities, lambda c, s: [CloudCamera(c, s)])


class CloudCamera(SenderEntity, Camera):
    # Each snapshot opens a new cloud tunnel and waits for a keyframe (10-60 s under qemu).
    # So the entity serves the last image and refreshes it in the background.

    def __init__(self, coordinator, serial) -> None:
        SenderEntity.__init__(self, coordinator, serial, "cloud_camera")
        Camera.__init__(self)
        self._image: bytes | None = None
        self._fetched = 0.0
        self._task: asyncio.Task | None = None
        self._attr_extra_state_attributes = {"last_error": None}
        self._attr_frame_interval = CAMERA_FRAME_INTERVAL

    async def _fetch(self) -> None:
        session = async_get_clientsession(self.hass)
        url = f"{self.coordinator.url}/api/camera/{self.serial}/snapshot"
        try:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=CAMERA_TIMEOUT)) as resp:
                body = await resp.read()
                if resp.status == 200 and resp.content_type == "image/jpeg":
                    self._image, error = body, None
                else:
                    error = f"HTTP {resp.status}: {body[:300].decode(errors='replace')}"
        except (aiohttp.ClientError, TimeoutError) as err:
            error = f"{type(err).__name__}: {err}"
        self._fetched = time.monotonic()
        if error:
            _LOGGER.debug("Snapshot for %s failed: %s", self.serial, error)
        self._attr_extra_state_attributes = {"last_error": error}
        self.async_write_ha_state()

    def _set_error(self, error: str | None) -> None:
        self._attr_extra_state_attributes = {"last_error": error}
        self.async_write_ha_state()

    async def handle_async_mjpeg_stream(self, request: web.Request) -> web.StreamResponse | None:
        # The add-on shares one bridge session between viewers; the first frame can take up to 90 s.
        url = f"{self.coordinator.url}/api/camera/{self.serial}/stream.mjpeg"
        session = async_get_clientsession(self.hass)
        try:
            async with asyncio.timeout(CAMERA_TIMEOUT):
                resp = await session.get(url, timeout=aiohttp.ClientTimeout(total=None, sock_read=CAMERA_TIMEOUT))
        except (aiohttp.ClientError, TimeoutError) as err:
            self._set_error(f"{type(err).__name__}: {err}")
            raise web.HTTPBadGateway from err
        try:
            if resp.status != 200:
                body = await resp.read()
                self._set_error(f"HTTP {resp.status}: {body[:300].decode(errors='replace')}")
                raise web.HTTPBadGateway
            if self._attr_extra_state_attributes["last_error"]:
                self._set_error(None)
            return await async_aiohttp_proxy_stream(
                self.hass, request, resp.content, resp.headers.get("Content-Type"), timeout=CAMERA_TIMEOUT
            )
        finally:
            resp.close()

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        stale = time.monotonic() - self._fetched > CAMERA_MAX_AGE
        if stale and (self._task is None or self._task.done()):
            self._task = self.hass.async_create_background_task(self._fetch(), f"bambu_sender camera {self.serial}")
        if (self._image is None or time.monotonic() - self._fetched > CAMERA_STALE) and self._task:
            # An old frame can show a state that no longer holds (light off), so wait for a new one.
            # The fetch continues if the caller times out; the card's next poll gets it.
            await asyncio.shield(self._task)
        return self._image
