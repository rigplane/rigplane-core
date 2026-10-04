/**
 * RX Audio Player — decodes Opus/PCM16 frames and plays via AudioContext.
 *
 * Graph (per #753):
 *   source → preGain(volume) → ChannelSplitter(2)
 *                                 ├─ [0] → mainGain → mainPanner → destination
 *                                 └─ [1] → subGain  → subPanner  → destination
 *
 * - MAIN/SUB focus and stereo split are controlled via setFocus /
 *   setSplitStereo / setChannelGainDb (wired from AudioManager.setAudioConfig).
 * - For mono input (no dual-watch) the splitter's channel [1] is silent per
 *   the WebAudio spec — SUB audio simply doesn't contribute, which is correct.
 * - ``volume`` preserves its old semantics as the pre-routing level.
 */

import {
  CODEC_OPUS,
  CODEC_PCM16,
  SAMPLE_RATE,
  parseRxHeader,
} from './constants';

export type RxAudioFocus = 'main' | 'sub' | 'both';

export interface RxPlaybackDiagnostics {
  schemaVersion: 1;
  contextState: 'unavailable' | 'running' | 'suspended' | 'closed' | 'interrupted' | 'unknown';
  sampleRate: number | null;
  receivedFrames: number;
  validFrames: number;
  decodedFrames: number;
  pcmPeak: number | null;
  scheduledFrames: number;
  suspendedDrops: number;
  resumeOutcome: 'not_attempted' | 'pending' | 'resolved' | 'rejected';
  lastResumeError: string | null;
}

function emptyPlayback(): RxPlaybackDiagnostics {
  return {
    schemaVersion: 1, contextState: 'unavailable', sampleRate: null,
    receivedFrames: 0, validFrames: 0, decodedFrames: 0, pcmPeak: null,
    scheduledFrames: 0, suspendedDrops: 0, resumeOutcome: 'not_attempted', lastResumeError: null,
  };
}

const MAX_COUNTER = 0xffffffff;
const RESUME_ERROR_NAMES = new Set([
  'NotAllowedError', 'InvalidStateError', 'NotSupportedError', 'AbortError', 'SecurityError', 'UnknownError',
]);

function dbToLinear(db: number): number {
  if (!Number.isFinite(db) || db <= -80) return 0;
  return Math.pow(10, db / 20);
}

export class RxPlayer {
  private ctx: AudioContext | null = null;
  private preGain: GainNode | null = null;
  private mainGain: GainNode | null = null;
  private subGain: GainNode | null = null;
  private mainPanner: StereoPannerNode | null = null;
  private subPanner: StereoPannerNode | null = null;
  private splitter: ChannelSplitterNode | null = null;
  private decoder: AudioDecoder | null = null;
  private nextPlayTime = 0;
  private opusTs = 0;
  // Codec byte of the previously fed frame — detects a mid-stream
  // adaptive egress switch (MOR-588, ADR §3.6).
  private lastCodec: number | null = null;
  private _volume = 1.0;

  // Routing state — applied whenever any of the nodes exist.
  private _focus: RxAudioFocus = 'both';
  private _splitStereo = false;
  private _mainGainDb = 0;
  private _subGainDb = 0;

  // Jitter buffer bounds (seconds) — defaults match env_config defaults
  // (50 ms floor / 300 ms ceiling). Configurable via setJitterBounds().
  private _floorSec = 0.05;
  private _ceilingSec = 0.30;

  // Count of frames dropped because the context was suspended (autoplay
  // policy). Surfaced via console.warn so a silent no-audio state is
  // observable instead of invisible. Reset once the context resumes.
  private _droppedSuspendedFrames = 0;
  private _resumeListenersAttached = false;

  // Link-quality counters for the periodic audio_stats uplink (MOR-585,
  // ADR §3.6). Real playback underruns (the jitter buffer ran dry while
  // playing — NOT the priming rebase after start()/flush()) plus all
  // frames the player dropped (suspended context, jitter ceiling).
  // Cumulative since start(); reset on stop().
  private _underruns = 0;
  private _droppedFrames = 0;
  private _playback = emptyPlayback();
  private _resumeAttempt = 0;

  get volume(): number {
    return this._volume;
  }

  set volume(v: number) {
    this._volume = Math.max(0, Math.min(1, v));
    if (this.preGain) this.preGain.gain.value = this._volume;
  }

  get focus(): RxAudioFocus {
    return this._focus;
  }

  get splitStereo(): boolean {
    return this._splitStereo;
  }

  get mainGainDb(): number {
    return this._mainGainDb;
  }

  get subGainDb(): number {
    return this._subGainDb;
  }

  setFocus(focus: RxAudioFocus): void {
    if (focus !== 'main' && focus !== 'sub' && focus !== 'both') return;
    this._focus = focus;
    this._applyGraphState();
  }

  setSplitStereo(on: boolean): void {
    this._splitStereo = !!on;
    this._applyGraphState();
  }

  setChannelGainDb(channel: 'main' | 'sub', db: number): void {
    if (channel === 'main') this._mainGainDb = db;
    else if (channel === 'sub') this._subGainDb = db;
    this._applyGraphState();
  }

  /** Configure jitter buffer bounds. Call before first feed() — typically after
   *  capabilities are fetched. Values must be in milliseconds (positive integers). */
  setJitterBounds(floorMs: number, ceilingMs: number): void {
    this._floorSec = floorMs / 1000;
    this._ceilingSec = ceilingMs / 1000;
  }

  get active(): boolean {
    return this.ctx !== null && this.ctx.state !== 'closed';
  }

  /** Start (or resume) playback.
   *
   * MUST be called synchronously from a user-gesture handler (e.g. the LIVE
   * button click): WKWebView / mobile-Safari autoplay policy only honours
   * ``AudioContext.resume()`` when it runs inside the gesture's call stack.
   * Calling again on an existing context is a cheap idempotent resume — the
   * AudioManager re-invokes start() on ``audio_start`` and reconnect to keep
   * nudging a context that re-entered ``suspended``.
   */
  start(): void {
    if (this.ctx) {
      this._resume();
      return;
    }
    const Ctx = globalThis.AudioContext ?? (globalThis as any).webkitAudioContext;
    if (!Ctx) return;
    this.ctx = new Ctx({ sampleRate: SAMPLE_RATE });
    this.preGain = this.ctx.createGain();
    this.preGain.gain.value = this._volume;
    this.mainGain = this.ctx.createGain();
    this.subGain = this.ctx.createGain();
    this.mainPanner = this.ctx.createStereoPanner();
    this.subPanner = this.ctx.createStereoPanner();
    this.splitter = this.ctx.createChannelSplitter(2);
    // Wire up the graph.
    this.preGain.connect(this.splitter);
    this.splitter.connect(this.mainGain, 0);
    this.splitter.connect(this.subGain, 1);
    this.mainGain.connect(this.mainPanner);
    this.subGain.connect(this.subPanner);
    this.mainPanner.connect(this.ctx.destination);
    this.subPanner.connect(this.ctx.destination);
    this._applyGraphState();
    this.nextPlayTime = 0;
    this._attachResumeListeners();
    this._resume();
  }

  stop(): void {
    this._resumeAttempt++;
    this._playback = emptyPlayback();
    this.resetDecoder();
    this.lastCodec = null;
    this._detachResumeListeners();
    if (this.ctx) {
      this.ctx.close().catch(() => {});
      this.ctx = null;
      this.preGain = null;
      this.mainGain = null;
      this.subGain = null;
      this.mainPanner = null;
      this.subPanner = null;
      this.splitter = null;
    }
    this.nextPlayTime = 0;
    this.opusTs = 0;
    this._droppedSuspendedFrames = 0;
    this._underruns = 0;
    this._droppedFrames = 0;
  }

  /** Link-quality snapshot for the periodic audio_stats uplink (MOR-585).
   *  bufferDepthMs is the audio currently scheduled ahead of playback —
   *  the live jitter-buffer depth; 0 when idle or stopped. */
  stats(): { underruns: number; bufferDepthMs: number; droppedFrames: number; playback: RxPlaybackDiagnostics } {
    let depthMs = 0;
    if (this.ctx) {
      const depth = (this.nextPlayTime - this.ctx.currentTime) * 1000;
      if (depth > 0) depthMs = Math.round(depth);
    }
    return {
      underruns: this._underruns,
      bufferDepthMs: depthMs,
      droppedFrames: this._droppedFrames,
      playback: {
        ...this._playback,
        contextState: this.ctx === null ? 'unavailable'
          : ['running', 'suspended', 'closed', 'interrupted'].includes(this.ctx.state)
            ? this.ctx.state as RxPlaybackDiagnostics['contextState'] : 'unknown',
        sampleRate: this.ctx && Number.isFinite(this.ctx.sampleRate)
          && this.ctx.sampleRate > 0 && this.ctx.sampleRate <= 384000
          ? this.ctx.sampleRate : null,
      },
    };
  }

  /** Best-effort resume of a suspended context. Safe to call repeatedly. */
  private _resume(): void {
    if (this.ctx && this.ctx.state === 'suspended') {
      const context = this.ctx;
      const attempt = ++this._resumeAttempt;
      this._playback.resumeOutcome = 'pending';
      this._playback.lastResumeError = null;
      context.resume().then(() => {
        if (this.ctx !== context || this._resumeAttempt !== attempt) return;
        this._playback.resumeOutcome = 'resolved';
        this._playback.lastResumeError = null;
      }, (error: unknown) => {
        if (this.ctx !== context || this._resumeAttempt !== attempt) return;
        const name = error && typeof error === 'object' && 'name' in error ? error.name : null;
        this._playback.resumeOutcome = 'rejected';
        this._playback.lastResumeError = typeof name === 'string' && RESUME_ERROR_NAMES.has(name)
          ? name : 'UnknownError';
      });
    }
  }

  /** Resume-on-regain-focus handler: WKWebView re-suspends an AudioContext
   *  when the webview is backgrounded; resume it when we come back. Bound as
   *  an arrow property so add/removeEventListener see a stable reference. */
  private _onResumeEvent = (): void => {
    this._resume();
  };

  private _attachResumeListeners(): void {
    if (this._resumeListenersAttached) return;
    if (typeof document !== 'undefined') {
      document.addEventListener('visibilitychange', this._onResumeEvent);
    }
    if (typeof window !== 'undefined') {
      window.addEventListener('focus', this._onResumeEvent);
    }
    this._resumeListenersAttached = true;
  }

  private _detachResumeListeners(): void {
    if (!this._resumeListenersAttached) return;
    if (typeof document !== 'undefined') {
      document.removeEventListener('visibilitychange', this._onResumeEvent);
    }
    if (typeof window !== 'undefined') {
      window.removeEventListener('focus', this._onResumeEvent);
    }
    this._resumeListenersAttached = false;
  }

  /** Returns true when frames can be scheduled (context running). When the
   *  context is suspended this re-attempts resume() and warns — rather than
   *  silently dropping every frame, which previously made a stuck-suspended
   *  context (no sound, blank scope) impossible to diagnose. */
  private _ensureRunning(): boolean {
    if (!this.ctx) return false;
    if (this.ctx.state === 'running') {
      this._droppedSuspendedFrames = 0;
      return true;
    }
    if (this.ctx.state === 'suspended') {
      this._resume();
      this._droppedSuspendedFrames++;
      this._droppedFrames++;
      this._playback.suspendedDrops = Math.min(MAX_COUNTER, this._playback.suspendedDrops + 1);
      if (this._droppedSuspendedFrames <= 3 || this._droppedSuspendedFrames % 200 === 0) {
        console.warn(
          `RxPlayer: AudioContext suspended — dropped ${this._droppedSuspendedFrames} ` +
          `frame(s), retrying resume(). Audio needs a user gesture (click LIVE).`,
        );
      }
    }
    return false;
  }

  /** Feed a raw binary frame from WS */
  feed(buffer: ArrayBuffer): void {
    this._playback.receivedFrames = Math.min(MAX_COUNTER, this._playback.receivedFrames + 1);
    const hdr = parseRxHeader(buffer);
    if (!hdr) return;
    if ((hdr.codec === CODEC_PCM16 || hdr.codec === CODEC_OPUS)
      && (hdr.channels === 1 || hdr.channels === 2) && hdr.sampleRate > 0 && hdr.payload.byteLength > 0
      && (hdr.codec !== CODEC_PCM16 || hdr.payload.byteLength % (2 * hdr.channels) === 0)) {
      this._playback.validFrames = Math.min(MAX_COUNTER, this._playback.validFrames + 1);
    }

    if (this.lastCodec !== null && hdr.codec !== this.lastCodec) {
      // Mid-stream adaptive egress switch (MOR-588, ADR §3.6): the
      // server starts a FRESH Opus encoder stream on every PCM16↔Opus
      // switch, so the WebCodecs decoder must be re-initialised —
      // feeding a new stream into stale decoder state yields garbage.
      // The playback schedule (nextPlayTime) is intentionally NOT
      // rebased: decoded frames keep landing on the same timeline, so
      // the switch stays seamless.
      this.resetDecoder();
    }
    this.lastCodec = hdr.codec;

    if (hdr.codec === CODEC_PCM16) {
      this.playPcm16(hdr.payload, hdr.sampleRate, hdr.channels);
    } else if (hdr.codec === CODEC_OPUS) {
      this.decodeOpus(hdr.payload, hdr.sampleRate, hdr.channels);
    }
  }

  /** Close the Opus decoder (if any) and restart its timestamp clock —
   *  the next Opus frame lazily configures a fresh decoder. */
  private resetDecoder(): void {
    if (this.decoder) {
      try { this.decoder.close(); } catch { /* ok */ }
      this.decoder = null;
    }
    this.opusTs = 0;
  }

  /** Flush pipeline (e.g. on reconnect) */
  flush(): void {
    this.nextPlayTime = 0;
  }

  // ── PCM16 playback ──

  private playPcm16(payload: Uint8Array, sr: number, ch: number): void {
    if (!this.ctx || !this.preGain) return;
    if (!this._ensureRunning()) return;
    const channels = ch === 2 ? 2 : 1;
    const frameCount = Math.floor(payload.byteLength / (2 * channels));
    if (frameCount <= 0) return;

    const int16 = new Int16Array(payload.buffer, payload.byteOffset, frameCount * channels);
    const buf = this.ctx.createBuffer(channels, frameCount, sr > 0 ? sr : SAMPLE_RATE);
    for (let c = 0; c < channels; c++) {
      const data = buf.getChannelData(c);
      for (let i = 0; i < frameCount; i++) {
        data[i] = int16[i * channels + c] / 32768.0;
      }
    }
    if ((ch === 1 || ch === 2) && sr > 0 && payload.byteLength === frameCount * channels * 2) {
      this._recordDecoded(buf);
    }
    this.schedule(buf);
  }

  // ── Opus decode ──

  private decodeOpus(payload: Uint8Array, sr: number, ch: number): void {
    if (!this.ctx || !this.preGain) return;
    if (!this._ensureRunning()) return;
    if (typeof AudioDecoder === 'undefined') return;

    if (!this.decoder) {
      const ctx = this.ctx;
      this.opusTs = 0;
      this.decoder = new AudioDecoder({
        output: (audioData: AudioData) => {
          const frames = audioData.numberOfFrames;
          const numCh = audioData.numberOfChannels;
          const buf = ctx.createBuffer(numCh, frames, audioData.sampleRate);
          for (let c = 0; c < numCh; c++) {
            const data = buf.getChannelData(c);
            audioData.copyTo(data, { planeIndex: c, format: 'f32-planar' });
          }
          if (this.ctx === ctx) this._recordDecoded(buf);
          this.schedule(buf);
          audioData.close();
        },
        error: (err: DOMException) => {
          console.warn('RxPlayer: AudioDecoder error', err);
          this.decoder = null;
        },
      });
      this.decoder.configure({
        codec: 'opus',
        sampleRate: sr > 0 ? sr : SAMPLE_RATE,
        numberOfChannels: ch === 2 ? 2 : 1,
      });
    }

    if (!this.decoder || this.decoder.state === 'closed') {
      this.decoder = null;
      return;
    }

    const chunk = new EncodedAudioChunk({
      type: 'key',
      timestamp: this.opusTs,
      data: payload,
    });
    this.opusTs += 20_000;
    this.decoder.decode(chunk);
  }

  // ── Scheduler ──

  private _recordDecoded(buffer: AudioBuffer): void {
    if (buffer.length <= 0 || (buffer.numberOfChannels !== 1 && buffer.numberOfChannels !== 2)) return;
    let peak = 0;
    for (let c = 0; c < buffer.numberOfChannels; c++) {
      for (const sample of buffer.getChannelData(c)) {
        if (!Number.isFinite(sample)) return;
        peak = Math.max(peak, Math.abs(sample));
      }
    }
    this._playback.decodedFrames = Math.min(MAX_COUNTER, this._playback.decodedFrames + 1);
    this._playback.pcmPeak = Math.round(Math.min(1, peak) * 100);
  }

  private schedule(buf: AudioBuffer): void {
    if (!this.ctx || !this.preGain) return;
    const src = this.ctx.createBufferSource();
    src.buffer = buf;
    src.connect(this.preGain);

    const now = this.ctx.currentTime;
    if (this.nextPlayTime === 0 || this.nextPlayTime < now) {
      // Preserve a still-future schedule even when its remaining lead is
      // below the jitter floor: rebasing it would insert a silent gap.
      // Only an exhausted schedule is an underrun; start()/flush() prime
      // a new schedule with the configured floor.
      if (this.nextPlayTime > 0) this._underruns++;
      this.nextPlayTime = now + this._floorSec;
    }
    if (this.nextPlayTime > now + this._ceilingSec) {
      this._droppedFrames++;
      return;
    }

    src.start(this.nextPlayTime);
    this._playback.scheduledFrames = Math.min(MAX_COUNTER, this._playback.scheduledFrames + 1);
    this.nextPlayTime += buf.duration;
  }

  private _applyGraphState(): void {
    if (!this.mainGain || !this.subGain || !this.mainPanner || !this.subPanner) {
      return; // graph not built yet (start() hasn't run); state cached
    }
    const mainOn = this._focus === 'main' || this._focus === 'both';
    const subOn = this._focus === 'sub' || this._focus === 'both';
    this.mainGain.gain.value = mainOn ? dbToLinear(this._mainGainDb) : 0;
    this.subGain.gain.value = subOn ? dbToLinear(this._subGainDb) : 0;
    this.mainPanner.pan.value = this._splitStereo ? -1 : 0;
    this.subPanner.pan.value = this._splitStereo ? +1 : 0;
  }
}
