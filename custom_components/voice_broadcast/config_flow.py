"""Config flow for Voice Broadcast.

There is nothing to configure: which speakers can be reached is a property of
the dashboard card, not of the integration. The flow exists only so the
integration can be added from the UI instead of configuration.yaml.
"""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import DOMAIN


class VoiceBroadcastConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Voice Broadcast."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm setup."""
        if self._async_current_entries():
            return self.async_abort(reason="single_instance_allowed")

        if user_input is None:
            return self.async_show_form(step_id="user")

        return self.async_create_entry(title="Voice Broadcast", data={})
