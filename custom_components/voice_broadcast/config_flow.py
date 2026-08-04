"""Config flow for Voice Broadcast.

Setup itself needs no input: which speakers can be reached is a property of the
dashboard card, not of the integration. The options flow exists for one thing —
overriding the address speakers are told to fetch audio from, for installs where
Home Assistant's own Internal URL is not reachable by the speakers.
"""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.core import callback

from .const import CONF_BASE_URL, DOMAIN


class VoiceBroadcastConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Voice Broadcast."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow."""
        return VoiceBroadcastOptionsFlow()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm setup."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is None:
            return self.async_show_form(step_id="user")

        return self.async_create_entry(title="Voice Broadcast", data={})


class VoiceBroadcastOptionsFlow(OptionsFlow):
    """Let the user override the address handed to speakers."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for an optional base URL."""
        errors: dict[str, str] = {}

        if user_input is not None:
            base_url = user_input.get(CONF_BASE_URL, "").strip().rstrip("/")
            if base_url and not base_url.startswith(("http://", "https://")):
                errors[CONF_BASE_URL] = "invalid_url"
            else:
                return self.async_create_entry(data={CONF_BASE_URL: base_url})

        current = self.config_entry.options.get(CONF_BASE_URL, "")
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema({vol.Optional(CONF_BASE_URL, default=current): str}),
            errors=errors,
        )
