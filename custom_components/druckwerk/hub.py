"""Polls the hub: queue snapshot and events; fires HA events and notifications."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import hub_api
from .const import (
    ACTION_PREFIX,
    CONF_HUB_SECRET,
    CONF_RECIPIENTS,
    DOMAIN,
    EVENT_INTERVAL,
    EVENT_PREFIX,
)

_LOGGER = logging.getLogger(__name__)
WAITING = ("queued", "pending_approval")


@dataclass
class HubData:
    jobs: list[dict[str, Any]] = field(default_factory=list)


class HubCoordinator(DataUpdateCoordinator[HubData]):
    """Every 5 s: GET /api/events; the queue is refetched when something happened."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass, _LOGGER, config_entry=entry, name=f"{DOMAIN}_hub", update_interval=EVENT_INTERVAL
        )
        self.url = hub_api.hub_url_for(entry.data)
        self.secret: str = entry.data[CONF_HUB_SECRET]
        self.seq: int | None = None  # None until the first poll; its backlog is not replayed

    async def call(self, method: str, path: str, body: Any = None) -> Any:
        session = async_get_clientsession(self.hass)
        return await hub_api.hub_request(session, self.url, self.secret, method, path, body)

    async def _async_update_data(self) -> HubData:
        try:
            query = "" if self.seq is None else f"?since={self.seq}"
            doc = await self.call("GET", f"/api/events{query}")
            events = doc.get("events", [])
            first = self.seq is None
            self.seq = doc.get("seq", self.seq)
            if first or events or self.data is None:
                queue = await self.call("GET", "/api/queue")
                data = HubData(jobs=list(queue.get("jobs", [])))
            else:
                data = self.data
        except (aiohttp.ClientError, TimeoutError, ValueError, AttributeError) as err:
            raise UpdateFailed(f"Druckwerk not reachable: {err}") from err
        self.data = data  # handlers below see the fresh queue
        if not first:
            for event in events:
                await self._handle(event)
        return data

    def next_job(self, serial: str, exclude: str | None = None) -> dict[str, Any] | None:
        """First queued job for this printer (or for any printer)."""
        for job in self.data.jobs if self.data else []:
            if (
                job.get("state") == "queued"
                and job.get("target") in (serial, "any")
                and job.get("id") != exclude
            ):
                return job
        return None

    async def _handle(self, event: dict[str, Any]) -> None:
        etype, serial = event.get("type"), event.get("serial")
        if not etype:
            return
        self.hass.bus.async_fire(f"{EVENT_PREFIX}{etype}", event)
        job = event.get("job") or {}
        try:
            if etype == "job_done":
                nxt = self.next_job(serial, exclude=job.get("id"))
                if nxt:
                    await self.notify_admins(
                        serial,
                        f"Plate clear? Start next: {nxt.get('file')}",
                        actions=[{"action": f"{ACTION_PREFIX}{serial}", "title": "Plate clear, start"}],
                    )
            elif etype == "approval_needed":
                who = job.get("owner_name") or "Someone"
                await self.notify_admins(serial, f"{who} needs approval to print {job.get('file')}")
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            _LOGGER.warning("Cannot notify for %s: %s", etype, err)

    async def admin_user_ids(self, serial: str) -> set[str]:
        """HA admins plus active owners of the printer (or of all printers)."""
        ids: set[str] = set()
        for g in await self.call("GET", "/api/grants"):
            if g.get("active", True) and g.get("role") == "owner" and g.get("printer") in (serial, "*"):
                ids.add(g.get("user_id"))
        for u in await self.call("GET", "/api/ha/users"):
            if u.get("is_admin"):
                ids.add(u.get("id"))
        return ids

    async def notify_admins(self, serial: str, message: str, actions: list | None = None) -> None:
        """Notify the printer's admins who are mapped and at home (person state "home")."""
        recipients: dict[str, str] = self.config_entry.options.get(CONF_RECIPIENTS, {})
        if not recipients:
            return
        admins = await self.admin_user_ids(serial)
        for person, service in recipients.items():
            state = self.hass.states.get(person)
            if not state or state.state != "home" or state.attributes.get("user_id") not in admins:
                continue
            data: dict[str, Any] = {"actions": actions} if actions else {}
            await self.hass.services.async_call(
                "notify",
                service.removeprefix("notify."),
                {"title": "Druckwerk", "message": message, "data": data},
            )

    async def start_next(self, serial: str) -> None:
        await self.call("POST", f"/api/printers/{serial}/start-next", {"plate_clear": True})
        await self.async_request_refresh()

    async def on_action(self, event: Event) -> None:
        action = str(event.data.get("action", ""))
        if action.startswith(ACTION_PREFIX):
            try:
                await self.start_next(action[len(ACTION_PREFIX):])
            except (aiohttp.ClientError, TimeoutError, ValueError) as err:
                _LOGGER.warning("start-next failed: %s", err)
