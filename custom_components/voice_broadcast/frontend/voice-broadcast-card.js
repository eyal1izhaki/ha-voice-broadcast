/**
 * Voice Broadcast card: hold to talk, and your voice plays on the speakers you pick.
 *
 * Recording is done here rather than server-side so that the integration needs
 * no audio dependencies at all. The browser captures raw PCM, this file writes a
 * WAV header around it, and Home Assistant only ever relays finished bytes.
 */

const SAMPLE_RATE = 16000; // Voice-grade mono: small payloads, plays everywhere.
const VOLUME_SET = 4; // MediaPlayerEntityFeature.VOLUME_SET
const MIN_SECONDS = 0.2; // Below this it was a mis-tap, not a message.

const DEFAULTS = {
  chime: true,
  volume_control: true,
  max_seconds: 30,
};

const SCHEMA = [
  { name: "title", selector: { text: {} } },
  {
    name: "entities",
    required: true,
    selector: { entity: { domain: "media_player", multiple: true } },
  },
  { name: "names", selector: { object: {} } },
  { name: "chime", selector: { boolean: {} } },
  { name: "volume_control", selector: { boolean: {} } },
  {
    name: "max_seconds",
    selector: {
      number: { min: 5, max: 120, step: 5, mode: "slider", unit_of_measurement: "s" },
    },
  },
];

const LABELS = {
  title: "Title",
  entities: "Speakers this card can reach",
  names: "Custom speaker labels, keyed by entity id (templates allowed)",
  chime: "Play a chime before your voice",
  volume_control: "Show a volume slider for selected speakers",
  max_seconds: "Maximum recording length",
};

/** Whether a configured label needs rendering by Home Assistant rather than
 *  being shown as-is. Plain labels skip the subscription entirely. */
function isTemplate(value) {
  return typeof value === "string" && (value.includes("{{") || value.includes("{%"));
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
        0.3 * Math.sin((2 * Math.PI * freq * i) / SAMPLE_RATE) * Math.exp((-4 * i) / samples);
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
    for (let i = 0; i < string.length; i++) view.setUint8(offset + i, string.charCodeAt(i));
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
    this._selected = new Set();
    this._recording = false;
    this._dragging = null;
    this._rendered = {};
    this._unsubscribers = [];
    this._generation = 0;
  }

  setConfig(config) {
    const entities = (config.entities ?? []).map((e) => (typeof e === "string" ? e : e.entity));
    if (entities.some((id) => !id?.startsWith("media_player."))) {
      throw new Error("Only media_player entities can be used");
    }
    const { names } = config;
    if (names !== undefined && (typeof names !== "object" || Array.isArray(names))) {
      throw new Error("names must be a mapping of entity id to label");
    }
    // An empty list is allowed rather than fatal, so a freshly added card shows
    // a hint instead of an error while it is still being configured.
    this._config = { ...DEFAULTS, ...config, entities };
    this._selected = new Set(entities);

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
    return 3;
  }

  /* ---------------------------------------------------------------- rendering */

  _build() {
    const style = document.createElement("style");
    style.textContent = `
      .content { padding: 16px; display: flex; flex-direction: column; gap: 12px; }
      .warn {
        background: var(--warning-color, #ffa726); color: var(--text-primary-color, #fff);
        border-radius: 8px; padding: 8px 12px; font-size: 14px;
      }
      .targets { display: flex; flex-wrap: wrap; gap: 8px; }
      .chip {
        font: inherit; font-size: 14px; cursor: pointer;
        border: 1px solid var(--divider-color); border-radius: 16px; padding: 6px 14px;
        background: transparent; color: var(--primary-text-color);
      }
      .chip[data-on="true"] {
        background: var(--primary-color); color: var(--text-primary-color, #fff);
        border-color: var(--primary-color);
      }
      .chip[data-available="false"] { opacity: 0.45; text-decoration: line-through; }
      .volumes { display: flex; flex-direction: column; gap: 6px; }
      .volume { display: flex; align-items: center; gap: 10px; font-size: 13px;
        color: var(--secondary-text-color); }
      .volume span { flex: 0 0 40%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
      .volume input { flex: 1; accent-color: var(--primary-color); }
      .ptt {
        font: inherit; font-size: 17px; font-weight: 500; cursor: pointer;
        padding: 22px; border: none; border-radius: 12px; touch-action: none;
        user-select: none; -webkit-user-select: none; -webkit-tap-highlight-color: transparent;
        background: var(--primary-color); color: var(--text-primary-color, #fff);
        transition: background 120ms ease, transform 120ms ease;
      }
      .ptt:disabled { opacity: 0.5; cursor: default; }
      .ptt[data-recording="true"] { background: var(--error-color, #db4437); transform: scale(0.99); }
      .status { min-height: 18px; font-size: 13px; color: var(--secondary-text-color); }
      .status[data-error="true"] { color: var(--error-color, #db4437); }
    `;

    const card = document.createElement("ha-card");
    if (this._config.title) card.setAttribute("header", this._config.title);
    card.innerHTML = `
      <div class="content">
        <div class="warn" hidden></div>
        <div class="targets"></div>
        <div class="volumes"></div>
        <button class="ptt" type="button">Hold to talk</button>
        <div class="status"></div>
      </div>
    `;

    this.shadowRoot.append(style, card);
    this._root = card;
    this._warn = card.querySelector(".warn");
    this._targets = card.querySelector(".targets");
    this._volumes = card.querySelector(".volumes");
    this._button = card.querySelector(".ptt");
    this._statusEl = card.querySelector(".status");

    this._blocked = true;
    if (!window.isSecureContext) {
      this._warn.hidden = false;
      this._warn.textContent =
        "Your browser blocks the microphone on insecure connections. Open Home Assistant over HTTPS to record audio.";
    } else if (!this._config.entities.length) {
      this._warn.hidden = false;
      this._warn.textContent = "Pick the speakers this card may reach in the card editor.";
    } else {
      this._blocked = false;
    }
    this._button.disabled = this._blocked;

    for (const entityId of this._config.entities) {
      const chip = document.createElement("button");
      chip.className = "chip";
      chip.type = "button";
      chip.dataset.entity = entityId;
      chip.addEventListener("click", () => this._toggle(entityId));
      this._targets.append(chip);
    }

    this._button.addEventListener("pointerdown", (ev) => {
      ev.preventDefault();
      this._button.setPointerCapture(ev.pointerId);
      this._start();
    });
    this._button.addEventListener("pointerup", () => this._stop());
    this._button.addEventListener("pointercancel", () => this._stop());
    this._button.addEventListener("contextmenu", (ev) => ev.preventDefault());

    this._renderVolumes();
    this._subscribeTemplates();
  }

  _sync() {
    for (const chip of this._targets.children) {
      const entityId = chip.dataset.entity;
      const state = this._hass.states[entityId];
      chip.textContent = this._name(entityId);
      chip.dataset.on = String(this._selected.has(entityId));
      chip.dataset.available = String(Boolean(state) && state.state !== "unavailable");
    }
    for (const row of this._volumes.children) {
      const entityId = row.dataset.entity;
      row.querySelector("span").textContent = this._name(entityId);
      const level = this._hass.states[entityId]?.attributes.volume_level;
      if (this._dragging !== entityId && level != null) {
        row.querySelector("input").value = String(Math.round(level * 100));
      }
    }
  }

  _renderVolumes() {
    this._volumes.innerHTML = "";
    if (!this._config.volume_control) return;

    for (const entityId of this._config.entities) {
      const state = this._hass.states[entityId];
      if (!this._selected.has(entityId) || !state) continue;
      if (!((state.attributes.supported_features ?? 0) & VOLUME_SET)) continue;

      const row = document.createElement("div");
      row.className = "volume";
      row.dataset.entity = entityId;

      const label = document.createElement("span");
      label.textContent = this._name(entityId);

      const slider = document.createElement("input");
      slider.type = "range";
      slider.min = "0";
      slider.max = "100";
      slider.value = String(Math.round((state.attributes.volume_level ?? 0) * 100));
      slider.addEventListener("pointerdown", () => (this._dragging = entityId));
      // Released without moving it: stop holding state updates back.
      slider.addEventListener("pointerup", () => (this._dragging = null));
      slider.addEventListener("change", () => {
        this._dragging = null;
        this._hass.callService("media_player", "volume_set", {
          entity_id: entityId,
          volume_level: Number(slider.value) / 100,
        });
      });

      row.append(label, slider);
      this._volumes.append(row);
    }
  }

  _toggle(entityId) {
    if (this._selected.has(entityId)) this._selected.delete(entityId);
    else this._selected.add(entityId);
    this._renderVolumes();
    this._sync();
  }

  _name(entityId) {
    const configured = this._config.names?.[entityId];
    // A plain label is used directly. A template shows the entity's own name
    // until Home Assistant sends the first render, so the raw Jinja is never
    // displayed and a broken template degrades instead of blanking the chip.
    if (configured && !isTemplate(configured)) return configured;
    if (this._rendered[entityId]) return this._rendered[entityId];
    return this._hass.states[entityId]?.attributes.friendly_name ?? entityId;
  }

  /** Subscribe to Home Assistant's template renderer for any templated label.
   *
   * render_template is not admin-only, so this works for the non-admin family
   * members the card is built for.
   */
  async _subscribeTemplates() {
    this._unsubscribeTemplates();
    const generation = this._generation;

    for (const [entityId, template] of Object.entries(this._config.names ?? {})) {
      if (!isTemplate(template) || !this._config.entities.includes(entityId)) continue;

      try {
        const unsubscribe = await this._hass.connection.subscribeMessage(
          (message) => {
            this._rendered[entityId] = message.error ? undefined : String(message.result);
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
        // Leave the fallback name in place rather than breaking the card.
        console.warn(`voice-broadcast: could not render name for ${entityId}`, err);
      }
    }
  }

  _unsubscribeTemplates() {
    this._generation += 1;
    for (const unsubscribe of this._unsubscribers) unsubscribe();
    this._unsubscribers = [];
    this._rendered = {};
  }

  _status(message, isError = false) {
    this._statusEl.textContent = message;
    this._statusEl.dataset.error = String(isError);
  }

  /* ---------------------------------------------------------------- recording */

  async _start() {
    if (this._recording || this._button.disabled) return;
    if (!this._selected.size) {
      this._status("Pick at least one speaker first.", true);
      return;
    }

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

    processor.onaudioprocess = (event) => {
      if (!this._recording) return;
      this._chunks.push(new Float32Array(event.inputBuffer.getChannelData(0)));
      this._recorded += event.inputBuffer.length;
      const seconds = this._recorded / this._context.sampleRate;
      this._status(`Recording… ${seconds.toFixed(1)}s`);
      if (seconds >= this._config.max_seconds) this._stop();
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
    if (this._config.chime) samples = concat(chime(), samples);

    await this._send(encodeWav(samples));
  }

  async _send(wav) {
    const entityIds = [...this._selected];
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

/* -------------------------------------------------------------------------- */
/* Editor                                                                     */
/* -------------------------------------------------------------------------- */

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
      // ha-form gives us Home Assistant's own entity picker and controls, so the
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
    this._form.schema = SCHEMA;
    this._form.data = this._config;
  }
}

customElements.define("voice-broadcast-card", VoiceBroadcastCard);
customElements.define("voice-broadcast-card-editor", VoiceBroadcastCardEditor);

window.customCards = window.customCards ?? [];
window.customCards.push({
  type: "voice-broadcast-card",
  name: "Voice Broadcast",
  description: "Hold to talk and broadcast your voice to the speakers you choose.",
  documentationURL: "https://github.com/eyal1izhaki/ha-voice-broadcast",
});
