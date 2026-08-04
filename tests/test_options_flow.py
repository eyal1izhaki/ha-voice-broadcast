"""Tests for choosing which Home Assistant URL speakers are given."""

from __future__ import annotations

import pytest
from homeassistant.components.media_player import ATTR_MEDIA_CONTENT_ID
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.voice_broadcast.const import (
    CONF_URL_SOURCE,
    URL_SOURCE_AUTO,
    URL_SOURCE_EXTERNAL,
    URL_SOURCE_INTERNAL,
)

from .conftest import ANNOUNCE_PLAYER, wav_b64

INTERNAL = "http://10.0.0.5:8123"
EXTERNAL = "https://home.example.com"


async def broadcast(hass: HomeAssistant, hass_ws_client) -> str:
    """Broadcast one clip and return the URL the speaker was handed."""
    calls = async_mock_service(hass, "media_player", "play_media")
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {
            "type": "voice_broadcast/broadcast",
            "audio": wav_b64(),
            "entity_id": [ANNOUNCE_PLAYER],
        }
    )
    msg = await client.receive_json()
    assert msg["success"], msg

    return calls[0].data[ATTR_MEDIA_CONTENT_ID]


async def test_options_flow_stores_the_choice(
    hass: HomeAssistant, setup_integration
) -> None:
    """The selection is accepted and saved."""
    result = await hass.config_entries.options.async_init(setup_integration.entry_id)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_URL_SOURCE: URL_SOURCE_EXTERNAL}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert setup_integration.options[CONF_URL_SOURCE] == URL_SOURCE_EXTERNAL


@pytest.mark.parametrize(
    ("source", "expected_base"),
    [
        (URL_SOURCE_AUTO, INTERNAL),
        (URL_SOURCE_INTERNAL, INTERNAL),
        (URL_SOURCE_EXTERNAL, EXTERNAL),
    ],
)
async def test_clip_url_follows_the_choice(
    hass: HomeAssistant,
    hass_ws_client,
    setup_integration,
    source: str,
    expected_base: str,
) -> None:
    """Speakers are handed whichever configured address was selected."""
    hass.config.external_url = EXTERNAL
    hass.config_entries.async_update_entry(
        setup_integration, options={CONF_URL_SOURCE: source}
    )
    await hass.async_block_till_done()

    url = await broadcast(hass, hass_ws_client)

    assert url.startswith(f"{expected_base}/api/voice_broadcast/clip/")


async def test_missing_external_url_is_reported(
    hass: HomeAssistant, hass_ws_client, setup_integration
) -> None:
    """Asking for an address Home Assistant does not have fails loudly.

    Falling back to the internal URL would silently reproduce the very problem
    the option exists to solve.
    """
    hass.config.external_url = None
    hass.config_entries.async_update_entry(
        setup_integration, options={CONF_URL_SOURCE: URL_SOURCE_EXTERNAL}
    )
    await hass.async_block_till_done()

    async_mock_service(hass, "media_player", "play_media")
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {
            "type": "voice_broadcast/broadcast",
            "audio": wav_b64(),
            "entity_id": [ANNOUNCE_PLAYER],
        }
    )
    msg = await client.receive_json()

    assert not msg["success"]
    assert msg["error"]["code"] == "not_supported"
