"""Config flow for Voice Broadcast.

Setup itself needs no input: which speakers can be reached is a property of the
dashboard card, not of the integration. The options flow exists for one thing —
choosing which of Home Assistant's configured URLs speakers are given, for
installs where the internal one is not reachable by the speakers.
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
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from .const import (
    CONF_URL_SOURCE,
    DOMAIN,
    URL_SOURCE_AUTO,
    URL_SOURCE_EXTERNAL,
    URL_SOURCE_INTERNAL,
)


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
    """Choose which Home Assistant URL speakers are given."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask which address to hand to speakers."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current = self.config_entry.options.get(CONF_URL_SOURCE, URL_SOURCE_AUTO)
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_URL_SOURCE, default=current): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                URL_SOURCE_AUTO,
                                URL_SOURCE_EXTERNAL,
                                URL_SOURCE_INTERNAL,
                            ],
                            translation_key=CONF_URL_SOURCE,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    )
                }
            ),
        )
