"""The Voice Broadcast integration."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url, remove_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.typing import ConfigType
from homeassistant.loader import async_get_integration

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
                # Cached deliberately. The card is injected as a module on every
                # page load, and Home Assistant only waits a short moment for the
                # custom element to be defined before showing a configuration
                # error. Re-downloading it each time loses that race on a slow
                # connection, so the version query below busts the cache instead.
                cache_headers=True,
            )
        ]
    )
    return True


async def _async_card_url(hass: HomeAssistant) -> str:
    """Return the card URL, versioned so an upgrade invalidates the cache."""
    integration = await async_get_integration(hass, DOMAIN)
    return f"{CARD_URL}?v={integration.version}"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Make the dashboard card available to the frontend."""
    add_extra_js_url(hass, await _async_card_url(hass))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Stop serving the dashboard card."""
    remove_extra_js_url(hass, await _async_card_url(hass))
    return True
