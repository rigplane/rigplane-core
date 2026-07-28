/**
 * ScopeController — singleton owner of hardware and audio-scope WebSockets.
 *
 * Opens each scope channel lazily on first presentation demand and closes it
 * on last release. Parses binary frames with
 * `parseScopeFrame()` from `scope-adapter.ts` and stores the latest frame as
 * a reactive `$state` property that Svelte components can read via `$derived`.
 *
 * Satisfies ADR INV-2 (single scope ownership) and INV-5 (mount/unmount of
 * presentation panels must not change transport state).
 *
 * @see docs/plans/2026-04-12-target-frontend-architecture.md §ScopeController
 */

import { getChannel } from '$lib/transport/ws-client';
import { markScopeFrame } from '$lib/stores/connection.svelte';
import { parseScopeFrame } from '$lib/runtime/adapters/scope-adapter';
import { untrack } from 'svelte';
import type { ScopeFrame } from '$lib/runtime/adapters/scope-adapter';
import type { ConnectionState, WsChannel } from '$lib/transport/ws-client';
import type { PresentationResourceDriver, PresentationResourceHost } from './resource-host';

export type { ScopeFrame };

type FrameHandler = (frame: ScopeFrame) => void;
type ChannelFactory = (name: string) => WsChannel;
export type ScopeSource = 'hardware' | 'audio_fft';
export type ScopeHealth = Readonly<{
  demanded: boolean; transport: ConnectionState; frameSeen: boolean;
}>;
type HealthHandler = (source: ScopeSource, health: ScopeHealth) => void;
type AudioFftHandle = Readonly<{ token: symbol }>;
type ChannelBinding = {
  channel: WsChannel; unsubscribeBinary: () => void; unsubscribeState: () => void;
};
type HardwareHandle = Readonly<{ generation: number }>;

export class ScopeController {
  /** Latest parsed audio-scope frame (Svelte 5 reactive). */
  audioScopeFrame: ScopeFrame | null = $state(null);

  /** Capability selection is wired by a later slice; lifecycle state stays factual. */
  readonly hardwareScopeAvailable = false;
  readonly audioScopeAvailable = true;
  readonly activeScope: 'hardware' | 'audio-fft' | null = 'audio-fft';
  scopeFrame: ScopeFrame | null = $state(null);
  get hardwareScopeDemanded() { return this._health.hardware.demanded; }
  get hardwareScopeConnected() { return this._health.hardware.transport === 'connected'; }
  get hardwareScopeFrameLive() { return this._health.hardware.frameSeen; }

  private _subscribers = new Map<number, FrameHandler>();
  private _hardwareSubscribers = new Map<number, FrameHandler>();
  private _healthSubscribers = new Map<number, HealthHandler>();
  private _nextId = 0;
  private _bindings = new Map<AudioFftHandle, ChannelBinding>();
  private _activeHandle: AudioFftHandle | null = null;
  private _hardwareBindings = new Map<HardwareHandle, ChannelBinding>();
  private _activeHardwareHandle: HardwareHandle | null = null;
  private _hardwareGeneration = 0;
  private _registeredHosts = new WeakSet<object>();
  private _health: Record<ScopeSource, ScopeHealth> = $state({
    hardware: { demanded: false, transport: 'disconnected', frameSeen: false },
    audio_fft: { demanded: false, transport: 'disconnected', frameSeen: false },
  });
  private _getChannel: ChannelFactory;
  readonly audioFftDriver: PresentationResourceDriver<unknown> = {
    start: () => this._connect(),
    stop: (handle) => this._disconnect(handle as AudioFftHandle),
    dispose: (handle) => this._disconnect(handle as AudioFftHandle),
  };
  readonly hardwareScopeDriver: PresentationResourceDriver<unknown> = {
    start: () => this._connectHardware(),
    stop: (handle) => this._disconnectHardware(handle as HardwareHandle),
    dispose: (handle) => this._disconnectHardware(handle as HardwareHandle),
  };

  constructor(channelFactory: ChannelFactory = (name) => getChannel(name)) {
    this._getChannel = channelFactory;
  }

  /**
   * Subscribe to parsed scope frames.
   * Channel lifetime is owned by presentation resource demand.
   * Returns an `unsubscribe` function — call it to stop receiving frames.
   * Each subscribe() call creates an independent subscription, even for the same handler reference.
   */
  subscribe(handler: FrameHandler): () => void {
    const id = this._nextId++;
    this._subscribers.set(id, handler);
    return () => { this._subscribers.delete(id); };
  }

  subscribeHardware(handler: FrameHandler): () => void {
    const id = this._nextId++; this._hardwareSubscribers.set(id, handler);
    return () => { this._hardwareSubscribers.delete(id); };
  }

  snapshotHealth(source: ScopeSource): ScopeHealth {
    return Object.freeze({ ...this._readHealth(source) });
  }

  subscribeHealth(handler: HealthHandler): () => void {
    const id = this._nextId++; this._healthSubscribers.set(id, handler);
    return () => { this._healthSubscribers.delete(id); };
  }

  registerPresentationDriver(
    host: Pick<PresentationResourceHost<unknown>, 'configure'>,
    initialConfig?: { available: boolean; selected: boolean },
  ): void {
    if (this._registeredHosts.has(host)) return;
    this._registeredHosts.add(host);
    host.configure('audio-fft', {
      ...(initialConfig ?? {
        available: this.audioScopeAvailable,
        selected: this.activeScope === 'audio-fft',
      }),
      driver: this.audioFftDriver,
    });
  }

  private _connect(): AudioFftHandle {
    const ch = this._getChannel('audio-scope');
    const handle = Object.freeze({ token: Symbol('audio-fft') });
    let unsubscribeBinary = () => {}, unsubscribeState = () => {};
    this._activeHandle = handle;
    this.audioScopeFrame = null;
    this._setHealth('audio_fft', {
      demanded: true, transport: ch.state, frameSeen: false,
    });
    try {
      unsubscribeState = ch.onStateChange((state) => {
        if (this._activeHandle !== handle) return;
        if (state !== 'connected') this.audioScopeFrame = null;
        this._setHealth('audio_fft', {
          demanded: true, transport: state,
          frameSeen: state === 'connected' && this._readHealth('audio_fft').frameSeen,
        });
      });
      unsubscribeBinary = ch.onBinary((buf: ArrayBuffer) => {
        if (this._activeHandle !== handle || this._readHealth('audio_fft').transport !== 'connected') return;
        const frame = parseScopeFrame(buf);
        if (frame) {
          markScopeFrame();
          this.audioScopeFrame = frame;
          this._setHealth('audio_fft', { ...this._readHealth('audio_fft'), frameSeen: true });
          for (const h of this._subscribers.values()) {
            h(frame);
          }
        }
      });
      this._bindings.set(handle, { channel: ch, unsubscribeBinary, unsubscribeState });
      ch.connect('/api/v1/audio-scope');
      return handle;
    } catch (error) {
      this._bindings.delete(handle);
      try { unsubscribeBinary(); } finally { unsubscribeState(); }
      if (this._activeHandle === handle) {
        this._activeHandle = null;
        this._setHealth('audio_fft', {
          demanded: false, transport: 'disconnected', frameSeen: false,
        });
      }
      if (![...this._bindings.values()].some((binding) => binding.channel === ch)) {
        ch.disconnect();
      }
      throw error;
    }
  }

  private _disconnect(handle: AudioFftHandle): void {
    const binding = this._bindings.get(handle);
    if (!binding) return;
    this._bindings.delete(handle);
    const shared = [...this._bindings.values()].some(
      (candidate) => candidate.channel === binding.channel,
    );
    if (this._activeHandle === handle) {
      this._activeHandle = null;
      this.audioScopeFrame = null;
      this._setHealth('audio_fft', {
        demanded: false, transport: 'disconnected', frameSeen: false,
      });
    }
    try {
      binding.unsubscribeBinary();
    } finally {
      try {
        binding.unsubscribeState();
      } finally {
        if (!shared) binding.channel.disconnect();
      }
    }
  }

  private _connectHardware(): HardwareHandle {
    const channel = this._getChannel('scope');
    const handle = Object.freeze({ generation: ++this._hardwareGeneration });
    let unsubscribeBinary = () => {}, unsubscribeState = () => {};
    this._activeHardwareHandle = handle;
    this.scopeFrame = null;
    this._setHealth('hardware', {
      demanded: true, transport: channel.state, frameSeen: false,
    });
    try {
      unsubscribeBinary = channel.onBinary((buf) => {
        if (
          this._activeHardwareHandle !== handle
          || this._readHealth('hardware').transport !== 'connected'
        ) return;
        const frame = parseScopeFrame(buf);
        if (!frame) return;
        markScopeFrame();
        this.scopeFrame = frame;
        this._setHealth('hardware', { ...this._readHealth('hardware'), frameSeen: true });
        for (const subscriber of this._hardwareSubscribers.values()) subscriber(frame);
      });
      unsubscribeState = channel.onStateChange((state: ConnectionState) => {
        if (this._activeHardwareHandle !== handle) return;
        if (state !== 'connected') this.scopeFrame = null;
        this._setHealth('hardware', {
          demanded: true, transport: state,
          frameSeen: state === 'connected' && this._readHealth('hardware').frameSeen,
        });
      });
      this._hardwareBindings.set(handle, { channel, unsubscribeBinary, unsubscribeState });
      channel.connect('/api/v1/scope');
    } catch (error) {
      this._hardwareBindings.delete(handle);
      try { unsubscribeBinary(); } finally { unsubscribeState(); }
      if (this._activeHardwareHandle === handle) {
        this._activeHardwareHandle = null;
        this._setHealth('hardware', {
          demanded: false, transport: 'disconnected', frameSeen: false,
        });
      }
      if (![...this._hardwareBindings.values()].some((item) => item.channel === channel)) {
        channel.disconnect();
      }
      throw error;
    }
    return handle;
  }

  private _disconnectHardware(handle: HardwareHandle): void {
    const binding = this._hardwareBindings.get(handle);
    if (!binding) return;
    this._hardwareBindings.delete(handle);
    if (this._activeHardwareHandle === handle) {
      this._activeHardwareHandle = null;
      this.scopeFrame = null;
      this._setHealth('hardware', {
        demanded: false, transport: 'disconnected', frameSeen: false,
      });
    }
    try {
      binding.unsubscribeBinary();
    } finally {
      try { binding.unsubscribeState(); } finally {
        if (![...this._hardwareBindings.values()].some((item) => item.channel === binding.channel)) {
          binding.channel.disconnect();
        }
      }
    }
  }

  private _setHealth(source: ScopeSource, next: ScopeHealth): void {
    const current = this._readHealth(source);
    if (
      current.demanded === next.demanded
      && current.transport === next.transport
      && current.frameSeen === next.frameSeen
    ) return;
    this._health[source] = next;
    const snapshot = this.snapshotHealth(source);
    for (const listener of this._healthSubscribers.values()) listener(source, snapshot);
  }

  private _readHealth(source: ScopeSource): ScopeHealth {
    return untrack(() => this._health[source]);
  }
}

/** Singleton instance used by FrontendRuntime. */
export const scopeController = new ScopeController();
