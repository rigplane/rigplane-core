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
import { TxMic } from './tx-mic';
import { setAudioConnected } from '../stores/connection.svelte';
import { setRxEnabled, setTxEnabled } from '../stores/audio.svelte';
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
  private backoff = BACKOFF_MIN;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private _listeners: Set<() => void> = new Set();

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
    });
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

  setRxVolume(v: number): void {
    this.rxPlayer.volume = v;
  }

  // ── TX ──

  async startTx(): Promise<string | null> {
    if (this._txEnabled) return null;
    const err = await this.txMic.start();
    if (err) return err;
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
    if (!this._txEnabled) return;
    this._txEnabled = false;
    setTxEnabled(false);
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify({ type: 'audio_stop', direction: 'tx' }));
    }
    this.txMic.stop();
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
    const ws = new WebSocket(url);
    ws.binaryType = 'arraybuffer';
    this.ws = ws;

    ws.onopen = () => {
      this.backoff = BACKOFF_MIN;
      setAudioConnected(true);
      console.log('[audio-ws] connected');
      if (this._rxEnabled) {
        ws.send(JSON.stringify({
          type: 'audio_start',
          direction: 'rx',
          preferred_rx_codec: preferredRxCodec(),
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
      this.rxPlayer.flush();
      this.notify();
    };

    ws.onmessage = (ev) => {
      if (ev.data instanceof ArrayBuffer) {
        this.rxPlayer.feed(ev.data);
      }
    };

    ws.onerror = (e) => {
      console.error('[audio-ws] error', e);
      ws.close();
    };

    ws.onclose = (ev) => {
      console.warn(`[audio-ws] closed code=${ev.code} reason=${ev.reason}`);
      this.ws = null;
      setAudioConnected(false);
      this.notify();
      if (!this._rxEnabled && !this._txEnabled) return;
      // Reconnect with backoff
      const delay = this.backoff;
      this.backoff = Math.min(Math.floor(this.backoff * 1.7), BACKOFF_MAX);
      this.reconnectTimer = setTimeout(() => this.connect(), delay);
    };
  }

  private close(): void {
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
