"""In-memory storage and delivery of recorded voice clips."""

from __future__ import annotations

import secrets
from collections import OrderedDict
from datetime import datetime
from http import HTTPStatus

from aiohttp import web
from homeassistant.components.http import HomeAssistantView
from homeassistant.util import dt as dt_util

from .const import CLIP_TTL, CLIP_URL_BASE, MAX_CLIPS


class ClipStore:
    """Bounded, in-memory store of recorded clips.

    Clips are deliberately never written to disk. They are played once and then
    forgotten, so holding the last few in memory avoids a cleanup job, avoids
    filesystem permissions entirely, and keeps voice recordings out of the
    user's media browser.
    """

    def __init__(self) -> None:
        """Initialise an empty store."""
        self._clips: OrderedDict[str, tuple[datetime, bytes]] = OrderedDict()

    def add(self, audio: bytes) -> str:
        """Store a clip and return its unguessable id."""
        self._drop_expired()
        clip_id = secrets.token_urlsafe(16)
        self._clips[clip_id] = (dt_util.utcnow(), audio)
        while len(self._clips) > MAX_CLIPS:
            self._clips.popitem(last=False)
        return clip_id

    def get(self, clip_id: str) -> bytes | None:
        """Return a stored clip, or None if it is unknown or expired."""
        self._drop_expired()
        if (clip := self._clips.get(clip_id)) is None:
            return None
        return clip[1]

    def __len__(self) -> int:
        """Return the number of live clips."""
        self._drop_expired()
        return len(self._clips)

    def _drop_expired(self) -> None:
        """Discard clips older than CLIP_TTL.

        Insertion order is age order, so this can stop at the first live clip.
        """
        cutoff = dt_util.utcnow() - CLIP_TTL
        for clip_id, (created, _) in list(self._clips.items()):
            if created > cutoff:
                break
            del self._clips[clip_id]


class ClipView(HomeAssistantView):
    """Serve a recorded clip to a speaker.

    Authentication is required, as for any Home Assistant view. Speakers cannot
    send an Authorization header, so they are handed a signed URL instead: the
    HTTP auth middleware validates that signature against the exact path it was
    issued for, which also stops a signature for one clip being replayed
    against another.
    """

    url = f"{CLIP_URL_BASE}/{{clip_id}}"
    name = "api:voice_broadcast:clip"

    def __init__(self, store: ClipStore) -> None:
        """Keep a reference to the clip store."""
        self._store = store

    async def get(self, request: web.Request, clip_id: str) -> web.Response:
        """Return the audio for a clip."""
        if (audio := self._store.get(clip_id)) is None:
            return web.Response(status=HTTPStatus.NOT_FOUND)
        return web.Response(body=audio, content_type="audio/wav")
