"""Tests for the broadcast WebSocket command."""

from __future__ import annotations

import base64
from urllib.parse import urlparse

import pytest
from homeassistant.components.media_player import (
    ATTR_MEDIA_ANNOUNCE,
    ATTR_MEDIA_CONTENT_ID,
)
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockUser, async_mock_service

from .conftest import ANNOUNCE_PLAYER, BASIC_PLAYER, wav_b64

BROADCAST = "voice_broadcast/broadcast"


@pytest.fixture
def family_member(hass_admin_user: MockUser) -> MockUser:
    """Demote the test user to a normal, non-admin family member.

    Home Assistant's built-in users group grants control of all entities without
    granting admin, which is exactly the account type that cannot use the core
    media upload endpoint.
    """
    hass_admin_user.groups = []
    hass_admin_user.mock_policy({"entities": {"all": {"read": True, "control": True}}})
    assert not hass_admin_user.is_admin
    return hass_admin_user


async def test_non_admin_can_broadcast(
    hass: HomeAssistant, hass_ws_client, family_member, setup_integration
) -> None:
    """A non-admin user must be able to broadcast.

    This is the regression that justifies the integration existing at all.
    """
    calls = async_mock_service(hass, "media_player", "play_media")
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {"type": BROADCAST, "audio": wav_b64(), "entity_id": [ANNOUNCE_PLAYER]}
    )
    msg = await client.receive_json()

    assert msg["success"]
    assert msg["result"]["targets"][ANNOUNCE_PLAYER]["ok"] is True
    assert len(calls) == 1


async def test_announce_only_where_supported(
    hass: HomeAssistant, hass_ws_client, setup_integration
) -> None:
    """Announce is added for players advertising it, and omitted for the rest."""
    calls = async_mock_service(hass, "media_player", "play_media")
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {
            "type": BROADCAST,
            "audio": wav_b64(),
            "entity_id": [ANNOUNCE_PLAYER, BASIC_PLAYER],
        }
    )
    msg = await client.receive_json()
    assert msg["success"]

    by_entity = {call.data["entity_id"]: call.data for call in calls}
    assert by_entity[ANNOUNCE_PLAYER][ATTR_MEDIA_ANNOUNCE] is True
    assert ATTR_MEDIA_ANNOUNCE not in by_entity[BASIC_PLAYER]

    # Both speakers are handed the same signed, absolute URL, and it ends in
    # .wav before the query string because speakers sniff the format from the
    # path rather than the Content-Type header.
    urls = {data[ATTR_MEDIA_CONTENT_ID] for data in by_entity.values()}
    assert len(urls) == 1
    url = urls.pop()
    assert url.startswith("http://10.0.0.5:8123/api/voice_broadcast/clip/")
    assert urlparse(url).path.endswith(".wav")


async def test_entity_permission_is_enforced(
    hass: HomeAssistant, hass_ws_client, hass_admin_user: MockUser, setup_integration
) -> None:
    """A restricted user cannot broadcast to a speaker they may not control."""
    hass_admin_user.groups = []
    hass_admin_user.mock_policy({"entities": {"entity_ids": {ANNOUNCE_PLAYER: True}}})

    calls = async_mock_service(hass, "media_player", "play_media")
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {"type": BROADCAST, "audio": wav_b64(), "entity_id": [BASIC_PLAYER]}
    )
    msg = await client.receive_json()

    assert not msg["success"]
    assert msg["error"]["code"] == "unauthorized"
    assert not calls


async def test_unknown_entity_is_reported_not_raised(
    hass: HomeAssistant, hass_ws_client, setup_integration
) -> None:
    """A missing speaker is reported per-target so the card can show it."""
    async_mock_service(hass, "media_player", "play_media")
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {
            "type": BROADCAST,
            "audio": wav_b64(),
            "entity_id": [ANNOUNCE_PLAYER, "media_player.nonexistent"],
        }
    )
    msg = await client.receive_json()

    assert msg["success"]
    assert msg["result"]["targets"][ANNOUNCE_PLAYER]["ok"] is True
    assert msg["result"]["targets"]["media_player.nonexistent"] == {
        "ok": False,
        "error": "unknown entity",
    }


async def test_rejects_non_media_player_entity(
    hass: HomeAssistant, hass_ws_client, setup_integration
) -> None:
    """Only media_player entities may be targeted."""
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {"type": BROADCAST, "audio": wav_b64(), "entity_id": ["light.kitchen"]}
    )
    msg = await client.receive_json()

    assert not msg["success"]
    assert msg["error"]["code"] == "invalid_format"


async def test_rejects_invalid_base64(
    hass: HomeAssistant, hass_ws_client, setup_integration
) -> None:
    """Audio that is not base64 is refused."""
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {"type": BROADCAST, "audio": "not base64!!", "entity_id": [ANNOUNCE_PLAYER]}
    )
    msg = await client.receive_json()

    assert not msg["success"]
    assert msg["error"]["code"] == "invalid_format"


async def test_rejects_non_wav_payload(
    hass: HomeAssistant, hass_ws_client, setup_integration
) -> None:
    """Arbitrary bytes cannot be parked behind a Home Assistant URL."""
    client = await hass_ws_client(hass)
    payload = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100).decode()

    await client.send_json_auto_id(
        {"type": BROADCAST, "audio": payload, "entity_id": [ANNOUNCE_PLAYER]}
    )
    msg = await client.receive_json()

    assert not msg["success"]
    assert msg["error"]["code"] == "invalid_format"
    assert "WAV" in msg["error"]["message"]


async def test_rejects_oversized_clip(
    hass: HomeAssistant,
    hass_ws_client,
    setup_integration,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Clips beyond the size cap are refused before being stored."""
    monkeypatch.setattr(
        "custom_components.voice_broadcast.websocket.MAX_CLIP_BYTES", 100
    )
    client = await hass_ws_client(hass)

    await client.send_json_auto_id(
        {
            "type": BROADCAST,
            "audio": wav_b64(b"\x00" * 500),
            "entity_id": [ANNOUNCE_PLAYER],
        }
    )
    msg = await client.receive_json()

    assert not msg["success"]
    assert msg["error"]["code"] == "invalid_format"
