"""The Voice Broadcast integration."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url, remove_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType

from . import websocket
from .clips import ClipStore, ClipView
from .const import CARD_URL, DOMAIN

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

CARD_FILENAME = "voice-broadcast-card.js"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the parts that live for the lifetime of the process.

    Home Assistant cannot unregister views, WebSocket commands or static paths,
    so they are set up once here rather than per config entry. That keeps
    reloading the integration safe.
    """
    store = ClipStore()
    hass.data[DOMAIN] = store

    hass.http.register_view(ClipView(store))
    websocket.async_register(hass)

    await hass.http.async_register_static_paths(
        [
            StaticPathConfig(
                CARD_URL,
                str(Path(__file__).parent / "frontend" / CARD_FILENAME),
                # Not cached, so upgrading the integration takes effect without
                # users having to hard-reload. The card is a few kilobytes.
                cache_headers=False,
            )
        ]
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Make the dashboard card available to the frontend."""
    add_extra_js_url(hass, CARD_URL)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Stop serving the dashboard card."""
    remove_extra_js_url(hass, CARD_URL)
    return True
