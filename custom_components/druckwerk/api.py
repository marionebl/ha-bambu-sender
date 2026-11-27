"""Add-on HTTP calls and file path handling."""

from __future__ import annotations

import base64
import os
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError

from . import preview as _preview
from .const import FILE_SUFFIX, MEDIA_SUBDIR, SHARE_ROOT

_TIMEOUT = aiohttp.ClientTimeout(total=10)


async def async_get_json(session: aiohttp.ClientSession, url: str, path: str) -> Any:
    """GET a JSON document from the add-on; raises ClientError/TimeoutError/ValueError."""
    async with session.get(f"{url}{path}", timeout=_TIMEOUT) as resp:
        resp.raise_for_status()
        return await resp.json(content_type=None)


def media_root(hass: HomeAssistant) -> str:
    """The HA media folder (default /media)."""
    return hass.config.media_dirs.get("local") or "/media"


def base_dir(hass: HomeAssistant) -> str:
    """Folder that holds the printable files."""
    return os.path.join(media_root(hass), MEDIA_SUBDIR)


def scan_files(base: str) -> list[str]:
    """Relative POSIX paths of all *.gcode.3mf below base (blocking)."""
    found: list[str] = []
    for dirpath, _dirs, names in os.walk(base):
        for name in names:
            if name.lower().endswith(FILE_SUFFIX):
                rel = os.path.relpath(os.path.join(dirpath, name), base)
                found.append(rel.replace(os.sep, "/"))
    return sorted(found)


def _inside(path: str, root: str) -> bool:
    return os.path.commonpath([path, root]) == root


def resolve_file(hass: HomeAssistant, file: str) -> str:
    """Validate a user supplied path and return its real path (blocking).

    Relative paths must stay inside the druckwerk media folder; absolute
    ones inside a media folder or /share. realpath defeats ../ and symlinks.
    """
    base = os.path.realpath(base_dir(hass))
    if os.path.isabs(file):
        roots = [os.path.realpath(r) for r in hass.config.media_dirs.values()]
        roots.append(os.path.realpath(SHARE_ROOT))
        real = os.path.realpath(file)
        if not any(_inside(real, r) for r in roots):
            raise ServiceValidationError(
                f"Path {file} is outside the allowed folders (/media, /share)"
            )
    else:
        real = os.path.realpath(os.path.join(base, file))
        if not _inside(real, base):
            raise ServiceValidationError(
                f"Path {file} is outside the {MEDIA_SUBDIR} media folder"
            )
    if not real.lower().endswith(FILE_SUFFIX):
        raise ServiceValidationError(f"Only {FILE_SUFFIX} files can be printed")
    if not os.path.isfile(real):
        raise ServiceValidationError(f"File not found: {file}")
    return real


def read_b64(path: str) -> str:
    """Read a file and base64 encode it (blocking)."""
    with open(path, "rb") as fh:
        return base64.b64encode(fh.read()).decode("ascii")


def plate_png(path: str) -> bytes | None:
    """The preview of the first sliced plate, or None (blocking)."""
    return _preview.plate_png(path)
