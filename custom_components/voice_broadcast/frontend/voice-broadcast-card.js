/**
 * Voice Broadcast card: hold to talk, and your voice plays on the configured speakers.
 *
 * Recording happens here rather than server-side so the integration needs no
 * audio dependencies at all. The browser captures raw PCM, this file writes a
 * WAV header around it, and Home Assistant only ever relays finished bytes.
 *
 * Which speakers can be reached is fixed in the card configuration. Every other
 * option may be given as a Home Assistant template and is re-rendered live.
 */

// Keep in step with the version in manifest.json. Logged on load so you can
// confirm which build the browser is actually running.
const CARD_VERSION = "0.3.0";

const SAMPLE_RATE = 16000; // Voice-grade mono: small payloads, plays everywhere.
const VOLUME_SET = 4; // MediaPlayerEntityFeature.VOLUME_SET
const MIN_SECONDS = 0.2; // Below this it was a mis-tap, not a message.

const DEFAULTS = {
  chime: true,
  volume_control: true,
  max_seconds: 30,
  layout: "full",
};

/** Options that accept a template. `layout` is excluded on purpose: it decides
 *  the DOM structure, and having it change under a live recording is worse than
 *  the flexibility is worth. */
const TEMPLATABLE = ["title", "entities", "chime", "volume_control", "max_seconds"];

const LABELS = {
  title: "Title",
  entities: "Speakers this card broadcasts to",
  names: "Custom speaker labels, keyed by entity id (templates allowed)",
  layout: "Layout",
  chime: "Play a chime before your voice",
  volume_control: "Show volume control",
  max_seconds: "Maximum recording length",
};

/** Whether a configured value needs rendering by Home Assistant rather than
 *  being used as-is. Plain values skip the subscription entirely. */
function isTemplate(value) {
  return typeof value === "string" && (value.includes("{{") || value.includes("{%"));
}

function asBoolean(value, fallback) {
  if (typeof value === "boolean") return value;
  if (typeof value === "number") return value !== 0;
  if (typeof value === "string") {
    const text = value.trim().toLowerCase();
    if (["true", "on", "yes", "1"].includes(text)) return true;
    if (["false", "off", "no", "0", "none", ""].includes(text)) return false;
  }
  return fallback;
}

function asNumber(value, fallback) {
  const number = typeof value === "number" ? value : Number.parseFloat(value);
  return Number.isFinite(number) ? number : fallback;
}

/** Templates may render a real list or a comma-separated string. */
function toEntityList(value) {
  const raw = Array.isArray(value)
    ? value
    : typeof value === "string"
      ? value.split(",")
      : [];
  return raw
    .map((entry) => String(typeof entry === "string" ? entry : entry?.entity ?? "").trim())
    .filter((id) => id.startsWith("media_player."));
}

/* -------------------------------------------------------------------------- */
/* Audio helpers                                                              */
/* -------------------------------------------------------------------------- */

/** Average-downsample to SAMPLE_RATE. The box filter is a crude but adequate
 *  anti-alias step for speech, and avoids pulling in a resampling library. */
function resample(input, inputRate) {
  if (inputRate === SAMPLE_RATE) return input;
  const ratio = inputRate / SAMPLE_RATE;
  const output = new Float32Array(Math.floor(input.length / ratio));
  for (let i = 0; i < output.length; i++) {
    const start = Math.floor(i * ratio);
    const end = Math.min(input.length, Math.floor((i + 1) * ratio));
    let sum = 0;
    for (let j = start; j < end; j++) sum += input[j];
    output[i] = sum / Math.max(1, end - start);
  }
  return output;
}

/** A two-tone doorbell ding, generated rather than shipped as an asset. Being
 *  part of the same clip means one play_media call and no gap before speech. */
function chime() {
  const notes = [
    [880, 0.1],
    [1245, 0.18],
  ];
  const tail = Math.round(SAMPLE_RATE * 0.05);
  const length =
    notes.reduce((n, [, seconds]) => n + Math.round(SAMPLE_RATE * seconds), 0) + tail;
  const out = new Float32Array(length);
  let offset = 0;
  for (const [freq, seconds] of notes) {
    const samples = Math.round(SAMPLE_RATE * seconds);
    for (let i = 0; i < samples; i++) {
      // Exponential decay makes it a soft ding rather than a click.
      out[offset + i] =
        0.3 *
        Math.sin((2 * Math.PI * freq * i) / SAMPLE_RATE) *
        Math.exp((-4 * i) / samples);
    }
    offset += samples;
  }
  return out;
}

function concat(a, b) {
  const out = new Float32Array(a.length + b.length);
  out.set(a, 0);
  out.set(b, a.length);
  return out;
}

/** Wrap Float32 samples in a 16-bit PCM WAV container. */
function encodeWav(samples) {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const text = (offset, string) => {
    for (let i = 0; i < string.length; i++) {
      view.setUint8(offset + i, string.charCodeAt(i));
    }
  };

  text(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  text(8, "WAVE");
  text(12, "fmt ");
  view.setUint32(16, 16, true); // PCM header size
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, SAMPLE_RATE, true);
  view.setUint32(28, SAMPLE_RATE * 2, true); // byte rate
  view.setUint16(32, 2, true); // block align
  view.setUint16(34, 16, true); // bits per sample
  text(36, "data");
  view.setUint32(40, samples.length * 2, true);

  let offset = 44;
  for (const sample of samples) {
    const clamped = Math.max(-1, Math.min(1, sample));
    view.setInt16(offset, clamped < 0 ? clamped * 0x8000 : clamped * 0x7fff, true);
    offset += 2;
  }
  return new Uint8Array(buffer);
}

/** Chunked so a long clip cannot blow the argument stack. */
function toBase64(bytes) {
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

/* -------------------------------------------------------------------------- */
/* Card                                                                       */
/* -------------------------------------------------------------------------- */

class VoiceBroadcastCard extends HTMLElement {
  static getConfigElement() {
    return document.createElement("voice-broadcast-card-editor");
  }

  static getStubConfig(hass) {
    const first = Object.keys(hass.states).find((id) => id.startsWith("media_player."));
    return { entities: first ? [first] : [] };
  }

  constructor() {
    super();
    this.attachShadow({ mode: "open" });
    this._recording = false;
    this._sending = false;
    this._dragging = false;
    this._rendered = {};
    this._unsubscribers = [];
    this._generation = 0;
    this._rowsKey = null;
  }

  setConfig(config) {
    // A literal list is validated now; a templated one can only be checked once
    // it renders, so toEntityList filters it at that point instead.
    if (!isTemplate(config.entities)) {
      const given = config.entities ?? [];
      if (!Array.isArray(given)) {
        throw new Error("entities must be a list of media_player entities");
      }
      const ids = given.map((e) => (typeof e === "string" ? e : e?.entity));
      if (ids.some((id) => !id?.startsWith("media_player."))) {
        throw new Error("Only media_player entities can be used");
      }
    }
    const { names } = config;
    if (names !== undefined && (typeof names !== "object" || Array.isArray(names))) {
      throw new Error("names must be a mapping of entity id to label");
    }

    this._config = { ...DEFAULTS, ...config };
    this._rowsKey = null;

    // Rebuild from scratch so editing the card updates its preview immediately.
    this.shadowRoot.innerHTML = "";
    this._root = null;
    if (this._hass) {
      this._build();
      this._sync();
    }
  }

  set hass(hass) {
    this._hass = hass;
    if (!this._root) this._build();
    this._sync();
  }

  getCardSize() {
    return this._config?.layout === "minimal" ? 1 : 3;
  }

  /* ------------------------------------------------------------- templates */

  /** Every templated option, flattened to `key -> template`. Nested speaker
   *  labels are keyed as `names.<entity_id>`. */
  _bindings() {
    const bindings = {};
    for (const key of TEMPLATABLE) {
      if (isTemplate(this._config[key])) bindings[key] = this._config[key];
    }
    for (const [entityId, label] of Object.entries(this._config.names ?? {})) {
      if (isTemplate(label)) bindings[`names.${entityId}`] = label;
    }
    return bindings;
  }

  /** Subscribe to Home Assistant's template renderer for every templated option.
   *
   * render_template is not admin-only, so this works for the non-admin family
   * members the card is built for.
   */
  async _subscribeTemplates() {
    this._unsubscribeTemplates();
    const generation = this._generation;

    for (const [key, template] of Object.entries(this._bindings())) {
      try {
        const unsubscribe = await this._hass.connection.subscribeMessage(
          (message) => {
            this._rendered[key] = message.error ? undefined : message.result;
            if (this._root) this._sync();
          },
          { type: "render_template", template, report_errors: true }
        );

        // The card may have been reconfigured or removed while awaiting.
        if (generation !== this._generation) {
          unsubscribe();
          return;
        }
        this._unsubscribers.push(unsubscribe);
      } catch (err) {
        // Leave the literal fallback in place rather than breaking the card.
        console.warn(`voice-broadcast: could not render template for ${key}`, err);
      }
    }
  }

  _unsubscribeTemplates() {
    this._generation += 1;
    for (const unsubscribe of this._unsubscribers) unsubscribe();
    this._unsubscribers = [];
    this._rendered = {};
  }

  /** A rendered template result if one has arrived, else the literal config. */
  _value(key, literal) {
    return key in this._rendered && this._rendered[key] !== undefined
      ? this._rendered[key]
      : literal;
  }

  _entityIds() {
    return toEntityList(this._value("entities", this._config.entities ?? []));
  }

  _title() {
    const title = this._value("title", this._config.title);
    return title == null ? "" : String(title);
  }

  _chimeEnabled() {
    return asBoolean(this._value("chime", this._config.chime), DEFAULTS.chime);
  }

  _volumeEnabled() {
    return asBoolean(
      this._value("volume_control", this._config.volume_control),
      DEFAULTS.volume_control
    );
  }

  _maxSeconds() {
    return asNumber(
      this._value("max_seconds", this._config.max_seconds),
      DEFAULTS.max_seconds
    );
  }

  _name(entityId) {
    const configured = this._config.names?.[entityId];
    // A plain label is used directly. A template shows the entity's own name
    // until Home Assistant sends the first render, so raw Jinja is never shown
    // and a broken template degrades instead of blanking the row.
    if (configured && !isTemplate(configured)) return String(configured);
    const rendered = this._rendered[`names.${entityId}`];
    if (rendered !== undefined && rendered !== "") return String(rendered);
    return this._hass.states[entityId]?.attributes.friendly_name ?? entityId;
  }

  /* ------------------------------------------------------------- rendering */

  get _minimal() {
    return this._config.layout === "minimal";
  }

  _supportsVolume(entityId) {
    const features = this._hass.states[entityId]?.attributes.supported_features ?? 0;
    return Boolean(features & VOLUME_SET);
  }

  _build() {
    const style = document.createElement("style");
    style.textContent = `
      .content { padding: 16px; display: flex; flex-direction: column; gap: 12px; }
      .content.minimal { padding: 12px; flex-direction: row; align-items: center; gap: 12px; }
      .warn {
        background: var(--warning-color, #ffa726); color: var(--text-primary-color, #fff);
        border-radius: 8px; padding: 8px 12px; font-size: 14px;
      }
      .content.minimal .warn { flex: 1; padding: 6px 10px; font-size: 13px; }
      .rows { display: flex; flex-direction: column; gap: 8px; }
      .row { display: flex; align-items: center; gap: 10px; font-size: 14px;
        color: var(--primary-text-color); }
      .row[data-available="false"] { opacity: 0.45; text-decoration: line-through; }
      .row span { flex: 0 0 40%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      input[type="range"] { flex: 1; min-width: 0; accent-color: var(--primary-color); }
      .ptt {
        font: inherit; font-size: 17px; font-weight: 500; cursor: pointer;
        padding: 22px; border: none; border-radius: 12px; touch-action: none;
        user-select: none; -webkit-user-select: none; -webkit-tap-highlight-color: transparent;
        background: var(--primary-color); color: var(--text-primary-color, #fff);
        transition: background 120ms ease, transform 120ms ease;
      }
      .ptt.minimal { flex: 1; padding: 14px; font-size: 15px; border-radius: 24px; }
      .ptt:disabled { opacity: 0.5; cursor: default; }
      .ptt[data-recording="true"] { background: var(--error-color, #db4437); transform: scale(0.99); }
      .status { min-height: 18px; font-size: 13px; color: var(--secondary-text-color); }
      .status[data-error="true"] { color: var(--error-color, #db4437); }
      .content.minimal .status { display: none; }
      .mini-volume { flex: 0 0 35%; }
    `;

    const card = document.createElement("ha-card");
    const title = this._title();
    if (title) card.setAttribute("header", title);

    card.innerHTML = this._minimal
      ? `
        <div class="content minimal">
          <div class="warn" hidden></div>
          <button class="ptt minimal" type="button">Hold to talk</button>
          <input class="mini-volume" type="range" min="0" max="100" hidden />
          <div class="status"></div>
        </div>
      `
      : `
        <div class="content">
          <div class="warn" hidden></div>
          <div class="rows"></div>
          <button class="ptt" type="button">Hold to talk</button>
          <div class="status"></div>
        </div>
      `;

    this.shadowRoot.append(style, card);
    this._root = card;
    this._warn = card.querySelector(".warn");
    this._rows = card.querySelector(".rows");
    this._button = card.querySelector(".ptt");
    this._statusEl = card.querySelector(".status");
    this._miniVolume = card.querySelector(".mini-volume");

    this._button.addEventListener("pointerdown", (ev) => {
      ev.preventDefault();
      this._button.setPointerCapture(ev.pointerId);
      this._start();
    });
    this._button.addEventListener("pointerup", () => this._stop());
    this._button.addEventListener("pointercancel", () => this._stop());
    this._button.addEventListener("contextmenu", (ev) => ev.preventDefault());

    if (this._miniVolume) {
      this._miniVolume.addEventListener("pointerdown", () => (this._dragging = true));
      this._miniVolume.addEventListener("pointerup", () => (this._dragging = false));
      this._miniVolume.addEventListener("change", () => {
        this._dragging = false;
        this._setVolume(this._volumeTargets(), Number(this._miniVolume.value) / 100);
      });
    }

    this._subscribeTemplates();
  }

  _sync() {
    const entityIds = this._entityIds();
    this._refreshRows(entityIds);
    this._refreshBlocked(entityIds);

    const title = this._title();
    if (title) this._root.setAttribute("header", title);
    else this._root.removeAttribute("header");

    if (this._rows) {
      for (const row of this._rows.children) {
        const entityId = row.dataset.entity;
        const state = this._hass.states[entityId];
        row.querySelector("span").textContent = this._name(entityId);
        row.dataset.available = String(Boolean(state) && state.state !== "unavailable");
        const slider = row.querySelector("input");
        const level = state?.attributes.volume_level;
        if (slider && this._dragging !== entityId && level != null) {
          slider.value = String(Math.round(level * 100));
        }
      }
    }

    if (this._miniVolume) {
      const targets = this._volumeTargets();
      this._miniVolume.hidden = !this._volumeEnabled() || targets.length === 0;
      const average = this._averageVolume(targets);
      if (!this._dragging && average != null) {
        this._miniVolume.value = String(Math.round(average * 100));
      }
    }
  }

  /** Rebuild the per-speaker rows only when the resolved speaker list changes,
   *  so a state update never yanks a slider out from under a dragging finger. */
  _refreshRows(entityIds) {
    if (!this._rows) return;
    const key = `${this._volumeEnabled()}|${entityIds.join(",")}`;
    if (key === this._rowsKey) return;
    this._rowsKey = key;
    this._rows.innerHTML = "";

    for (const entityId of entityIds) {
      const row = document.createElement("div");
      row.className = "row";
      row.dataset.entity = entityId;

      const label = document.createElement("span");
      label.textContent = this._name(entityId);
      row.append(label);

      if (this._volumeEnabled() && this._supportsVolume(entityId)) {
        const slider = document.createElement("input");
        slider.type = "range";
        slider.min = "0";
        slider.max = "100";
        slider.value = String(
          Math.round((this._hass.states[entityId]?.attributes.volume_level ?? 0) * 100)
        );
        slider.addEventListener("pointerdown", () => (this._dragging = entityId));
        slider.addEventListener("pointerup", () => (this._dragging = false));
        slider.addEventListener("change", () => {
          this._dragging = false;
          this._setVolume([entityId], Number(slider.value) / 100);
        });
        row.append(slider);
      }

      this._rows.append(row);
    }
  }

  _refreshBlocked(entityIds) {
    const insecure = !window.isSecureContext;
    this._blocked = insecure || entityIds.length === 0;

    // Ordered by how much the user can do about it. The last error is kept until
    // the next attempt so a state update cannot wipe it off the screen.
    const warning = insecure
      ? "Your browser blocks the microphone on insecure connections. Open Home Assistant over HTTPS to record audio."
      : !entityIds.length
        ? "No speakers configured for this card."
        : this._errorMessage;

    this._warn.hidden = !warning;
    if (warning) this._warn.textContent = warning;

    if (!this._recording) this._button.disabled = this._blocked || this._sending;
  }

  _volumeTargets() {
    return this._entityIds().filter((id) => this._supportsVolume(id));
  }

  _averageVolume(entityIds) {
    const levels = entityIds
      .map((id) => this._hass.states[id]?.attributes.volume_level)
      .filter((level) => level != null);
    if (!levels.length) return null;
    return levels.reduce((total, level) => total + level, 0) / levels.length;
  }

  _setVolume(entityIds, volumeLevel) {
    if (!entityIds.length) return;
    this._hass.callService("media_player", "volume_set", {
      entity_id: entityIds,
      volume_level: volumeLevel,
    });
  }

  _status(message, isError = false) {
    this._statusEl.textContent = message;
    this._statusEl.dataset.error = String(isError);
    // The minimal layout has no room for a status line, so errors surface in the
    // warning strip. _refreshBlocked keeps them there until the next attempt.
    if (isError) {
      this._errorMessage = message;
      if (this._minimal) {
        this._warn.hidden = false;
        this._warn.textContent = message;
      }
    }
  }

  /* ------------------------------------------------------------- recording */

  async _start() {
    if (this._recording || this._button.disabled) return;

    this._errorMessage = null;
    this._pressed = true;
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
    } catch (err) {
      this._pressed = false;
      this._status(`Microphone unavailable: ${err.message}`, true);
      return;
    }

    // Granting permission can take a while on first use, and the button may
    // already have been released by the time it resolves.
    if (!this._pressed) {
      stream.getTracks().forEach((track) => track.stop());
      return;
    }

    this._stream = stream;
    this._context = new AudioContext();
    this._chunks = [];
    this._recorded = 0;
    this._recording = true;

    const source = this._context.createMediaStreamSource(this._stream);
    // ScriptProcessorNode is deprecated, but it needs no separate worklet file,
    // which keeps this card a single dependency-free module. The muted gain node
    // exists only because the processor does not run unless it reaches a
    // destination — it must never play the speaker's own voice back at them.
    const processor = this._context.createScriptProcessor(4096, 1, 1);
    const muted = this._context.createGain();
    muted.gain.value = 0;
    const maxSeconds = this._maxSeconds();

    processor.onaudioprocess = (event) => {
      if (!this._recording) return;
      this._chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)));
      this._recorded += event.inputBuffer.length;
      const seconds = this._recorded / this._context.sampleRate;
      this._status(`Recording… ${seconds.toFixed(1)}s`);
      if (seconds >= maxSeconds) this._stop();
    };

    source.connect(processor);
    processor.connect(muted);
    muted.connect(this._context.destination);

    this._button.dataset.recording = "true";
    this._button.textContent = "Release to send";
    this._status("Recording…");
  }

  async _stop() {
    this._pressed = false;
    if (!this._recording) return;
    this._recording = false;
    this._button.dataset.recording = "false";
    this._button.textContent = "Hold to talk";

    const inputRate = this._context.sampleRate;
    const chunks = this._chunks;
    this._teardown();

    const total = chunks.reduce((n, chunk) => n + chunk.length, 0);
    if (total < inputRate * MIN_SECONDS) {
      this._status("Too short — hold the button while you speak.", true);
      return;
    }

    const raw = new Float32Array(total);
    let offset = 0;
    for (const chunk of chunks) {
      raw.set(chunk, offset);
      offset += chunk.length;
    }

    let samples = resample(raw, inputRate);
    if (this._chimeEnabled()) samples = concat(chime(), samples);

    await this._send(encodeWav(samples));
  }

  async _send(wav) {
    const entityIds = this._entityIds();
    this._sending = true;
    this._button.disabled = true;
    this._status("Sending…");

    try {
      const result = await this._hass.callWS({
        type: "voice_broadcast/broadcast",
        audio: toBase64(wav),
        entity_id: entityIds,
      });
      const failed = Object.entries(result.targets).filter(([, r]) => !r.ok);
      if (failed.length) {
        this._status(
          failed.map(([id, r]) => `${this._name(id)}: ${r.error}`).join(" · "),
          true
        );
      } else {
        this._status(`Playing on ${entityIds.map((id) => this._name(id)).join(", ")}`);
      }
    } catch (err) {
      this._status(err.message ?? "Broadcast failed", true);
    } finally {
      this._sending = false;
      this._button.disabled = this._blocked;
    }
  }

  _teardown() {
    this._stream?.getTracks().forEach((track) => track.stop());
    this._stream = null;
    this._context?.close();
    this._context = null;
    this._chunks = null;
  }

  disconnectedCallback() {
    this._recording = false;
    this._teardown();
    this._unsubscribeTemplates();
  }
}

/** The same card, compact by default: one button and one volume slider. An
 *  explicit `layout` still wins, so this is only a different default. */
class VoiceBroadcastMiniCard extends VoiceBroadcastCard {
  setConfig(config) {
    super.setConfig({ layout: "minimal", ...config });
  }
}

/* -------------------------------------------------------------------------- */
/* Editor                                                                     */
/* -------------------------------------------------------------------------- */

/** Any templatable option falls back to a plain text field once it holds a
 *  template, so the native picker cannot overwrite the template with its own
 *  idea of the value. */
function buildSchema(config) {
  const field = (name, selector) =>
    isTemplate(config?.[name]) ? { name, selector: { text: {} } } : { name, selector };

  return [
    field("title", { text: {} }),
    field("entities", { entity: { domain: "media_player", multiple: true } }),
    { name: "names", selector: { object: {} } },
    {
      name: "layout",
      selector: {
        select: {
          mode: "dropdown",
          options: [
            { value: "full", label: "Full — a row per speaker" },
            { value: "minimal", label: "Minimal — button and volume only" },
          ],
        },
      },
    },
    field("chime", { boolean: {} }),
    field("volume_control", { boolean: {} }),
    field("max_seconds", {
      number: { min: 5, max: 120, step: 5, mode: "slider", unit_of_measurement: "s" },
    }),
  ];
}

class VoiceBroadcastCardEditor extends HTMLElement {
  setConfig(config) {
    this._config = { ...DEFAULTS, ...config };
    this._render();
  }

  set hass(hass) {
    this._hass = hass;
    this._render();
  }

  _render() {
    if (!this._hass || !this._config) return;

    if (!this._form) {
      // ha-form gives us Home Assistant's own pickers and controls, so the
      // editor looks and behaves like a built-in card's.
      this._form = document.createElement("ha-form");
      this._form.computeLabel = (schema) => LABELS[schema.name] ?? schema.name;
      this._form.addEventListener("value-changed", (event) => {
        this.dispatchEvent(
          new CustomEvent("config-changed", { detail: { config: event.detail.value } })
        );
      });
      this.append(this._form);
    }

    this._form.hass = this._hass;
    this._form.schema = buildSchema(this._config);
    this._form.data = this._config;
  }
}

console.info(
  `%c VOICE-BROADCAST-CARD %c ${CARD_VERSION} `,
  "color: white; background: #03a9f4; font-weight: 700;",
  "color: #03a9f4; background: white; font-weight: 700;"
);

customElements.define("voice-broadcast-card", VoiceBroadcastCard);
customElements.define("voice-broadcast-mini-card", VoiceBroadcastMiniCard);
customElements.define("voice-broadcast-card-editor", VoiceBroadcastCardEditor);

window.customCards = window.customCards ?? [];
window.customCards.push(
  {
    type: "voice-broadcast-card",
    name: "Voice Broadcast",
    description: "Hold to talk and broadcast your voice to your speakers.",
    documentationURL: "https://github.com/eyal1izhaki/ha-voice-broadcast",
  },
  {
    type: "voice-broadcast-mini-card",
    name: "Voice Broadcast (minimal)",
    description: "Compact push-to-talk button with a single volume slider.",
    documentationURL: "https://github.com/eyal1izhaki/ha-voice-broadcast",
  }
);
