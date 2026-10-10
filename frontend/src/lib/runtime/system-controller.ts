/**
 * SystemController — owns system-level actions: power, client connect/disconnect,
 * frequency identification.
 *
 * connect()/disconnect() control the entire frontend connection lifecycle:
 * all WebSocket channels, audio, and MediaSession.
 */

import { disconnectAll as wsDisconnectAll, reconnectAll as wsReconnectAll, emitLocalNotification } from '$lib/transport/ws-client';
import { controllerClient, ControllerClientError } from '$lib/transport/controller-client';
import { audioManager } from '$lib/audio/audio-manager';
import { destroyMediaSession, initMediaSession } from '$lib/media/media-session';
import { setRadioStatus } from '$lib/stores/connection.svelte';
import { resetRadioState } from '$lib/stores/radio.svelte';
import { getAuthHeaders } from '$lib/auth';

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
  private _clientConnected = true;
  private _releaseBarrier: { run: () => Promise<void> } | null = null;
  private _disconnectInFlight: Promise<void> | null = null;
  private _connectInFlight: Promise<void> | null = null;
  private _connectAbort: AbortController | null = null;

  constructor(
    private readonly _effects = {
      destroyAudio: () => audioManager.destroy(),
      disconnectWebSockets: () => wsDisconnectAll(),
      destroyMediaSession: () => destroyMediaSession(),
      setRadioDisconnected: () => setRadioStatus('disconnected'),
      resetRadioState: () => resetRadioState(),
      initMediaSession: () => initMediaSession(),
      reconnectWebSockets: () => wsReconnectAll(),
    },
  ) {}

  get clientConnected(): boolean {
    return this._clientConnected;
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
    controllerClient.assertHostAction();
    const resp = await fetch('/api/v1/radio/power', {
      method: 'POST',
      headers: { ...getAuthHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ state: 'on' }),
    });
    if (!resp.ok) throw new Error(await resp.text());
  }

  async powerOff(): Promise<void> {
    controllerClient.assertHostAction();
    const resp = await fetch('/api/v1/radio/power', {
      method: 'POST',
      headers: { ...getAuthHeaders(), 'Content-Type': 'application/json' },
      body: JSON.stringify({ state: 'off' }),
    });
    if (!resp.ok) throw new Error(await resp.text());
  }

  /** Disconnect all frontend channels: WS, audio, MediaSession. */
  disconnect(): Promise<void> {
    if (this._disconnectInFlight) return this._disconnectInFlight;
    if (!this._clientConnected) return Promise.resolve();
    this._clientConnected = false;
    this._connectAbort?.abort();
    this._connectAbort = null;
    this._connectInFlight = null;
    if (controllerClient.remote) this._effects.destroyAudio();
    const barrier = this._releaseBarrier?.run;
    if (!barrier && !controllerClient.remote) {
      this._teardown();
      return Promise.resolve();
    }

    const inFlight = Promise.resolve()
      .then(barrier)
      .finally(async () => {
        try {
          const release = controllerClient.remote ? controllerClient.release() : Promise.resolve();
          try { this._teardown(); } finally { await release; }
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

    // 2. Close all WebSocket channels (control + scope + any named). This
    // synchronously flips `wsConnected` false via the control channel's
    // state-change callback (MOR-1419) — no separate HTTP flag to clear.
    this._effects.disconnectWebSockets();

    // 3. Stop MediaSession silent audio loop
    this._effects.destroyMediaSession();

    // 4. Clear stale state
    this._effects.setRadioDisconnected();
    this._effects.resetRadioState();
  }

  /** Reconnect all frontend channels. */
  connect(options: { operatorInitiated?: boolean } = {}): void {
    if (this._disconnectInFlight || this._clientConnected) return;
    this._clientConnected = true;

    const abort = new AbortController();
    this._connectAbort = abort;
    const pending = Promise.resolve().then(async () => {
      if (abort.signal.aborted) return false;
      if (options.operatorInitiated && typeof window !== 'undefined') {
        let handler: (() => Promise<boolean>) | undefined;
        // A host may replace this explicit action with its own connection flow.
        // Bootstrap and transport retries never dispatch this event.
        window.dispatchEvent(new CustomEvent('rigplane:before-connect', {
          detail: {
            signal: abort.signal,
            handle: (candidate: () => Promise<boolean>): boolean => {
              if (handler) return false;
              handler = candidate;
              return true;
            },
          },
        }));
        if (handler && await handler() === false) {
          if (this._connectInFlight === pending) this._clientConnected = false;
          return false;
        }
      }
      if (this._connectInFlight !== pending || abort.signal.aborted) return false;
      await controllerClient.prepare(abort.signal);
      return true;
    }).then((prepared) => {
      if (!prepared) return;
      if (this._connectInFlight !== pending || !this._clientConnected || abort.signal.aborted) return;
      this._effects.initMediaSession();
      this._effects.reconnectWebSockets();
    }).catch((error: unknown) => {
      if (this._connectInFlight !== pending || abort.signal.aborted) return;
      this._clientConnected = false;
      const failure = error instanceof ControllerClientError ? error : new ControllerClientError('controller_not_ready');
      emitLocalNotification('error', failure.message, failure.code);
    }).finally(() => {
      if (this._connectInFlight === pending) {
        this._connectInFlight = null;
        this._connectAbort = null;
      }
    });
    this._connectInFlight = pending;
  }

  async identifyFrequency(freqHz: number): Promise<EibiResult | null> {
    try {
      const resp = await fetch(`/api/v1/eibi/identify?freq=${freqHz}`, {
        headers: getAuthHeaders(),
      });
      if (!resp.ok) return null;
      return await resp.json();
    } catch {
      return null;
    }
  }
}

export const systemController = new SystemController();
