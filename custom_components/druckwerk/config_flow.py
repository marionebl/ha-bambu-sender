"""Config flow for Druckwerk."""

from __future__ import annotations

from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.core import callback
from homeassistant.helpers.selector import ObjectSelector, TextSelector, TextSelectorConfig, TextSelectorType
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.hassio import HassioServiceInfo

from . import hub_api
from .api import async_get_json
from .const import (
    BRIDGE_PORT,
    CONF_HUB_SECRET,
    CONF_HUB_URL,
    CONF_RECIPIENTS,
    CONF_UPLOAD_SECRET,
    CONF_URL,
    DEFAULT_HUB_URL,
    DOMAIN,
    HUB_PORT,
    MIN_SECRET_LENGTH,
)


class DruckwerkConfigFlow(ConfigFlow, domain=DOMAIN):
    """Ask for the add-on URL and check it answers /api/health."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return DruckwerkOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()
        errors: dict[str, str] = {}
        session = async_get_clientsession(self.hass)
        if user_input is not None:
            url = user_input[CONF_URL].strip().rstrip("/")
            try:
                health = await async_get_json(
                    async_get_clientsession(self.hass), url, "/api/health"
                )
                if not isinstance(health, dict) or "server" not in health:
                    raise ValueError("not a Druckwerk health document")
            except (aiohttp.ClientError, TimeoutError, ValueError):
                errors["base"] = "cannot_connect"
            else:
                return self.async_create_entry(
                    title="Druckwerk", data={CONF_URL: url}
                )
        # Supervisor tells where the app runs; manual otherwise.
        default = await hub_api.discover_bridge_url(session) or DEFAULT_HUB_URL
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {vol.Required(CONF_URL, default=default): str}
            ),
            errors=errors,
        )


    async def async_step_hassio(self, discovery_info: HassioServiceInfo) -> ConfigFlowResult:
        """The Hub app announces host, ports and secret through Supervisor discovery."""
        cfg = discovery_info.config
        host = cfg["host"]
        self._discovered = {
            CONF_URL: f"http://{host}:{cfg.get('bridge_port', BRIDGE_PORT)}",
            CONF_HUB_URL: f"http://{host}:{cfg.get('hub_port', HUB_PORT)}",
            CONF_HUB_SECRET: cfg.get("secret", ""),
        }
        await self.async_set_unique_id(DOMAIN)
        # An existing entry takes the new url/secret; its update listener reloads it.
        self._abort_if_unique_id_configured(updates=self._discovered, reload_on_update=False)
        return await self.async_step_hassio_confirm()

    async def async_step_hassio_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(title="Druckwerk", data=self._discovered)
        return self.async_show_form(step_id="hassio_confirm")


class DruckwerkOptionsFlow(OptionsFlow):
    """Upload secret, bridge/hub URLs, hub secret and notification recipients."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self.config_entry
        errors: dict[str, str] = {}
        if user_input is not None:
            secret = (user_input.get(CONF_UPLOAD_SECRET) or "").strip()
            recipients = user_input.get(CONF_RECIPIENTS) or {}
            data = dict(entry.data)
            if user_input.get(CONF_URL):
                data[CONF_URL] = user_input[CONF_URL].strip().rstrip("/")
            if user_input.get(CONF_HUB_URL):
                data[CONF_HUB_URL] = user_input[CONF_HUB_URL].strip().rstrip("/")
            hub_secret = (user_input.get(CONF_HUB_SECRET) or data.get(CONF_HUB_SECRET) or "").strip()
            session = async_get_clientsession(self.hass)
            hub_url = hub_api.hub_url_for(data)
            if hub_secret and not await self._hub_accepts(session, hub_url, hub_secret):
                errors[CONF_HUB_SECRET] = "invalid_hub_secret"
            if secret and len(secret) < MIN_SECRET_LENGTH:
                errors[CONF_UPLOAD_SECRET] = "secret_too_short"
            if not (isinstance(recipients, dict) and all(
                str(k).startswith("person.") and isinstance(v, str) for k, v in recipients.items()
            )):
                errors[CONF_RECIPIENTS] = "bad_recipients"
            if not errors:
                if hub_secret:
                    data[CONF_HUB_SECRET] = hub_secret
                if data != dict(entry.data):
                    self.hass.config_entries.async_update_entry(entry, data=data)
                options = {CONF_UPLOAD_SECRET: secret}
                if recipients:
                    options[CONF_RECIPIENTS] = recipients
                return self.async_create_entry(data=options)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Optional(CONF_UPLOAD_SECRET): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                    vol.Optional(CONF_URL): str,
                    vol.Optional(CONF_HUB_URL): str,
                    vol.Optional(CONF_HUB_SECRET): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    ),
                    vol.Optional(CONF_RECIPIENTS): ObjectSelector(),
                }
            ),
            errors=errors,
        )

    @staticmethod
    async def _hub_accepts(session, url: str, secret: str) -> bool:
        try:
            await hub_api.hub_request(session, url, secret, "GET", "/api/events")
        except (aiohttp.ClientError, TimeoutError, ValueError):
            return False
        return True
