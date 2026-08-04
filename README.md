# Voice Broadcast

Hold a button in your Home Assistant dashboard, speak, and your voice plays on your speakers.

Any Home Assistant user can use it — including non-admin accounts, which is the whole reason this
integration exists (see [Why an integration at all](#why-an-integration-at-all)).

- One HACS install, one click to add. The card registers itself; there is no Lovelace resource to paste.
- Two layouts: a full card with a row per speaker, or a minimal button-and-volume strip.
- Every option accepts a Home Assistant template and updates live.
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

## Cards

Two card types are registered. They are the same card with a different default layout, so every option below
applies to both.

| Type | Layout |
| --- | --- |
| `custom:voice-broadcast-card` | A row per speaker with its name and volume, then the talk button. |
| `custom:voice-broadcast-mini-card` | One line: talk button plus a single volume slider for all speakers. |

**Which speakers can be reached is fixed in the card configuration.** Pressing the button always broadcasts to
every entity in `entities` — there is no runtime picker. Give people a second card if they need a second set of
speakers.

### Options

| Option | Default | Template? | Description |
| --- | --- | --- | --- |
| `entities` | *required* | yes | The `media_player` entities this card broadcasts to. |
| `title` | – | yes | Card header. |
| `names` | – | yes, per entry | Custom label per speaker, keyed by entity id. |
| `layout` | `full` | no | `full` or `minimal`. Changes the DOM, so it is not templatable. |
| `chime` | `true` | yes | Play a short two-tone chime before your voice so people look up. |
| `volume_control` | `true` | yes | Show volume control. |
| `max_seconds` | `30` | yes | Recording stops automatically at this length. |

```yaml
type: custom:voice-broadcast-card
title: Broadcast
entities:
  - media_player.kitchen
  - media_player.living_room
names:
  media_player.kitchen: Kitchen
  media_player.living_room: "Lounge — {{ states('sensor.lounge_temperature') }}°"
chime: "{{ now().hour < 22 }}"
```

```yaml
type: custom:voice-broadcast-mini-card
entities: [media_player.kitchen]
```

### Templates

Any option marked *Template?* above may be given as a Jinja template instead of a literal. A value containing
`{{` or `{%` is subscribed to Home Assistant's template renderer and updates live:

```yaml
# Quiet hours: no chime late at night
chime: "{{ now().hour >= 7 and now().hour < 22 }}"

# Skip the bedroom while someone is asleep in there
entities: >
  {{ ['media_player.kitchen'] if is_state('binary_sensor.bedroom_occupied', 'on')
     else ['media_player.kitchen', 'media_player.bedroom'] }}

# Label that reacts to state
names:
  media_player.bedroom: "Bedroom{{ ' (asleep)' if is_state('binary_sensor.bedroom_occupied','on') else '' }}"
```

Notes:

- Plain values are used as-is with **no** template subscription, so templating costs nothing when unused.
- A template that fails or has not rendered yet falls back to the literal value — or, for `names`, to the
  entity's own friendly name — rather than showing raw Jinja or going blank.
- Booleans accept real booleans as well as `true/false`, `on/off`, `yes/no`, `1/0`.
- An `entities` template may render a list or a comma-separated string. Anything that is not a
  `media_player` entity is dropped.
- Templated `entities` do not weaken anything: the integration still checks the pressing user's own `control`
  permission for every entity it is asked to play on.
- In the visual editor, an option holding a template is shown as a text field instead of its usual picker, so
  the picker cannot overwrite your template.
- `names` is keyed by entity id rather than nested inside `entities` so the editor cannot silently drop labels
  when you change the speaker list.

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

## Troubleshooting: the speaker goes idle and nothing plays

Almost always this means the speaker could not fetch the clip. **The speaker has to reach Home Assistant over
the network by itself** — your browser being able to reach it is not enough.

By default speakers are given the address from **Settings → System → Network**, preferring the Internal URL.
Behind a reverse proxy that address is often wrong: Home Assistant may only listen on `localhost`, so the
`http://<lan-ip>:8123` it advertises is not actually served.

A Chromecast reports this in the Home Assistant log as:

```
Failed to cast media http://<your-ha-address>:8123/api/voice_broadcast/clip/….wav … from internal_url (…)
```

To diagnose:

1. **Call `tts.speak` to the same speaker.** Text-to-speech resolves its URL exactly the same way, so if TTS
   also fails the problem is the address, not this integration.
2. **Enable debug logging** to see the URL that was handed out, then fetch it yourself:

   ```yaml
   logger:
     logs:
       custom_components.voice_broadcast: debug
   ```

To fix, either correct the Internal URL in Settings → System → Network, or tell the integration to hand out a
different one of your configured addresses in **Settings → Devices & Services → Voice Broadcast → Configure**:

| Choice | Effect |
| --- | --- |
| Automatic | `get_url()` decides, preferring the Internal URL. The default. |
| External URL only | Use the External URL (including a Nabu Casa address). |
| Internal URL only | Use the Internal URL and never fall back. |

The addresses themselves stay in Settings → System → Network — this only picks which one speakers are given.
Both explicit choices are strict rather than falling back, so a wrong pick shows an error instead of quietly
reproducing the problem.

Other things that stop a speaker playing:

- **A certificate the speaker does not trust.** Chromecasts reject self-signed and private-CA certificates.
- **The speaker on a different VLAN or guest network** from Home Assistant, so it cannot route to it at all.
- **DNS.** The hostname has to resolve from the *speaker's* DNS, which split-horizon setups often break.

Note that the card cannot detect this: `media_player.play_media` returns successfully and the speaker fails
afterwards, asynchronously. Cast does not expose its idle reason as an entity attribute, so the failure only
appears in the Home Assistant log — the card will say it is playing.

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
