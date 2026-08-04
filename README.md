# Voice Broadcast

Hold a button in your Home Assistant dashboard, speak, and your voice plays on the speakers you pick.

Any Home Assistant user can use it — including non-admin accounts, which is the whole reason this
integration exists (see [Why an integration at all](#why-an-integration-at-all)).

- One HACS install, one click to add. The card registers itself; there is no Lovelace resource to paste.
- No ffmpeg, no Docker, no Python audio libraries, no npm. Zero runtime dependencies beyond Home Assistant.
- Speakers that support announcements duck and restore your music by themselves.
- Recordings are never written to disk.

## Requires HTTPS

**Browsers only allow microphone access over a secure connection.** `http://homeassistant.local:8123` and
`http://192.168.x.x:8123` do *not* count, and the card will tell you so instead of failing silently.

You need one of:

- Home Assistant Cloud (Nabu Casa), or
- your own HTTPS reverse proxy / certificate, or
- `http://localhost:8123` on the machine itself.

This is a browser rule, not something this integration can work around.

## Install

1. Add this repository to HACS, install **Voice Broadcast**, and restart Home Assistant.

   [![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=eyal1izhaki&repository=ha-voice-broadcast&category=integration)

   If the button does not work, open HACS, use the ⋮ menu → **Custom repositories**, and add
   `https://github.com/eyal1izhaki/ha-voice-broadcast` with category **Integration**.

2. Add the integration. There is nothing to configure.

   [![Open your Home Assistant instance and start setting up a new integration.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=voice_broadcast)

   Or go to **Settings → Devices & Services → Add Integration → Voice Broadcast**.

3. Edit a dashboard, **Add Card → Voice Broadcast**, and choose which speakers the card may reach.

Everyone who can see that dashboard can now broadcast to those speakers.

## Card options

| Option | Default | Description |
| --- | --- | --- |
| `entities` | *required* | The `media_player` entities this card is allowed to reach. Whoever presses the button chooses which of them to talk to. |
| `title` | – | Card header. |
| `names` | – | Custom label per speaker, keyed by entity id. Values may be [templates](#speaker-labels). |
| `chime` | `true` | Play a short two-tone chime before your voice so people look up. |
| `volume_control` | `true` | Show a volume slider for each selected speaker. |
| `max_seconds` | `30` | Recording stops automatically at this length. |

```yaml
type: custom:voice-broadcast-card
title: Broadcast
entities:
  - media_player.kitchen
  - media_player.living_room
names:
  media_player.kitchen: Kitchen
  media_player.living_room: "Lounge — {{ states('sensor.lounge_temperature') }}°"
chime: true
```

### Speaker labels

By default each speaker shows its own friendly name. `names` overrides that, and a value containing `{{` or
`{%` is rendered as a Home Assistant template that updates live:

```yaml
names:
  media_player.bedroom: "Bedroom{{ ' (asleep)' if is_state('binary_sensor.bedroom_occupied','on') else '' }}"
```

Plain labels are used as-is with no template subscription, so there is no cost to using `names` for simple
renaming. If a template fails or has not rendered yet, the speaker falls back to its friendly name rather than
showing raw Jinja or going blank.

Labels are keyed by entity id rather than nested inside `entities` so that the visual card editor cannot
silently drop them when you change the speaker list.

## How it works

1. The card captures raw microphone PCM, downsamples it to 16 kHz mono, prepends the chime, and writes a WAV
   header — all in the browser. This is why no server-side audio conversion (and no ffmpeg) is needed.
2. It sends the clip over Home Assistant's existing authenticated WebSocket connection, so there is no new
   upload endpoint and no token for you to manage.
3. The integration validates the audio, holds it in memory, and hands each speaker a **signed, short-lived
   URL** to fetch it from.
4. It calls the standard `media_player.play_media` service, adding `announce: true` for any player that
   advertises `MEDIA_ANNOUNCE`. That covers Music Assistant, Chromecast, Sonos and HomePod, which then duck
   and restore whatever was playing on their own.

Players *without* announce support are simply interrupted; the clip plays and nothing is restored. Restoring
prior playback means guessing clip durations and snapshotting state, which is where the complexity in
comparable projects lives, so it is deliberately left out.

## Why an integration at all

Home Assistant has no native way to broadcast recorded audio. `tts.speak` synthesizes text, Assist's
microphone feeds speech-to-text into a conversation agent, and `assist_satellite.announce` is text-only —
none of them carry a recording to a speaker.

Home Assistant *does* have a media upload endpoint, but `/api/media_source/local_source/upload` is
`@require_admin`. That single decorator locks out exactly the people an intercom is for: kids, partners and
guests. Routing around it is all this integration does; everything else is stock Home Assistant.

## Security

- Broadcasting requires an authenticated Home Assistant user. Non-admin is allowed by design, and each
  target entity is checked against the user's own `control` permission — Home Assistant only enforces entity
  policy inside its own service handlers, so the integration does that check itself.
- Speakers fetch clips using signed, path-bound URLs that expire with the clip. There is no unauthenticated
  endpoint, and nothing is written to `www/` or left publicly readable.
- Uploads are size-capped and verified to actually be WAV files, so the endpoint cannot be used to host
  arbitrary content. Clip ids come from `secrets`, so they are not enumerable.
- Clips live only in memory, capped in both count and age, and are gone on restart.

## Not included

Hardware push-to-talk buttons, a standalone/Docker mode, restoring playback on non-announce speakers,
text-to-speech, and clip history. If you want those, [`mdj2812/home-intercom`](https://github.com/mdj2812/home-intercom)
is a more featureful take on the same idea.

## Development

```bash
pip install -r requirements-dev.txt
pytest
```

## License

MIT
