"""Fixtures for the Voice Broadcast tests."""

from __future__ import annotations

import base64

import pytest
from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.const import ATTR_SUPPORTED_FEATURES
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.voice_broadcast.const import DOMAIN

ANNOUNCE_PLAYER = "media_player.kitchen"
BASIC_PLAYER = "media_player.garage"


def make_wav(payload: bytes = b"\x00" * 64) -> bytes:
    """Return a minimal but structurally valid 16 kHz mono WAV file."""
    header = (
        b"RIFF"
        + (36 + len(payload)).to_bytes(4, "little")
        + b"WAVE"
        + b"fmt "
        + (16).to_bytes(4, "little")
        + b"\x01\x00"  # PCM
        + b"\x01\x00"  # mono
        + (16000).to_bytes(4, "little")
        + (32000).to_bytes(4, "little")
        + b"\x02\x00"
        + b"\x10\x00"
        + b"data"
        + len(payload).to_bytes(4, "little")
    )
    return header + payload


def wav_b64(payload: bytes = b"\x00" * 64) -> str:
    """Return a valid WAV file as a base64 string, as the card would send it."""
    return base64.b64encode(make_wav(payload)).decode()


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow Home Assistant to load this custom integration in every test."""
    return


@pytest.fixture
async def setup_integration(hass: HomeAssistant) -> MockConfigEntry:
    """Set up the integration alongside two speakers with different features."""
    # Speakers fetch clips over HTTP, so a reachable URL has to exist.
    hass.config.internal_url = "http://10.0.0.5:8123"
    assert await async_setup_component(hass, "http", {})

    hass.states.async_set(
        ANNOUNCE_PLAYER,
        "idle",
        {ATTR_SUPPORTED_FEATURES: MediaPlayerEntityFeature.MEDIA_ANNOUNCE},
    )
    hass.states.async_set(
        BASIC_PLAYER,
        "idle",
        {ATTR_SUPPORTED_FEATURES: MediaPlayerEntityFeature.PLAY_MEDIA},
    )

    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry
