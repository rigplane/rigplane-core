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

  start(): void {
    if (this.ctx) {
      if (this.ctx.state === 'suspended') this.ctx.resume();
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
    if (this.ctx.state === 'suspended') {
      this.ctx.resume().catch(() => {});
    }
  }

  stop(): void {
    if (this.decoder) {
      try { this.decoder.close(); } catch { /* ok */ }
      this.decoder = null;
    }
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
  }

  /** Feed a raw binary frame from WS */
  feed(buffer: ArrayBuffer): void {
    const hdr = parseRxHeader(buffer);
    if (!hdr) return;

    if (hdr.codec === CODEC_PCM16) {
      this.playPcm16(hdr.payload, hdr.sampleRate, hdr.channels);
    } else if (hdr.codec === CODEC_OPUS) {
      this.decodeOpus(hdr.payload, hdr.sampleRate, hdr.channels);
    }
  }

  /** Flush pipeline (e.g. on reconnect) */
  flush(): void {
    this.nextPlayTime = 0;
  }

  // ── PCM16 playback ──

  private playPcm16(payload: Uint8Array, sr: number, ch: number): void {
    if (!this.ctx || !this.preGain) return;
    if (this.ctx.state === 'suspended') return;
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
    this.schedule(buf);
  }

  // ── Opus decode ──

  private decodeOpus(payload: Uint8Array, sr: number, ch: number): void {
    if (!this.ctx || !this.preGain) return;
    if (this.ctx.state === 'suspended') return;
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

  private schedule(buf: AudioBuffer): void {
    if (!this.ctx || !this.preGain) return;
    const src = this.ctx.createBufferSource();
    src.buffer = buf;
    src.connect(this.preGain);

    const now = this.ctx.currentTime;
    if (this.nextPlayTime < now + this._floorSec / 2) {
      this.nextPlayTime = now + this._floorSec;
    }
    if (this.nextPlayTime > now + this._ceilingSec) return;

    src.start(this.nextPlayTime);
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
