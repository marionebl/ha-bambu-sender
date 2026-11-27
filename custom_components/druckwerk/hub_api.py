"""Calls to the Druckwerk API and Supervisor discovery."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

import aiohttp

from .const import (
    BRIDGE_PORT,
    CONF_HUB_SECRET,
    CONF_HUB_URL,
    CONF_URL,
    HUB_PORT,
    HUB_SECRET_HEADER,
    HUB_SLUG_SUFFIX,
    SUPERVISOR_URL,
)

_TIMEOUT = aiohttp.ClientTimeout(total=10)


def hub_url_for(data: dict[str, Any]) -> str:
    """The hub API URL: explicit, else the bridge host on the hub port."""
    if data.get(CONF_HUB_URL):
        return str(data[CONF_HUB_URL]).rstrip("/")
    return f"http://{urlparse(data[CONF_URL]).hostname}:{HUB_PORT}"


async def hub_request(
    session: aiohttp.ClientSession,
    url: str,
    secret: str | None,
    method: str,
    path: str,
    body: Any = None,
) -> Any:
    """Call the hub; internal calls carry X-Hub-Secret. Raises ClientError/TimeoutError/ValueError."""
    headers = {HUB_SECRET_HEADER: secret} if secret else {}
    async with session.request(
        method, f"{url}{path}", json=body, headers=headers, timeout=_TIMEOUT
    ) as resp:
        resp.raise_for_status()
        return await resp.json(content_type=None)


async def _supervisor(session: aiohttp.ClientSession, path: str) -> Any:
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return None
    try:
        async with session.get(
            f"{SUPERVISOR_URL}{path}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=_TIMEOUT,
        ) as resp:
            resp.raise_for_status()
            return (await resp.json(content_type=None)).get("data")
    except (aiohttp.ClientError, TimeoutError, ValueError, AttributeError):
        return None


async def discover_bridge_url(session: aiohttp.ClientSession) -> str | None:
    """Bridge URL of the Druckwerk app; None off HA OS."""
    data = await _supervisor(session, "/addons")
    slugs = [a.get("slug", "") for a in (data or {}).get("addons", []) if isinstance(a, dict)]
    for slug in [s for s in slugs if s.endswith(HUB_SLUG_SUFFIX)]:
        info = await _supervisor(session, f"/addons/{slug}/info")
        host = (info or {}).get("hostname")
        if host:
            return f"http://{host}:{BRIDGE_PORT}"
    return None
