"""Tests for overriding the address handed to speakers."""

from __future__ import annotations

import pytest
from homeassistant.components.media_player import ATTR_MEDIA_CONTENT_ID
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import async_mock_service

from custom_components.voice_broadcast.const import CONF_BASE_URL

from .conftest import ANNOUNCE_PLAYER, wav_b64


async def test_options_flow_stores_a_base_url(
    hass: HomeAssistant, setup_integration
) -> None:
    """The override is accepted and saved."""
    result = await hass.config_entries.options.async_init(setup_integration.entry_id)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_BASE_URL: "https://home.example.com/"}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    # Stored without the trailing slash so it concatenates cleanly with a path.
    assert setup_integration.options[CONF_BASE_URL] == "https://home.example.com"


async def test_options_flow_rejects_a_bare_hostname(
    hass: HomeAssistant, setup_integration
) -> None:
    """A value without a scheme would build an unusable URL."""
    result = await hass.config_entries.options.async_init(setup_integration.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_BASE_URL: "home.example.com"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {CONF_BASE_URL: "invalid_url"}


@pytest.mark.parametrize(
    ("override", "expected_prefix"),
    [
        ("https://home.example.com", "https://home.example.com/api/voice_broadcast/"),
        ("", "http://10.0.0.5:8123/api/voice_broadcast/"),
    ],
    ids=["override_used", "falls_back_to_home_assistant_url"],
)
async def test_clip_url_honours_the_override(
    hass: HomeAssistant,
    hass_ws_client,
    setup_integration,
    override: str,
    expected_prefix: str,
) -> None:
    """Speakers are handed the overridden address when one is configured."""
    hass.config_entries.async_update_entry(
        setup_integration, options={CONF_BASE_URL: override}
    )
    await hass.async_block_till_done()

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

    assert msg["success"]
    assert calls[0].data[ATTR_MEDIA_CONTENT_ID].startswith(expected_prefix)
