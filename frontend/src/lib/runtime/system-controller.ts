/**
 * SystemController — owns system-level actions: power, client connect/disconnect,
 * frequency identification.
 *
 * connect()/disconnect() control the entire frontend connection lifecycle:
 * all WebSocket channels, HTTP polling, audio, and MediaSession.
 */

import { disconnectAll as wsDisconnectAll, reconnectAll as wsReconnectAll } from '$lib/transport/ws-client';
import { audioManager } from '$lib/audio/audio-manager';
import { destroyMediaSession, initMediaSession } from '$lib/media/media-session';
import { clearEtag } from '$lib/transport/http-client';
import { setHttpConnected, setRadioStatus } from '$lib/stores/connection.svelte';
import { resetRadioState } from '$lib/stores/radio.svelte';

export interface EibiStation {
  name?: string;
  freq?: number;
  language?: string;
  target?: string;
  [key: string]: unknown;
}

export interface EibiResult {
  stations: EibiStation[];
}

export class SystemController {
  private _stopPolling: (() => void) | null = null;
  private _startPolling: (() => (() => void)) | null = null;
  private _clientConnected = true;
  private _releaseBarrier: { run: () => Promise<void> } | null = null;
  private _disconnectInFlight: Promise<void> | null = null;

  constructor(
    private readonly _effects = {
      destroyAudio: () => audioManager.destroy(),
      disconnectWebSockets: () => wsDisconnectAll(),
      setHttpDisconnected: () => setHttpConnected(false),
      destroyMediaSession: () => destroyMediaSession(),
      setRadioDisconnected: () => setRadioStatus('disconnected'),
      resetRadioState: () => resetRadioState(),
      initMediaSession: () => initMediaSession(),
      clearEtag: () => clearEtag(),
      reconnectWebSockets: () => wsReconnectAll(),
    },
  ) {}

  get clientConnected(): boolean {
    return this._clientConnected;
  }

  /** Register the polling start function (called once from App.svelte). */
  registerPolling(startFn: () => (() => void)): void {
    this._startPolling = startFn;
  }

  /** Register an active polling stop handle (called from App.svelte after startPolling). */
  setStopPolling(stopFn: (() => void) | null): void {
    this._stopPolling = stopFn;
  }

  /** Register the sole opaque barrier awaited before disconnect teardown. */
  registerPreDisconnectBarrier(barrier: () => Promise<void>): () => void {
    if (this._releaseBarrier) {
      throw new Error('A pre-disconnect barrier is already registered');
    }
    const registration = { run: barrier };
    this._releaseBarrier = registration;
    return () => {
      if (this._releaseBarrier === registration) this._releaseBarrier = null;
    };
  }

  async powerOn(): Promise<void> {
    const resp = await fetch('/api/v1/radio/power', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ state: 'on' }),
    });
    if (!resp.ok) throw new Error(await resp.text());
  }

  async powerOff(): Promise<void> {
    const resp = await fetch('/api/v1/radio/power', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ state: 'off' }),
    });
    if (!resp.ok) throw new Error(await resp.text());
  }

  /** Disconnect all frontend channels: WS, audio, polling, MediaSession. */
  disconnect(): Promise<void> {
    if (this._disconnectInFlight) return this._disconnectInFlight;
    if (!this._clientConnected) return Promise.resolve();
    this._clientConnected = false;
    const barrier = this._releaseBarrier?.run;
    if (!barrier) {
      this._teardown();
      return Promise.resolve();
    }

    const inFlight = Promise.resolve()
      .then(barrier)
      .finally(() => {
        try {
          this._teardown();
        } finally {
          this._disconnectInFlight = null;
        }
      });
    this._disconnectInFlight = inFlight;
    return inFlight;
  }

  private _teardown(): void {
    // 1. Stop audio (RX/TX playback + audio WS)
    this._effects.destroyAudio();

    // 2. Close all WebSocket channels (control + scope + any named)
    this._effects.disconnectWebSockets();

    // 3. Stop HTTP polling
    this._stopPolling?.();
    this._stopPolling = null;
    this._effects.setHttpDisconnected();

    // 4. Stop MediaSession silent audio loop
    this._effects.destroyMediaSession();

    // 5. Clear stale state
    this._effects.setRadioDisconnected();
    this._effects.resetRadioState();
  }

  /** Reconnect all frontend channels. */
  connect(): void {
    if (this._disconnectInFlight || this._clientConnected) return;
    this._clientConnected = true;

    // 1. Restart MediaSession
    this._effects.initMediaSession();

    // 2. Restart HTTP polling
    this._effects.clearEtag();
    if (this._startPolling) {
      this._stopPolling = this._startPolling();
    }

    // 3. Reconnect all WebSocket channels (control + scope + any named)
    this._effects.reconnectWebSockets();
  }

  async identifyFrequency(freqHz: number): Promise<EibiResult | null> {
    try {
      const resp = await fetch(`/api/v1/eibi/identify?freq=${freqHz}`);
      if (!resp.ok) return null;
      return await resp.json();
    } catch {
      return null;
    }
  }
}

export const systemController = new SystemController();
