"""Tests for clip storage and delivery."""

from __future__ import annotations

from datetime import timedelta
from http import HTTPStatus

from homeassistant.components.http.auth import async_sign_path
from homeassistant.core import HomeAssistant

from custom_components.voice_broadcast.clips import ClipStore
from custom_components.voice_broadcast.const import (
    CARD_URL,
    CLIP_TTL,
    CLIP_URL_BASE,
    DOMAIN,
    MAX_CLIPS,
)

from .conftest import make_wav


def test_store_evicts_oldest_beyond_the_cap() -> None:
    """Memory stays bounded, dropping the oldest clips first."""
    store = ClipStore()
    clip_ids = [store.add(make_wav(bytes([i]) * 64)) for i in range(MAX_CLIPS + 2)]

    assert len(store) == MAX_CLIPS
    assert store.get(clip_ids[0]) is None
    assert store.get(clip_ids[-1]) is not None


def test_store_expires_clips(freezer) -> None:
    """A clip is unavailable once its lifetime has passed."""
    store = ClipStore()
    clip_id = store.add(make_wav())
    assert store.get(clip_id) is not None

    freezer.tick(CLIP_TTL + timedelta(seconds=1))
    assert store.get(clip_id) is None
    assert len(store) == 0


async def test_clip_requires_a_signature(
    hass: HomeAssistant, hass_client_no_auth, setup_integration
) -> None:
    """Speakers can fetch a clip with a signed URL, but not without one."""
    store: ClipStore = hass.data[DOMAIN]
    audio = make_wav()
    clip_id = store.add(audio)
    client = await hass_client_no_auth()

    unsigned = await client.get(f"{CLIP_URL_BASE}/{clip_id}")
    assert unsigned.status == HTTPStatus.UNAUTHORIZED

    signed = async_sign_path(
        hass, f"{CLIP_URL_BASE}/{clip_id}", CLIP_TTL, use_content_user=True
    )
    response = await client.get(signed)
    assert response.status == HTTPStatus.OK
    assert response.content_type == "audio/wav"
    assert await response.read() == audio


async def test_signature_is_bound_to_one_clip(
    hass: HomeAssistant, hass_client_no_auth, setup_integration
) -> None:
    """A signature issued for one clip cannot be reused to read another."""
    store: ClipStore = hass.data[DOMAIN]
    mine = store.add(make_wav(b"a" * 64))
    someone_elses = store.add(make_wav(b"b" * 64))
    client = await hass_client_no_auth()

    signed = async_sign_path(
        hass, f"{CLIP_URL_BASE}/{mine}", CLIP_TTL, use_content_user=True
    )
    tampered = signed.replace(mine, someone_elses)

    assert (await client.get(tampered)).status == HTTPStatus.UNAUTHORIZED


async def test_unknown_clip_is_not_found(
    hass: HomeAssistant, hass_client_no_auth, setup_integration
) -> None:
    """An expired or unknown clip returns 404 even with a valid signature."""
    client = await hass_client_no_auth()
    signed = async_sign_path(
        hass, f"{CLIP_URL_BASE}/gone", CLIP_TTL, use_content_user=True
    )

    assert (await client.get(signed)).status == HTTPStatus.NOT_FOUND


async def test_card_is_served(
    hass: HomeAssistant, hass_client, setup_integration
) -> None:
    """The dashboard card is reachable at the URL registered with the frontend."""
    client = await hass_client()
    response = await client.get(CARD_URL)

    assert response.status == HTTPStatus.OK
    assert "voice-broadcast-card" in await response.text()
