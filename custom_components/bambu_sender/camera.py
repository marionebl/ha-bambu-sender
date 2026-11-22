"""Still camera through the add-on's cloud tunnel (TUTK) snapshot route."""

from __future__ import annotations

import asyncio
import logging
import time

import aiohttp
from homeassistant.components.camera import Camera
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import CAMERA_MAX_AGE, CAMERA_STALE, CAMERA_TIMEOUT
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

    async def async_camera_image(self, width: int | None = None, height: int | None = None) -> bytes | None:
        stale = time.monotonic() - self._fetched > CAMERA_MAX_AGE
        if stale and (self._task is None or self._task.done()):
            self._task = self.hass.async_create_background_task(self._fetch(), f"bambu_sender camera {self.serial}")
        if (self._image is None or time.monotonic() - self._fetched > CAMERA_STALE) and self._task:
            # An old frame can show a state that no longer holds (light off), so wait for a new one.
            # The fetch continues if the caller times out; the card's next poll gets it.
            await asyncio.shield(self._task)
        return self._image
