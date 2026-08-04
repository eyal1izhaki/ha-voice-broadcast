"""The WebSocket command that broadcasts a recorded clip to speakers."""

from __future__ import annotations

import asyncio
import base64
import binascii
import logging
from typing import Any

import voluptuous as vol
from homeassistant.auth.permissions.const import POLICY_CONTROL
from homeassistant.components import websocket_api
from homeassistant.components.http.auth import async_sign_path
from homeassistant.components.media_player import (
    ATTR_MEDIA_ANNOUNCE,
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    SERVICE_PLAY_MEDIA,
    MediaPlayerEntityFeature,
    MediaType,
)
from homeassistant.components.media_player import (
    DOMAIN as MEDIA_PLAYER_DOMAIN,
)
from homeassistant.components.websocket_api.const import (
    ERR_INVALID_FORMAT,
    ERR_NOT_SUPPORTED,
    ERR_UNAUTHORIZED,
)
from homeassistant.const import ATTR_ENTITY_ID, ATTR_SUPPORTED_FEATURES
from homeassistant.core import Context, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .clips import ClipStore
from .const import CLIP_TTL, CLIP_URL_BASE, CONF_BASE_URL, DOMAIN, MAX_CLIP_BYTES

_LOGGER = logging.getLogger(__name__)


@callback
def async_register(hass: HomeAssistant) -> None:
    """Register the Voice Broadcast WebSocket API."""
    websocket_api.async_register_command(hass, handle_broadcast)


@websocket_api.websocket_command(
    {
        vol.Required("type"): f"{DOMAIN}/broadcast",
        vol.Required("audio"): str,
        vol.Required("entity_id"): vol.All(
            cv.ensure_list,
            vol.Length(min=1),
            [cv.entity_domain(MEDIA_PLAYER_DOMAIN)],
        ),
    }
)
@websocket_api.async_response
async def handle_broadcast(
    hass: HomeAssistant,
    connection: websocket_api.ActiveConnection,
    msg: dict[str, Any],
) -> None:
    """Play a recorded clip on the requested speakers.

    Deliberately not decorated with @websocket_api.require_admin. Letting
    non-admin users broadcast is the entire reason this integration exists:
    Home Assistant's own media upload endpoint is admin-only.

    Home Assistant enforces entity policy inside its own service call handlers,
    not in hass.services.async_call, so the per-entity check below is ours to
    make and is what keeps a restricted user restricted.
    """
    entity_ids: list[str] = msg["entity_id"]
    user = connection.user

    for entity_id in entity_ids:
        if not user.permissions.check_entity(entity_id, POLICY_CONTROL):
            connection.send_error(
                msg["id"], ERR_UNAUTHORIZED, f"Not allowed to control {entity_id}"
            )
            return

    try:
        audio = base64.b64decode(msg["audio"], validate=True)
    except (binascii.Error, ValueError):
        connection.send_error(
            msg["id"], ERR_INVALID_FORMAT, "audio is not valid base64"
        )
        return

    if len(audio) > MAX_CLIP_BYTES:
        connection.send_error(
            msg["id"],
            ERR_INVALID_FORMAT,
            f"clip is larger than the {MAX_CLIP_BYTES} byte limit",
        )
        return

    # Only ever serve back something that really is a WAV file, so that an
    # authenticated user cannot park arbitrary bytes behind a Home Assistant URL.
    if len(audio) < 44 or audio[:4] != b"RIFF" or audio[8:12] != b"WAVE":
        connection.send_error(msg["id"], ERR_INVALID_FORMAT, "clip is not a WAV file")
        return

    store: ClipStore = hass.data[DOMAIN]
    clip_id = store.add(audio)

    try:
        url = _clip_url(hass, clip_id)
    except NoURLAvailableError:
        connection.send_error(
            msg["id"],
            ERR_NOT_SUPPORTED,
            "No Home Assistant URL is configured for speakers to fetch audio from",
        )
        return

    # Logged because "the speaker went idle" almost always means it could not
    # fetch this URL. Fetching it by hand is the fastest way to tell.
    _LOGGER.debug("Broadcasting %d bytes to %s from %s", len(audio), entity_ids, url)

    context = Context(user_id=user.id)
    results = await asyncio.gather(
        *(_async_play(hass, entity_id, url, context) for entity_id in entity_ids)
    )
    targets = dict(zip(entity_ids, results, strict=True))
    connection.send_result(msg["id"], {"targets": targets})


@callback
def _base_url(hass: HomeAssistant) -> str:
    """Return the address speakers should fetch audio from.

    Home Assistant's own URL is right for most installs, but it is chosen for
    browsers, not speakers: behind a reverse proxy the Internal URL is often an
    address the speakers cannot reach. The option is the escape hatch for that.
    """
    for entry in hass.config_entries.async_entries(DOMAIN):
        if override := entry.options.get(CONF_BASE_URL):
            return str(override).rstrip("/")
    return get_url(hass)


@callback
def _clip_url(hass: HomeAssistant, clip_id: str) -> str:
    """Build an absolute, signed, short-lived URL for a clip.

    Signed against the internal content user rather than the caller, so a
    broadcast keeps playing even if the person who sent it logs out or has their
    token revoked mid-playback. The signature expires with the clip itself, so a
    URL can never outlive the audio it points at.
    """
    signed = async_sign_path(
        hass, f"{CLIP_URL_BASE}/{clip_id}.wav", CLIP_TTL, use_content_user=True
    )
    return f"{_base_url(hass)}{signed}"


async def _async_play(
    hass: HomeAssistant, entity_id: str, url: str, context: Context
) -> dict[str, Any]:
    """Play the clip on one speaker, reporting failures rather than raising."""
    if (state := hass.states.get(entity_id)) is None:
        return {"ok": False, "error": "unknown entity"}

    features = state.attributes.get(ATTR_SUPPORTED_FEATURES) or 0
    announce = bool(features & MediaPlayerEntityFeature.MEDIA_ANNOUNCE)

    data: dict[str, Any] = {
        ATTR_ENTITY_ID: entity_id,
        ATTR_MEDIA_CONTENT_ID: url,
        ATTR_MEDIA_CONTENT_TYPE: MediaType.MUSIC,
    }
    if announce:
        # Players advertising MEDIA_ANNOUNCE duck and restore whatever was
        # playing by themselves, which covers Music Assistant, Cast, Sonos and
        # HomePod. Anything else is simply interrupted, by design.
        data[ATTR_MEDIA_ANNOUNCE] = True

    try:
        await hass.services.async_call(
            MEDIA_PLAYER_DOMAIN,
            SERVICE_PLAY_MEDIA,
            data,
            blocking=True,
            context=context,
        )
    except HomeAssistantError as err:
        return {"ok": False, "error": str(err)}

    return {"ok": True, "announced": announce}
