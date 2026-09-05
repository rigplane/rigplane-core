/**
 * Audio Manager — manages the /api/v1/audio WebSocket, RX playback, TX mic.
 *
 * Usage:
 *   audioManager.startRx()  → opens WS, starts playback
 *   audioManager.stopRx()   → stops playback
 *   audioManager.startTx()  → captures mic, starts encoding
 *   audioManager.stopTx()   → stops mic
 */

import { RxPlayer, type RxAudioFocus } from './rx-player';
import { TxMic, type TxCodec } from './tx-mic';
import { setAudioConnected } from '../stores/connection.svelte';
import { setRxEnabled, setTxEnabled, setTxCodecFallback } from '../stores/audio.svelte';
import { authenticatedWsUrl } from '../transport/ws-url';
import { getCapabilities } from '$lib/stores/capabilities.svelte';

export type AudioFocus = RxAudioFocus;

export interface AudioRoutingConfig {
  focus: AudioFocus;
  split_stereo: boolean;
  main_gain_db: number;
  sub_gain_db: number;
}

const BACKOFF_MIN = 500;
const BACKOFF_MAX = 10000;
// Link-quality uplink rate (MOR-585, ADR §3.6): low — one audio_stats
// message per 1.5 s while RX is active.
const AUDIO_STATS_INTERVAL_MS = 1500;

/** Stable per-page-context token so the server can coalesce this audio
 *  manager's reconnects (MOR-924). A soft_reconnect / audio re-arm drops the
 *  audio WS; the browser reopens it and re-sends ``audio_start`` carrying the
 *  same ``client_id``, letting the broadcaster drop the prior (now-zombie)
 *  subscription synchronously instead of fanning RX out to two subscribers
 *  until the half-open socket finally times out. Distinct AudioManager
 *  instances (a genuine second tab) get distinct ids and are NOT coalesced. */
function makeClientId(): string {
  const c = (globalThis as { crypto?: { randomUUID?: () => string } }).crypto;
  if (c?.randomUUID) return c.randomUUID();
  return `audio-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function preferredRxCodec(): 'opus' | 'pcm16' {
  const globals = globalThis as typeof globalThis & {
    __TAURI__?: unknown;
    __TAURI_INTERNALS__?: unknown;
  };
  if (globals.__TAURI__ !== undefined || globals.__TAURI_INTERNALS__ !== undefined) {
    return 'pcm16';
  }
  return typeof AudioDecoder === 'undefined' ? 'pcm16' : 'opus';
}

class AudioManager {
  private ws: WebSocket | null = null;
  private rxPlayer = new RxPlayer();
  private txMic: TxMic;
  private _rxEnabled = false;
  private _txEnabled = false;
  private appliedAudioConfig: Readonly<Partial<AudioRoutingConfig>> | null = null;
  private backoff = BACKOFF_MIN;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private statsTimer: ReturnType<typeof setInterval> | null = null;
  private _listeners: Set<() => void> = new Set();
  // Stable identity for reconnect coalescing (MOR-924); see makeClientId.
  private readonly clientId = makeClientId();

  // Reactive state (read externally)
  get rxEnabled(): boolean { return this._rxEnabled; }
  get txEnabled(): boolean { return this._txEnabled; }
  get wsConnected(): boolean { return this.ws?.readyState === WebSocket.OPEN; }
  get txSupported(): boolean { return TxMic.supported(); }

  constructor() {
    let txFrames = 0;
    let droppedFrames = 0;
    this.txMic = new TxMic((data) => {
      // Gate on local _txEnabled (set immediately on startTx), not
      // getRadioState()?.ptt which has a full round-trip delay.
      // IC-7610 LAN audio: RX stops during TX (not full-duplex).
      if (!this._txEnabled) {
        return;
      }
      
      if (this.ws?.readyState === WebSocket.OPEN) {
        this.ws.send(data);
        txFrames++;
        if (txFrames <= 3 || txFrames % 50 === 0) {
          console.log(`[audio-ws] TX frame #${txFrames} sent, size=${data.byteLength}`);
        }
      } else {
        // Dropped! WS not ready
        droppedFrames++;
        if (droppedFrames <= 5) {
          console.warn(`[audio-ws] TX frame dropped, WS state=${this.ws?.readyState}`);
        }
      }
    }, (reason) => this._failTxAudio(reason));
  }

  /** Register a change callback for reactive UI updates. Returns unsubscribe fn. */
  onChange(fn: () => void): () => void {
    this._listeners.add(fn);
    return () => { this._listeners.delete(fn); };
  }

  private notify(): void {
    for (const fn of this._listeners) fn();
  }

  // ── RX ──

  startRx(): void {
    if (this._rxEnabled) return;
    this._rxEnabled = true;
    setRxEnabled(true);
    const audioCfg = getCapabilities()?.audioConfig;
    if (audioCfg?.jitterFloorMs !== undefined && audioCfg?.jitterCeilingMs !== undefined) {
      this.rxPlayer.setJitterBounds(audioCfg.jitterFloorMs, audioCfg.jitterCeilingMs);
    }
    this.rxPlayer.start();
    this.connect();
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({
        type: 'audio_start',
        direction: 'rx',
        preferred_rx_codec: preferredRxCodec(),
        client_id: this.clientId,
      }));
    }
    this.notify();
  }

  stopRx(): void {
    if (!this._rxEnabled) return;
    this._rxEnabled = false;
    setRxEnabled(false);
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'audio_stop', direction: 'rx' }));
    }
    this.rxPlayer.stop();
    this.maybeDisconnect();
    this.notify();
  }

  /** Apply MAIN/SUB focus + stereo split + per-channel gain (issue #753).
   *
   * Updates the local WebAudio graph and — when WS is open — sends the
   * ``audio_config`` message to the backend so it can set CI-V Phones L/R
   * Mix accordingly (handled server-side in #752/#755).  Gain values are
   * applied locally only; the backend does not receive them.
   *
   * WS-delivery semantics: if the audio WS is not yet open (user hasn't
   * started RX audio in the browser) we eagerly open it and queue the
   * pending focus/split pair; ``_flushPendingAudioConfig`` fires the
   * message once the socket is open.  Without this, clicking ACTIVATE on
   * MAIN/SUB was a no-op on the radio's own Phones L/R Mix because the
   * CI-V command never left the browser.
   */
  setAudioConfig(cfg: Partial<AudioRoutingConfig>): void {
    if (cfg.focus !== undefined) this.rxPlayer.setFocus(cfg.focus);
    if (cfg.split_stereo !== undefined) this.rxPlayer.setSplitStereo(cfg.split_stereo);
    if (cfg.main_gain_db !== undefined) this.rxPlayer.setChannelGainDb('main', cfg.main_gain_db);
    if (cfg.sub_gain_db !== undefined) this.rxPlayer.setChannelGainDb('sub', cfg.sub_gain_db);
    const applied = this.getAudioConfig();
    this.appliedAudioConfig = Object.freeze({
      ...this.appliedAudioConfig,
      ...(cfg.focus !== undefined && cfg.focus === applied.focus ? { focus: applied.focus } : {}),
      ...(cfg.split_stereo !== undefined ? { split_stereo: applied.split_stereo } : {}),
      ...(cfg.main_gain_db !== undefined ? { main_gain_db: applied.main_gain_db } : {}),
      ...(cfg.sub_gain_db !== undefined ? { sub_gain_db: applied.sub_gain_db } : {}),
    });
    this.notify();
    // Only the focus + split_stereo pair maps to CI-V; gain is local.
    if (cfg.focus === undefined && cfg.split_stereo === undefined) return;
    if (this.ws?.readyState === WebSocket.OPEN) {
      this._flushPendingAudioConfig();
      return;
    }
    // WS not open — remember that the next open should flush the config,
    // and kick off the connect.  ``onopen`` will call the flush.
    this._audioConfigPending = true;
    this.connect();
  }

  private _audioConfigPending: boolean = false;

  private _flushPendingAudioConfig(): void {
    if (this.ws?.readyState !== WebSocket.OPEN) return;
    this.ws.send(JSON.stringify({
      type: 'audio_config',
      focus: this.rxPlayer.focus,
      split_stereo: this.rxPlayer.splitStereo,
    }));
    this._audioConfigPending = false;
  }

  /** Current config snapshot — useful for UI state rehydration. */
  getAudioConfig(): AudioRoutingConfig {
    return {
      focus: this.rxPlayer.focus,
      split_stereo: this.rxPlayer.splitStereo,
      main_gain_db: this.rxPlayer.mainGainDb,
      sub_gain_db: this.rxPlayer.subGainDb,
    };
  }

  getAppliedAudioConfig(): Readonly<Partial<AudioRoutingConfig>> | null {
    return this.appliedAudioConfig;
  }

  setRxVolume(v: number): void {
    this.rxPlayer.volume = v;
  }

  // ── TX ──

  async startTx(): Promise<string | null> {
    if (this._txEnabled) return null;
    const err = await this.txMic.start();
    if (err) return err;
    if (!this.txMic.active) return 'TX MIC: capture stopped before start completed';
    this._txEnabled = true;
    setTxEnabled(true);
    this.connect();
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'audio_start', direction: 'tx' }));
    }
    this.notify();
    return null;
  }

  stopTx(): void {
    this.txMic.stop();
    if (!this._txEnabled) return;
    this._txEnabled = false;
    setTxEnabled(false);
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'audio_stop', direction: 'tx' }));
    }
    this.maybeDisconnect();
    this.notify();
  }

  // ── WS lifecycle ──

  private connect(): void {
    if (this.ws?.readyState === WebSocket.OPEN || this.ws?.readyState === WebSocket.CONNECTING) {
      return;
    }
    this.close();

    const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
    const url = `${proto}//${location.host}/api/v1/audio`;
    const ws = new WebSocket(authenticatedWsUrl(url));
    ws.binaryType = 'arraybuffer';
    this.ws = ws;

    ws.onopen = () => {
      this.backoff = BACKOFF_MIN;
      setAudioConnected(true);
      console.log('[audio-ws] connected');
      if (this._rxEnabled) {
        // Idempotent resume nudge: if the AudioContext re-entered
        // 'suspended' while the WS was down (WKWebView backgrounding), wake
        // it as we resubscribe so streamed frames are not dropped.
        this.rxPlayer.start();
        ws.send(JSON.stringify({
          type: 'audio_start',
          direction: 'rx',
          preferred_rx_codec: preferredRxCodec(),
          client_id: this.clientId,
        }));
      }
      if (this._txEnabled) {
        ws.send(JSON.stringify({ type: 'audio_start', direction: 'tx' }));
      }
      // If setAudioConfig was called before the WS was open, push the
      // cached focus/split pair now so the backend can update CI-V
      // Phones L/R Mix.  Keeps ACTIVATE on MAIN/SUB consistent regardless
      // of whether the user has started RX audio in the browser.
      if (this._audioConfigPending) {
        this._flushPendingAudioConfig();
      }
      this._startStatsTimer();
      this.rxPlayer.flush();
      this.notify();
    };

    ws.onmessage = (ev) => {
      if (ev.data instanceof ArrayBuffer) {
        this.rxPlayer.feed(ev.data);
        return;
      }
      if (typeof ev.data === 'string') {
        this._handleServerMessage(ev.data);
      }
    };

    ws.onerror = () => {
      console.error('[audio-ws] error');
      ws.close();
    };

    ws.onclose = (ev) => {
      console.warn(`[audio-ws] closed code=${ev.code} reason=${ev.reason}`);
      this._clearStatsTimer();
      this.ws = null;
      setAudioConnected(false);
      // Decoder availability is a fact about the server we are talking to;
      // once the link is gone we no longer know it. Re-learned at the next
      // TX start (MOR-1791).
      this._setTxCodecFallback(false);
      this.notify();
      if (!this._rxEnabled && !this._txEnabled) return;
      // Reconnect with backoff
      const delay = this.backoff;
      this.backoff = Math.min(Math.floor(this.backoff * 1.7), BACKOFF_MAX);
      this.reconnectTimer = setTimeout(() => this.connect(), delay);
    };
  }

  // ── TX codec negotiation (MOR-1791) ──
  //
  // The server answers every ``audio_start direction=tx`` with an
  // ``audio_tx_format`` ack naming the codec it can actually accept. Without
  // it, a server with no native opus codec drops every browser Opus frame
  // fail-closed: the radio keys and nothing reaches the air. Text frames on
  // this socket were previously discarded, so consuming them is additive on
  // both sides — an older server simply never sends one and we keep Opus.

  /** True while browser TX is pinned to PCM16 because the server cannot
   *  decode Opus. Surfaced to the operator as a quiet status hint. */
  get txCodecFallback(): boolean { return this._txCodecFallback; }

  /** Codec the microphone is currently emitting, or null when not capturing. */
  get txCodec(): TxCodec | null { return this.txMic.codec; }

  private _txCodecFallback = false;

  private txAudioDiedCallbacks = new Set<() => void>();

  /** MOR-1796: notified when mid-transmission TX capture dies and the TX
   *  audio leg is torn down. The TX controller turns this into a de-key. */
  onTxAudioDied(callback: () => void): () => void {
    this.txAudioDiedCallbacks.add(callback);
    return () => this.txAudioDiedCallbacks.delete(callback);
  }

  private _handleServerMessage(raw: string): void {
    let msg: { type?: unknown; codec?: unknown; opus_decode?: unknown };
    try {
      msg = JSON.parse(raw) as typeof msg;
    } catch {
      return;
    }
    if (msg?.type !== 'audio_tx_format') return;
    // Fail-safe on an unrecognized codec: ignore the whole ack rather than
    // guessing. Defaulting to 'opus' would CLEAR a sticky PCM16 pin, i.e.
    // fail open on exactly the condition this negotiation exists for.
    const codec = msg.codec === 'pcm16' ? 'pcm16' : msg.codec === 'opus' ? 'opus' : null;
    if (codec === null) return;

    const { switched, error } = this.txMic.applyServerCodec(codec);
    if (error !== null) {
      this._failTxAudio(error);
      return;
    }
    if (switched) {
      console.warn('[audio-ws] server cannot decode Opus — TX switched to PCM16');
    }
    this._setTxCodecFallback(msg.opus_decode === false);
  }

  /** End failed capture/codec audio and notify the existing canonical de-key path. */
  private _failTxAudio(reason: string): void {
    // A failure during preparation is returned by startTx, before TX admission.
    if (!this._txEnabled) return;
    console.error(`[audio-ws] TX audio failed, stopping TX audio: ${reason}`);
    this._setTxCodecFallback(false);
    this.stopTx();
    // Snapshot + isolate: one throwing subscriber must not starve the rest.
    for (const callback of [...this.txAudioDiedCallbacks]) {
      try {
        callback();
      } catch (error) {
        console.error('[audio-ws] TX audio-died subscriber failed', error);
      }
    }
  }

  private _setTxCodecFallback(active: boolean): void {
    if (active === this._txCodecFallback) return;
    this._txCodecFallback = active;
    setTxCodecFallback(active);
    this.notify();
  }

  // ── Link-quality uplink (MOR-585, ADR §3.6) ──
  //
  // While RX is active, periodically reports the player's link-quality
  // counters so the server can record them per client (the step-19
  // adaptive codec controller's client-side signal). Stats only — the
  // server changes no behavior on receipt.

  private _startStatsTimer(): void {
    if (this.statsTimer) return;
    this.statsTimer = setInterval(() => this._sendAudioStats(), AUDIO_STATS_INTERVAL_MS);
  }

  private _clearStatsTimer(): void {
    if (this.statsTimer) {
      clearInterval(this.statsTimer);
      this.statsTimer = null;
    }
  }

  private _sendAudioStats(): void {
    if (!this._rxEnabled || this.ws?.readyState !== WebSocket.OPEN) return;
    const stats = this.rxPlayer.stats();
    this.ws.send(JSON.stringify({
      type: 'audio_stats',
      underruns: stats.underruns,
      buffer_depth_ms: stats.bufferDepthMs,
      dropped_frames: stats.droppedFrames,
    }));
  }

  private close(): void {
    this._clearStatsTimer();
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.ws.onclose = null;
      this.ws.close();
      this.ws = null;
      setAudioConnected(false);
    }
  }

  private maybeDisconnect(): void {
    if (!this._rxEnabled && !this._txEnabled) {
      this.close();
      this.notify();
    }
  }

  /** Full cleanup */
  destroy(): void {
    this.stopRx();
    this.stopTx();
    this.close();
  }
}

export const audioManager = new AudioManager();
