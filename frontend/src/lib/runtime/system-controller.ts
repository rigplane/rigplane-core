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
import { setRxError, setTxError } from '$lib/stores/audio.svelte';
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
  /** The in-flight disconnect: its shared promise plus whether an explicit
   *  request joined this automatic operation and must be acknowledged at
   *  its completion (MOR-3753). */
  private _disconnectInFlight: { promise: Promise<void>; explicitJoin: boolean } | null = null;
  private _connectInFlight: Promise<void> | null = null;

  constructor(
    private readonly _effects = {
      destroyAudio: (options?: { automatic?: boolean }) => audioManager.destroy(options),
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

  /** Disconnect all frontend channels: WS, audio, MediaSession. Default is
   *  an explicit disconnect, which acknowledges latched audio errors;
   *  { automatic: true } preserves them for the operator. */
  disconnect(options?: { automatic?: boolean }): Promise<void> {
    const automatic = options?.automatic === true;
    if (!automatic) {
      // State-only acknowledgement (MOR-3753): an explicit stop clears a
      // latched RX/TX error — also on the already-disconnected no-op path
      // and while coalescing with an in-flight disconnect — without
      // repeating media cleanup. Automatic calls neither clear nor
      // manufacture an error.
      setRxError(false);
      setTxError(false);
    }
    if (this._disconnectInFlight) {
      // MOR-3753: an explicit join is acknowledged at completion of THIS
      // shared teardown, not only here at entry — a failure arriving while
      // the barrier still holds (the retiring session's dying socket)
      // belongs to the operation this request is joining.
      if (!automatic) this._disconnectInFlight.explicitJoin = true;
      return this._disconnectInFlight.promise;
    }
    if (!this._clientConnected) return Promise.resolve();
    this._clientConnected = false;
    this._connectInFlight = null;
    if (controllerClient.remote) this._effects.destroyAudio(options);
    const barrier = this._releaseBarrier?.run;
    if (!barrier && !controllerClient.remote) {
      // MOR-3535: release the local session so the server frees this
      // client's TX hold immediately instead of waiting for the TTL.
      if (controllerClient.session) void controllerClient.release().catch(() => {});
      this._teardown(options);
      return Promise.resolve();
    }

    const operation = {
      // Assigned synchronously below, once the chain exists.
      promise: undefined as unknown as Promise<void>,
      explicitJoin: !automatic,
    };
    const inFlight = Promise.resolve()
      .then(barrier)
      .finally(async () => {
        try {
          const release = controllerClient.remote
            ? controllerClient.release()
            : controllerClient.session
              ? controllerClient.release().catch(() => undefined)
              : Promise.resolve();
          try { this._teardown(options); } finally { await release; }
        } finally {
          this._disconnectInFlight = null;
          // State-only acknowledgement of the shared operation, on rejection
          // as well — never a second media teardown (MOR-3753).
          if (operation.explicitJoin) {
            setRxError(false);
            setTxError(false);
          }
        }
      });
    operation.promise = inFlight;
    this._disconnectInFlight = operation;
    return inFlight;
  }

  private _teardown(options?: { automatic?: boolean }): void {
    // 1. Stop audio (RX/TX playback + audio WS). An automatic teardown
    //    preserves latched audio errors (MOR-3753); the default clears them.
    this._effects.destroyAudio(options);

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
  connect(): void {
    if (this._disconnectInFlight || this._clientConnected) return;
    this._clientConnected = true;

    if (controllerClient.remote) {
      const pending = controllerClient.prepare().then(() => {
        if (this._connectInFlight !== pending || !this._clientConnected) return;
        this._effects.initMediaSession();
        this._effects.reconnectWebSockets();
      }).catch((error: unknown) => {
        if (this._connectInFlight !== pending) return;
        this._clientConnected = false;
        const failure = error instanceof ControllerClientError ? error : new ControllerClientError('controller_not_ready');
        emitLocalNotification('error', failure.message, failure.code);
      }).finally(() => {
        if (this._connectInFlight === pending) this._connectInFlight = null;
      });
      this._connectInFlight = pending;
      return;
    }

    // 1. Restart MediaSession
    this._effects.initMediaSession();

    // 2. Reconnect all WebSocket channels (control + scope + any named)
    this._effects.reconnectWebSockets();
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
