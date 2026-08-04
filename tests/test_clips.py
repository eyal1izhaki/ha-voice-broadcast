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


def signed_clip_url(hass: HomeAssistant, clip_id: str) -> str:
    """Return the signed URL a speaker would be handed for a clip."""
    return async_sign_path(
        hass, f"{CLIP_URL_BASE}/{clip_id}.wav", CLIP_TTL, use_content_user=True
    )


async def test_clip_requires_a_signature(
    hass: HomeAssistant, hass_client_no_auth, setup_integration
) -> None:
    """Speakers can fetch a clip with a signed URL, but not without one."""
    store: ClipStore = hass.data[DOMAIN]
    audio = make_wav()
    clip_id = store.add(audio)
    client = await hass_client_no_auth()

    unsigned = await client.get(f"{CLIP_URL_BASE}/{clip_id}.wav")
    assert unsigned.status == HTTPStatus.UNAUTHORIZED

    response = await client.get(signed_clip_url(hass, clip_id))
    assert response.status == HTTPStatus.OK
    assert response.content_type == "audio/wav"
    assert response.headers["Accept-Ranges"] == "bytes"
    assert await response.read() == audio


async def test_range_request_gets_partial_content(
    hass: HomeAssistant, hass_client_no_auth, setup_integration
) -> None:
    """Speakers that probe with a byte range need a 206, not the whole file."""
    store: ClipStore = hass.data[DOMAIN]
    audio = make_wav(bytes(range(200)) * 2)
    clip_id = store.add(audio)
    client = await hass_client_no_auth()

    response = await client.get(
        signed_clip_url(hass, clip_id), headers={"Range": "bytes=10-49"}
    )

    assert response.status == HTTPStatus.PARTIAL_CONTENT
    assert response.headers["Content-Range"] == f"bytes 10-49/{len(audio)}"
    assert await response.read() == audio[10:50]


async def test_open_ended_range_runs_to_the_end(
    hass: HomeAssistant, hass_client_no_auth, setup_integration
) -> None:
    """An open-ended range serves the remainder of the clip."""
    store: ClipStore = hass.data[DOMAIN]
    audio = make_wav()
    clip_id = store.add(audio)
    client = await hass_client_no_auth()

    response = await client.get(
        signed_clip_url(hass, clip_id), headers={"Range": "bytes=44-"}
    )

    assert response.status == HTTPStatus.PARTIAL_CONTENT
    assert await response.read() == audio[44:]


async def test_range_beyond_the_clip_is_rejected(
    hass: HomeAssistant, hass_client_no_auth, setup_integration
) -> None:
    """A range starting past the end returns 416 rather than empty audio."""
    store: ClipStore = hass.data[DOMAIN]
    audio = make_wav()
    clip_id = store.add(audio)
    client = await hass_client_no_auth()

    response = await client.get(
        signed_clip_url(hass, clip_id), headers={"Range": f"bytes={len(audio) + 5}-"}
    )

    assert response.status == HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE
    assert response.headers["Content-Range"] == f"bytes */{len(audio)}"


async def test_head_reports_the_clip_without_a_body(
    hass: HomeAssistant, hass_client_no_auth, setup_integration
) -> None:
    """Speakers that probe with HEAD get the headers and no audio."""
    store: ClipStore = hass.data[DOMAIN]
    audio = make_wav()
    clip_id = store.add(audio)
    client = await hass_client_no_auth()

    response = await client.head(signed_clip_url(hass, clip_id))

    assert response.status == HTTPStatus.OK
    assert response.content_type == "audio/wav"
    assert response.headers["Content-Length"] == str(len(audio))
    assert await response.read() == b""


async def test_signature_is_bound_to_one_clip(
    hass: HomeAssistant, hass_client_no_auth, setup_integration
) -> None:
    """A signature issued for one clip cannot be reused to read another."""
    store: ClipStore = hass.data[DOMAIN]
    mine = store.add(make_wav(b"a" * 64))
    someone_elses = store.add(make_wav(b"b" * 64))
    client = await hass_client_no_auth()

    tampered = signed_clip_url(hass, mine).replace(mine, someone_elses)

    assert (await client.get(tampered)).status == HTTPStatus.UNAUTHORIZED


async def test_unknown_clip_is_not_found(
    hass: HomeAssistant, hass_client_no_auth, setup_integration
) -> None:
    """An expired or unknown clip returns 404 even with a valid signature."""
    client = await hass_client_no_auth()

    assert (await client.get(signed_clip_url(hass, "gone"))).status == (
        HTTPStatus.NOT_FOUND
    )


async def test_card_is_served(
    hass: HomeAssistant, hass_client, setup_integration
) -> None:
    """The dashboard card is reachable at the URL registered with the frontend."""
    client = await hass_client()
    response = await client.get(CARD_URL)

    assert response.status == HTTPStatus.OK
    assert "voice-broadcast-card" in await response.text()
