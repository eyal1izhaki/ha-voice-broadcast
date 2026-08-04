"""Constants for the Voice Broadcast integration."""

from __future__ import annotations

from datetime import timedelta

DOMAIN = "voice_broadcast"

# A clip only has to outlive the few seconds a speaker needs to fetch it. The
# signed URL handed to the speaker expires on the same schedule, so a link can
# never outlive the audio it points at.
CLIP_TTL = timedelta(minutes=5)
MAX_CLIPS = 5

# 2 MiB of 16 kHz mono 16-bit audio is a little over a minute of speech, and
# stays clear of the 4 MiB WebSocket frame limit once base64 inflates it by a
# third. The card enforces a matching duration cap before it ever sends.
MAX_CLIP_BYTES = 2 * 1024 * 1024

CLIP_URL_BASE = "/api/voice_broadcast/clip"
CARD_URL = "/voice_broadcast/voice-broadcast-card.js"
