"""The Voice Broadcast integration."""

from __future__ import annotations

from pathlib import Path

from homeassistant.components.frontend import add_extra_js_url, remove_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.components.lovelace.const import LOVELACE_DATA, MODE_STORAGE
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


def _async_resource_collection(hass: HomeAssistant):
    """Return Lovelace's resource collection, if it can be written to.

    Registering a Lovelace resource is how every other custom card is delivered:
    the frontend reads the resource list over the WebSocket and loads the module
    at runtime. add_extra_js_url instead injects a script tag into the index HTML,
    which a client holding a cached index never sees — the companion app's web
    view especially. Dashboards in YAML mode own their resource list, so there we
    have no choice but the extra_js_url fallback.
    """
    lovelace = hass.data.get(LOVELACE_DATA)
    if lovelace is None or lovelace.resource_mode != MODE_STORAGE:
        return None
    return lovelace.resources


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Make the dashboard card available to the frontend."""
    url = await _async_card_url(hass)

    if (resources := _async_resource_collection(hass)) is None:
        add_extra_js_url(hass, url)
        return True

    # Loads the store; async_items() is empty until this has run.
    await resources.async_get_info()

    for item in resources.async_items():
        if str(item.get("url", "")).split("?")[0] != CARD_URL:
            continue
        if item["url"] != url:
            # Same card, older version: repoint it rather than adding a second.
            await resources.async_update_item(item["id"], {"url": url})
        return True

    await resources.async_create_item({"res_type": "module", "url": url})
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Stop injecting the card.

    A Lovelace resource is left in place: it is user-visible dashboard config,
    and removing it on every reload would churn its id. async_remove_entry cleans
    it up when the integration is actually removed.
    """
    if _async_resource_collection(hass) is None:
        remove_extra_js_url(hass, await _async_card_url(hass))
    return True


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Delete the Lovelace resource when the integration is removed."""
    if (resources := _async_resource_collection(hass)) is None:
        return

    await resources.async_get_info()
    for item in resources.async_items():
        if str(item.get("url", "")).split("?")[0] == CARD_URL:
            await resources.async_delete_item(item["id"])
