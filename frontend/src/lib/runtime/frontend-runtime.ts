/**
 * FrontendRuntime — shared singleton shell for all frontend behavior.
 *
 * Wraps existing stores, transport, and audio into a single entry point.
 * Components import `runtime` instead of reaching into individual modules.
 *
 * This is a thin delegation layer — it owns no state, only routes reads
 * and writes to the existing infrastructure. Svelte 5 reactivity is
 * preserved because getters return live $state references, not copies.
 *
 * @see docs/plans/2026-04-12-target-frontend-architecture.md
 */

import { radio, getRadioState, patchActiveReceiver, patchRadioState, setRadioState } from '$lib/stores/radio.svelte';
import { getCapabilities, setCapabilities } from '$lib/stores/capabilities.svelte';
import {
  getConnectionStatus,
  isConnected,
  getHttpConnected,
  getWsConnected,
  isAudioConnected,
  isStale,
  isReconnecting,
  getRadioStatus,
  getRadioPowerOn,
} from '$lib/stores/connection.svelte';
import { getAudioState, setVolume, setMuted, toggleMute } from '$lib/stores/audio.svelte';
import { sendCommand, connect, onMessage, sendRaw } from '$lib/transport/ws-client';
import { fetchCapabilities, startPolling, setPollingMultiplier } from '$lib/transport/http-client';
import { audioManager } from '$lib/audio/audio-manager';
import { clearLegacyPendingModInputRestore } from './adapters/mod-input-auto.svelte';
import { derivePresentationCapabilities } from './adapters/presentation-capabilities';
import { systemController } from './system-controller';
import { scopeController } from './scope-controller.svelte';
import type { ScopeController, ScopeSource } from './scope-controller.svelte';
import { PresentationResourceHost } from './resource-host';
import type { ResourceHealth, ResourceLease } from './resource-demand';
import { createSubscriber } from 'svelte/reactivity';
import type { ServerState, ReceiverState } from '$lib/types/state';
import type { Capabilities } from '$lib/types/capabilities';
import type { WsIncoming } from '$lib/types/protocol';
import type { ConnectionState } from '$lib/transport/ws-client';
export const presentationResources = new PresentationResourceHost<unknown>('app');
// ── Types ──

export type DxMessage = Extract<WsIncoming, { type: 'dx_spot' | 'dx_spots' }>;

export interface ConnectionSnapshot {
  status: 'connected' | 'partial' | 'disconnected';
  http: boolean;
  ws: boolean;
  audio: boolean;
  stale: boolean;
  reconnecting: boolean;
  radioStatus: string;
  radioPowerOn: boolean | null;
}

export interface DefaultScopeStatus {
  source: ScopeSource | null;
  available: boolean;
  resourceSelected: boolean;
  demand: number;
  lifecycle: ResourceHealth;
  transport: ConnectionState;
  frameSeen: boolean;
}

// ── Runtime class ──

class FrontendRuntime {
  private _bootstrapCleanup: (() => void) | null = null;
  private _bootstrapInFlight: Promise<() => void> | null = null;
  private _rxAudioLease: ResourceLease | null = null;
  private _ended = false;
  private _dxSubscribers = new Map<number, (message: DxMessage) => void>();
  private _dxControlUnsubscribe: (() => void) | null = null;
  private _nextDxSubscriber = 0;
  private _defaultScopeSource: ScopeSource | null = null;
  private _defaultScopeSnapshot: DefaultScopeStatus = {
    source: null, available: false, resourceSelected: false, demand: 0,
    lifecycle: 'inactive', transport: 'disconnected', frameSeen: false,
  };
  private _defaultScopeStop: (() => void) | null = null;
  private _defaultScopeSubscribe = createSubscriber((update) => {
    if (this._ended) return;
    const notify = () => {
      this._defaultScopeSnapshot = this._readDefaultScopeStatus();
      update();
    };
    const unsubscribeHost = presentationResources.subscribe(() => notify());
    const unsubscribeHealth = scopeController.subscribeHealth(() => notify());
    let active = true;
    const stop = () => {
      if (!active) return;
      active = false;
      try { unsubscribeHost(); } finally { unsubscribeHealth(); }
      if (this._defaultScopeStop === stop) this._defaultScopeStop = null;
    };
    this._defaultScopeStop = stop;
    return stop;
  });

  constructor() {
    presentationResources.configure('hardware-scope', {
      available: false,
      selected: false,
      driver: scopeController.hardwareScopeDriver,
    });
    scopeController.registerPresentationDriver(presentationResources);
    presentationResources.configure('rx-audio', {
      available: false,
      selected: true,
      driver: {
        start: () => {
          audioManager.startRx();
          return audioManager;
        },
        stop: () => {
          audioManager.stopRx();
        },
      },
    });
  }

  // ── Reactive state reads ──
  // These return live $state references — Svelte 5 tracks them automatically.

  /** Current radio state (frequency, mode, meters, etc.) */
  get state(): ServerState | null {
    return radio.current;
  }

  /** Radio capabilities (modes, filters, features, etc.) */
  get caps(): Capabilities | null {
    return getCapabilities();
  }

  /** Connection health — individual reactive getters to avoid object allocation. */
  get connectionStatus(): 'connected' | 'partial' | 'disconnected' {
    return getConnectionStatus();
  }

  get connectionHttp(): boolean { return getHttpConnected(); }
  get connectionWs(): boolean { return getWsConnected(); }
  get connectionAudio(): boolean { return isAudioConnected(); }
  get connectionStale(): boolean { return isStale(); }
  get connectionReconnecting(): boolean { return isReconnecting(); }
  get radioStatus(): string { return getRadioStatus(); }
  get radioPowerOn(): boolean | null { return getRadioPowerOn(); }

  /**
   * Connection snapshot (for contexts that need all fields at once).
   * Prefer individual getters in $derived for better Svelte 5 reactivity.
   */
  get connection(): ConnectionSnapshot {
    return {
      status: getConnectionStatus(),
      http: getHttpConnected(),
      ws: getWsConnected(),
      audio: isAudioConnected(),
      stale: isStale(),
      reconnecting: isReconnecting(),
      radioStatus: getRadioStatus(),
      radioPowerOn: getRadioPowerOn(),
    };
  }

  /** Audio UI state — returns the live $state object directly. */
  get audio() {
    return getAudioState();
  }

  /** Whether the runtime has a radio connection. */
  get connected(): boolean {
    return isConnected();
  }

  // ── System controller ──

  /** System actions (power, connect/disconnect, frequency identification). */
  get system() {
    return systemController;
  }

  // ── Scope controller ──

  /** Single owner of the audio-scope WS channel. Subscribe to receive parsed frames. */
  get scope(): ScopeController {
    return scopeController;
  }

  get defaultScopeStatus(): DefaultScopeStatus {
    this._defaultScopeSubscribe();
    this._defaultScopeSnapshot = this._readDefaultScopeStatus();
    return this._defaultScopeSnapshot;
  }

  private _readDefaultScopeStatus(): DefaultScopeStatus {
    const source = this._defaultScopeSource;
    if (source === null) return {
      source, available: false, resourceSelected: false, demand: 0,
      lifecycle: 'inactive', transport: 'disconnected', frameSeen: false,
    };
    const resource = source === 'hardware' ? 'hardware-scope' : 'audio-fft';
    const host = presentationResources.snapshot(resource);
    const health = scopeController.snapshotHealth(source);
    return {
      source, available: host.available, resourceSelected: host.selected,
      demand: host.demand, lifecycle: host.health,
      transport: health.transport, frameSeen: health.frameSeen,
    };
  }

  // ── Bootstrap ──

  /**
   * Initialize the full transport stack: capabilities → polling → WebSocket → subscribe.
   *
   * Idempotent: if already started, returns the existing cleanup function without
   * re-running any transport calls. Concurrent callers share a single in-flight promise
   * to prevent duplicate initialization. If the previous attempt threw, the sentinel
   * is cleared and bootstrap can be retried.
   *
   * @returns A cleanup function that stops polling when called.
   */
  async bootstrap(): Promise<() => void> {
    // If already completed, return cached cleanup.
    if (this._bootstrapCleanup !== null) {
      return this._bootstrapCleanup;
    }

    // If in-flight, return that promise to serialize concurrent callers.
    if (this._bootstrapInFlight !== null) {
      return this._bootstrapInFlight;
    }

    // Set sentinel before first await to serialize concurrent callers.
    this._bootstrapInFlight = this._doBootstrap();

    try {
      return await this._bootstrapInFlight;
    } finally {
      // Clear sentinel after completion (success or failure).
      this._bootstrapInFlight = null;
    }
  }

  /**
   * Private implementation of bootstrap. Separated so the sentinel
   * can be set before this async function starts.
   */
  private async _doBootstrap(): Promise<() => void> {
    // Remove legacy pre-authority-gate restore records without consulting
    // cached state or issuing any radio command.
    clearLegacyPendingModInputRestore();

    // 1. Fetch capabilities and push into the store.
    const caps = await fetchCapabilities();
    setCapabilities(caps);
    const presentationCaps = derivePresentationCapabilities(caps);
    this._defaultScopeSource = presentationCaps.scope.defaultSource;
    presentationResources.configure('hardware-scope', {
      available: presentationCaps.scope.hardwareScopeAvailable,
      selected: presentationCaps.scope.hardwareScopeAvailable,
    });
    presentationResources.configure('audio-fft', {
      available: presentationCaps.scope.audioFftAvailable,
      selected: presentationCaps.scope.audioFftAvailable,
    });
    presentationResources.configure('rx-audio', {
      available: caps.audio === true && caps.capabilities.includes('audio'),
      selected: true,
    });

    // 2. Register polling lifecycle with SystemController so connect/disconnect works.
    systemController.registerPolling(() =>
      startPolling((state) => { setRadioState(state); }, 1000),
    );

    // 3. Start polling and hand the stop handle to SystemController.
    const stopPolling = startPolling((state) => { setRadioState(state); }, 1000);
    systemController.setStopPolling(stopPolling);

    // 4. Open the control WebSocket channel.
    connect('/api/v1/ws');

    // 5. Subscribe to the events stream (re-sent automatically on reconnect by WsChannel).
    sendRaw({ type: 'subscribe', streams: ['events'] });

    // Only latch as started after the entire chain succeeds.
    let cleanupInFlight: Promise<void> | undefined;
    const cleanup = () => cleanupInFlight ??= (async () => {
      this._ended = true;
      this._rxAudioLease = null;
      this._dxSubscribers.clear();
      const unsubscribeDx = this._dxControlUnsubscribe;
      this._dxControlUnsubscribe = null;
      try { unsubscribeDx?.(); } finally {
        const stopScopeStatus = this._defaultScopeStop;
        this._defaultScopeStop = null;
        try { stopScopeStatus?.(); } finally {
          try { await presentationResources.teardown(); } finally { stopPolling(); }
        }
      }
    })();
    this._bootstrapCleanup = cleanup;
    return cleanup;
  }

  // ── Command dispatch ──

  /** Send a command to the radio backend. */
  send(name: string, params?: Record<string, unknown>): void {
    sendCommand(name, params ?? {});
  }

  // ── Optimistic state patches ──

  /** Apply an optimistic patch to the active receiver's state. */
  patchActiveReceiver(patch: Partial<ReceiverState>, lock?: boolean): void {
    patchActiveReceiver(patch, lock);
  }

  /** Apply an optimistic patch to the top-level radio state. */
  patchState(patch: Partial<ServerState>): void {
    patchRadioState(patch);
  }

  // ── Audio control ──

  acquireHardwareScope(consumer: string): ResourceLease {
    if (this._ended) throw new Error('frontend runtime is torn down');
    return presentationResources.acquire('hardware-scope', consumer);
  }

  releaseHardwareScope(lease: ResourceLease): boolean {
    return lease.resource === 'hardware-scope' && presentationResources.release(lease);
  }

  subscribeDx(handler: (message: DxMessage) => void): () => void {
    if (this._ended) throw new Error('frontend runtime is torn down');
    const id = this._nextDxSubscriber++;
    this._dxSubscribers.set(id, handler);
    if (this._dxControlUnsubscribe === null) {
      try {
        this._dxControlUnsubscribe = onMessage((message) => {
          if (message.type !== 'dx_spot' && message.type !== 'dx_spots') return;
          for (const subscriber of [...this._dxSubscribers.values()]) subscriber(message);
        });
      } catch (error) {
        this._dxSubscribers.delete(id);
        throw error;
      }
    }
    let active = true;
    return () => {
      if (!active) return;
      active = false;
      this._dxSubscribers.delete(id);
      if (this._dxSubscribers.size === 0) {
        const unsubscribe = this._dxControlUnsubscribe;
        this._dxControlUnsubscribe = null;
        unsubscribe?.();
      }
    };
  }

  setRxLive(live: boolean): void {
    if (live) {
      if (!this._ended && this._rxAudioLease === null) {
        this._rxAudioLease = presentationResources.acquire('rx-audio', 'presentation');
      }
      return;
    }

    const lease = this._rxAudioLease;
    this._rxAudioLease = null;
    if (lease) presentationResources.release(lease);
  }

  get rxEnabled(): boolean {
    return audioManager.rxEnabled;
  }

  setRxVolume(v: number): void {
    audioManager.setRxVolume(v);
  }

  async startTx(): Promise<string | null> {
    return audioManager.startTx();
  }

  stopTx(): void {
    audioManager.stopTx();
  }

  setVolume(v: number): void {
    setVolume(v);
  }

  setMuted(v: boolean): void {
    setMuted(v);
  }

  toggleMute(): void {
    toggleMute();
  }

  /** Adjust HTTP polling cadence (e.g. from battery monitor). */
  setPollingMultiplier(m: number): void {
    setPollingMultiplier(m);
  }
}

/** Singleton runtime instance. */
export const runtime = new FrontendRuntime();
