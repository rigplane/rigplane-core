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
import type { ScopeFrame } from '$lib/runtime/adapters/scope-adapter';
import type { ConnectionState, WsChannel } from '$lib/transport/ws-client';
import type { PresentationResourceDriver, PresentationResourceHost } from './resource-host';

export type { ScopeFrame };

type FrameHandler = (frame: ScopeFrame) => void;
type ChannelFactory = (name: string) => WsChannel;
type AudioFftHandle = Readonly<{ token: symbol }>;
type ChannelBinding = { channel: WsChannel; unsubscribe: () => void };
type HardwareHandle = Readonly<{ generation: number }>;
type HardwareBinding = { channel: WsChannel; unsubscribeBinary: () => void; unsubscribeState: () => void };

export class ScopeController {
  /** Latest parsed audio-scope frame (Svelte 5 reactive). */
  audioScopeFrame: ScopeFrame | null = $state(null);

  /** Capability selection is wired by a later slice; lifecycle state stays factual. */
  readonly hardwareScopeAvailable = false;
  readonly audioScopeAvailable = true;
  readonly activeScope: 'hardware' | 'audio-fft' | null = 'audio-fft';
  scopeFrame: ScopeFrame | null = $state(null);
  hardwareScopeDemanded = $state(false);
  hardwareScopeConnected = $state(false);
  hardwareScopeFrameLive = $state(false);

  private _subscribers = new Map<number, FrameHandler>();
  private _hardwareSubscribers = new Map<number, FrameHandler>();
  private _nextId = 0;
  private _bindings = new Map<AudioFftHandle, ChannelBinding>();
  private _activeHandle: AudioFftHandle | null = null;
  private _hardwareBindings = new Map<HardwareHandle, HardwareBinding>();
  private _activeHardwareHandle: HardwareHandle | null = null;
  private _hardwareGeneration = 0;
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

  registerPresentationDriver(host: Pick<PresentationResourceHost<unknown>, 'configure'>): void {
    host.configure('audio-fft', {
      available: this.audioScopeAvailable,
      selected: this.activeScope === 'audio-fft',
      driver: this.audioFftDriver,
    });
  }

  private _connect(): AudioFftHandle {
    const ch = this._getChannel('audio-scope');
    const handle = Object.freeze({ token: Symbol('audio-fft') });
    try {
      ch.connect('/api/v1/audio-scope');
      const unsubscribe = ch.onBinary((buf: ArrayBuffer) => {
        if (this._activeHandle !== handle) return;
        markScopeFrame();
        const frame = parseScopeFrame(buf);
        if (frame) {
          this.audioScopeFrame = frame;
          for (const h of this._subscribers.values()) {
            h(frame);
          }
        }
      });
      this._bindings.set(handle, { channel: ch, unsubscribe });
      this._activeHandle = handle;
      return handle;
    } catch (error) {
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
    try {
      binding.unsubscribe();
    } finally {
      try {
        if (!shared) binding.channel.disconnect();
      } finally {
        if (this._activeHandle === handle) {
          this._activeHandle = null;
          this.audioScopeFrame = null;
        }
      }
    }
  }

  private _connectHardware(): HardwareHandle {
    const channel = this._getChannel('scope');
    const handle = Object.freeze({ generation: ++this._hardwareGeneration });
    let unsubscribeBinary = () => {}, unsubscribeState = () => {};
    try {
      unsubscribeBinary = channel.onBinary((buf) => {
        if (this._activeHardwareHandle !== handle) return;
        const frame = parseScopeFrame(buf);
        if (!frame) return;
        markScopeFrame();
        this.scopeFrame = frame;
        this.hardwareScopeFrameLive = true;
        for (const subscriber of this._hardwareSubscribers.values()) subscriber(frame);
      });
      unsubscribeState = channel.onStateChange((state: ConnectionState) => {
        if (this._activeHardwareHandle !== handle) return;
        this.hardwareScopeConnected = state === 'connected';
        if (state !== 'connected') {
          this.hardwareScopeFrameLive = false;
          this.scopeFrame = null;
        }
      });
      channel.connect('/api/v1/scope');
    } catch (error) {
      try { unsubscribeBinary(); } finally { unsubscribeState(); }
      if (![...this._hardwareBindings.values()].some((item) => item.channel === channel)) {
        channel.disconnect();
      }
      throw error;
    }
    this._hardwareBindings.set(handle, { channel, unsubscribeBinary, unsubscribeState });
    this._activeHardwareHandle = handle;
    this.hardwareScopeDemanded = true;
    this.hardwareScopeConnected = channel.state === 'connected';
    this.hardwareScopeFrameLive = false;
    this.scopeFrame = null;
    return handle;
  }

  private _disconnectHardware(handle: HardwareHandle): void {
    const binding = this._hardwareBindings.get(handle);
    if (!binding) return;
    this._hardwareBindings.delete(handle);
    if (this._activeHardwareHandle === handle) {
      this._activeHardwareHandle = null;
      this.hardwareScopeDemanded = false;
      this.hardwareScopeConnected = false;
      this.hardwareScopeFrameLive = false;
      this.scopeFrame = null;
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
}

/** Singleton instance used by FrontendRuntime. */
export const scopeController = new ScopeController();
