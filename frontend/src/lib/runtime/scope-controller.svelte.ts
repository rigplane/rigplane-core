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
import {
  parseScopeFrame,
  qualifyScopeFrameEnvelope,
  SCOPE_FRAME_SILENCE_MS,
} from '$lib/runtime/adapters/scope-adapter';
import { untrack } from 'svelte';
import type {
  QualifiedScopeFrameEnvelope,
  ScopeFrameProjectionAuthority,
  ScopeFrame,
} from '$lib/runtime/adapters/scope-adapter';
import type {
  ConnectionState,
  ControlSessionTransition,
  WsChannel,
} from '$lib/transport/ws-client';
import type { PresentationResourceDriver, PresentationResourceHost } from './resource-host';

export type { ScopeFrame };

type FrameHandler = (frame: ScopeFrame) => void;
type ChannelFactory = (name: string) => WsChannel;
export type ScopeSource = 'hardware' | 'audio_fft';
export type ScopeHealth = Readonly<{
  demanded: boolean; transport: ConnectionState; frameSeen: boolean;
}>;
type HealthHandler = (source: ScopeSource, health: ScopeHealth) => void;
type EvidenceHandler = () => void;
type AudioFftHandle = Readonly<{ token: symbol }>;
type ChannelBinding = {
  channel: WsChannel;
  authorityRevision: number;
  unsubscribeBinary: () => void;
  unsubscribeState: () => void;
  unsubscribeSession: () => void;
};
type HardwareHandle = Readonly<{ generation: number }>;
type TimerHandle = ReturnType<typeof setTimeout>;
export type ScopeFrameCanonicalAuthority = Readonly<{
  source: ScopeSource;
  receiver: 0 | 1 | null;
  providerGeneration: number | null;
}>;
export type ScopeFrameEvidence = Readonly<{
  envelope: QualifiedScopeFrameEnvelope | null;
  authority: ScopeFrameProjectionAuthority;
}>;
export interface ScopeFrameTiming {
  now(): number;
  setTimeout(callback: () => void, delayMs: number): TimerHandle;
  clearTimeout(handle: TimerHandle): void;
}

const DEFAULT_TIMING: ScopeFrameTiming = {
  now: () => performance.now(),
  setTimeout: (callback, delayMs) => setTimeout(callback, delayMs),
  clearTimeout: (handle) => clearTimeout(handle),
};

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
  private _evidenceSubscribers = new Map<number, EvidenceHandler>();
  private _nextId = 0;
  private _bindings = new Map<AudioFftHandle, ChannelBinding>();
  private _activeHandle: AudioFftHandle | null = null;
  private _hardwareBindings = new Map<HardwareHandle, ChannelBinding>();
  private _activeHardwareHandle: HardwareHandle | null = null;
  private _hardwareGeneration = 0;
  private _authorityRevision = 0;
  private _frameAuthority: ScopeFrameCanonicalAuthority | null = null;
  private _frameEnvelope: QualifiedScopeFrameEnvelope | null = null;
  private _acceptedSequence = 0;
  private _arrivals: Record<ScopeSource, Readonly<{
    receivedAt: number; acceptedSequence: number;
  }> | null> = { hardware: null, audio_fft: null };
  private _expiryTimers: Record<ScopeSource, TimerHandle | null> = {
    hardware: null, audio_fft: null,
  };
  private _registeredHosts = new WeakSet<object>();
  private _health: Record<ScopeSource, ScopeHealth> = $state({
    hardware: { demanded: false, transport: 'disconnected', frameSeen: false },
    audio_fft: { demanded: false, transport: 'disconnected', frameSeen: false },
  });
  private _getChannel: ChannelFactory;
  private _timing: ScopeFrameTiming;
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

  constructor(
    channelFactory: ChannelFactory = (name) => getChannel(name),
    timing: ScopeFrameTiming = DEFAULT_TIMING,
  ) {
    this._getChannel = channelFactory;
    this._timing = timing;
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

  /**
   * Installs the one canonical display identity. Changing any identity member
   * retires the prior envelope synchronously. A provider-generation change
   * also retires the channel revision and rebinds the selected demanded source
   * without changing the resource host's handle or adopting old-provider pixels.
   */
  setFrameAuthority(authority: ScopeFrameCanonicalAuthority | null): void {
    const normalized = authority
      && (authority.source === 'hardware' || authority.source === 'audio_fft')
      && (authority.receiver === null || authority.receiver === 0 || authority.receiver === 1)
      && (authority.providerGeneration === null
        || (Number.isSafeInteger(authority.providerGeneration) && authority.providerGeneration >= 0))
      ? Object.freeze({ ...authority }) : null;
    const current = this._frameAuthority;
    if (current?.source === normalized?.source
      && current?.receiver === normalized?.receiver
      && current?.providerGeneration === normalized?.providerGeneration) return;
    if (current?.providerGeneration !== normalized?.providerGeneration) {
      this._authorityRevision += 1;
    }
    this._frameAuthority = normalized;
    this._frameEnvelope = null;
    this._notifyEvidence();
    if (normalized?.receiver != null && normalized.providerGeneration != null) {
      if (normalized.source === 'hardware') {
        this._rebind('hardware', this._hardwareBindings, this._activeHardwareHandle);
      } else {
        this._rebind('audio_fft', this._bindings, this._activeHandle);
      }
    }
  }

  snapshotFrameEvidence(): ScopeFrameEvidence {
    const authority = this._frameAuthority;
    const source = authority?.source ?? 'hardware';
    const health = this._readHealth(source);
    const binding = source === 'hardware'
      ? this._activeHardwareHandle && this._hardwareBindings.get(this._activeHardwareHandle)
      : this._activeHandle && this._bindings.get(this._activeHandle);
    const epoch = binding?.channel.sessionEpoch;
    return Object.freeze({
      envelope: this._frameEnvelope,
      authority: Object.freeze({
        source,
        receiver: authority?.receiver ?? null,
        providerGeneration: authority?.providerGeneration ?? null,
        transportEpoch: Number.isSafeInteger(epoch) && (epoch as number) > 0
          ? epoch as number : null,
        demanded: authority !== null && health.demanded,
        transport: health.transport,
        nowMonotonic: this._timing.now(),
      }),
    });
  }

  subscribeFrameEvidence(handler: EvidenceHandler): () => void {
    const id = this._nextId++;
    this._evidenceSubscribers.set(id, handler);
    return () => { this._evidenceSubscribers.delete(id); };
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
    const channel = this._getChannel('audio-scope');
    const handle = Object.freeze({ token: Symbol('audio-fft') });
    this._activeHandle = handle;
    return this._installBinding('audio_fft', this._bindings, handle, channel);
  }

  private _rebind<H extends AudioFftHandle | HardwareHandle>(
    source: ScopeSource, bindings: Map<H, ChannelBinding>, handle: H | null,
  ): void {
    const old = handle && bindings.get(handle);
    if (!handle || !old || old.authorityRevision === this._authorityRevision) return;
    this._invalidateSource(source);
    bindings.delete(handle); // Retained callbacks lose authority before transport teardown.
    try {
      try { this._unsubscribeBinding(old); } finally { old.channel.disconnect(); }
      this._setHealth(source, { demanded: true, transport: 'disconnected', frameSeen: false });
      this._installBinding(source, bindings, handle, old.channel, true);
    } catch (error) {
      // The host still owns this exact handle, including after failed registration/connect.
      if (!bindings.has(handle)) bindings.set(handle, this._emptyBinding(old.channel, old.authorityRevision));
      this._setHealth(source, { demanded: true, transport: 'disconnected', frameSeen: false });
      console.warn('Scope authority rebind failed', error);
    }
  }

  private _emptyBinding(channel: WsChannel, authorityRevision: number): ChannelBinding {
    return { channel, authorityRevision, unsubscribeBinary: () => {}, unsubscribeState: () => {}, unsubscribeSession: () => {} };
  }

  private _unsubscribeBinding(binding: ChannelBinding): void {
    try { binding.unsubscribeBinary(); } finally {
      try { binding.unsubscribeState(); } finally { binding.unsubscribeSession(); }
    }
  }

  private _installBinding<H extends AudioFftHandle | HardwareHandle>(
    source: ScopeSource, bindings: Map<H, ChannelBinding>, handle: H,
    channel: WsChannel, preserveDemand = false,
  ): H {
    const authorityRevision = this._authorityRevision;
    const binding = this._emptyBinding(channel, authorityRevision);
    const current = () => bindings.get(handle) === binding
      && (source === 'hardware' ? this._activeHardwareHandle : this._activeHandle) === handle;
    bindings.set(handle, binding);
    this._invalidateSource(source);
    this._setHealth(source, { demanded: true, transport: channel.state, frameSeen: false });
    try {
      binding.unsubscribeBinary = channel.onBinary((buf) => {
        if (!current() || this._readHealth(source).transport !== 'connected') return;
        const frame = parseScopeFrame(buf);
        if (!frame) {
          this._invalidateSource(source, authorityRevision === this._authorityRevision);
          return;
        }
        if (this._acceptFrame(source, channel, authorityRevision, frame, buf)) {
          const subscribers = source === 'hardware' ? this._hardwareSubscribers : this._subscribers;
          for (const subscriber of subscribers.values()) subscriber(frame);
        }
      });
      binding.unsubscribeState = channel.onStateChange((state) => {
        if (!current()) return;
        if (state !== 'connected') this._invalidateSource(source);
        this._setHealth(source, {
          demanded: true, transport: state,
          frameSeen: state === 'connected' && this._arrivalIsLive(source),
        });
      });
      binding.unsubscribeSession = this._subscribeSession(channel, (transition) => {
        if (current()) this._sessionTransition(source, handle, transition);
      });
      channel.connect(source === 'hardware' ? '/api/v1/scope' : '/api/v1/audio-scope');
      return handle;
    } catch (error) {
      bindings.delete(handle);
      this._unsubscribeBinding(binding);
      const active = source === 'hardware' ? this._activeHardwareHandle : this._activeHandle;
      if (active === handle) {
        if (!preserveDemand) {
          if (source === 'hardware') this._activeHardwareHandle = null;
          else this._activeHandle = null;
        }
        this._invalidateSource(source);
        this._setHealth(source, { demanded: preserveDemand, transport: 'disconnected', frameSeen: false });
      }
      if (![...bindings.values()].some((item) => item.channel === channel)) channel.disconnect();
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
      this._invalidateSource('audio_fft');
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
        try { binding.unsubscribeSession(); } finally {
          if (!shared) binding.channel.disconnect();
        }
      }
    }
  }

  private _connectHardware(): HardwareHandle {
    const channel = this._getChannel('scope');
    const handle = Object.freeze({ generation: ++this._hardwareGeneration });
    this._activeHardwareHandle = handle;
    return this._installBinding('hardware', this._hardwareBindings, handle, channel);
  }

  private _disconnectHardware(handle: HardwareHandle): void {
    const binding = this._hardwareBindings.get(handle);
    if (!binding) return;
    this._hardwareBindings.delete(handle);
    if (this._activeHardwareHandle === handle) {
      this._activeHardwareHandle = null;
      this._invalidateSource('hardware');
      this._setHealth('hardware', {
        demanded: false, transport: 'disconnected', frameSeen: false,
      });
    }
    try {
      binding.unsubscribeBinary();
    } finally {
      try { binding.unsubscribeState(); } finally {
        try { binding.unsubscribeSession(); } finally {
          if (![...this._hardwareBindings.values()].some((item) => item.channel === binding.channel)) {
            binding.channel.disconnect();
          }
        }
      }
    }
  }

  private _sessionTransition(
    source: ScopeSource,
    handle: AudioFftHandle | HardwareHandle,
    transition: ControlSessionTransition,
  ): void {
    const active = source === 'hardware' ? this._activeHardwareHandle : this._activeHandle;
    if (active !== handle) return;
    const envelope = this._frameEnvelope;
    if (transition.state !== 'connected'
      || (envelope?.source === source && envelope.transportEpoch !== transition.epoch)) {
      this._invalidateSource(source);
    } else {
      this._notifyEvidence();
    }
  }

  private _subscribeSession(
    channel: WsChannel,
    handler: (transition: ControlSessionTransition) => void,
  ): () => void {
    return typeof channel.onSessionTransition === 'function'
      ? channel.onSessionTransition(handler) : () => {};
  }

  private _acceptFrame(
    source: ScopeSource,
    channel: WsChannel,
    authorityRevision: number,
    frame: ScopeFrame,
    buffer: ArrayBuffer,
  ): boolean {
    const receivedAt = this._timing.now();
    const transportEpoch = channel.sessionEpoch;
    if (!Number.isFinite(receivedAt) || receivedAt < 0) {
      this._invalidateSource(source, authorityRevision === this._authorityRevision);
      return false;
    }
    const acceptedSequence = ++this._acceptedSequence;
    const arrival = Object.freeze({ receivedAt, acceptedSequence });
    this._arrivals[source] = arrival;
    if (source === 'hardware') this.scopeFrame = frame;
    else this.audioScopeFrame = frame;
    markScopeFrame();
    this._setHealth(source, { ...this._readHealth(source), frameSeen: true });
    this._armExpiry(source, arrival);

    const authority = this._frameAuthority;
    if (authorityRevision !== this._authorityRevision || authority?.source !== source) return true;
    if (!Number.isSafeInteger(transportEpoch) || transportEpoch <= 0) {
      this._frameEnvelope = null;
      this._notifyEvidence();
      return true;
    }
    if ((authority.receiver !== 0 && authority.receiver !== 1)
      || !Number.isSafeInteger(authority.providerGeneration)
      || (authority.providerGeneration as number) < 0) {
      this._frameEnvelope = null;
      this._notifyEvidence();
      return true;
    }
    const view = new DataView(buffer);
    this._frameEnvelope = qualifyScopeFrameEnvelope(frame, {
      source,
      receiver: authority.receiver,
      providerGeneration: authority.providerGeneration as number,
      transportEpoch,
      receivedAt,
      acceptedSequence,
    }, view.getUint16(12, true));
    this._notifyEvidence();
    return true;
  }

  private _armExpiry(
    source: ScopeSource,
    arrival: Readonly<{ receivedAt: number; acceptedSequence: number }>,
  ): void {
    this._clearExpiry(source);
    const wake = () => {
      const current = this._arrivals[source];
      if (!current || current.acceptedSequence !== arrival.acceptedSequence) return;
      const age = this._timing.now() - arrival.receivedAt;
      if (!Number.isFinite(age) || age < 0 || age >= SCOPE_FRAME_SILENCE_MS) {
        this._expiryTimers[source] = null;
        if (source === 'hardware') this.scopeFrame = null;
        else this.audioScopeFrame = null;
        this._setHealth(source, { ...this._readHealth(source), frameSeen: false });
        if (this._frameEnvelope?.source === source
          && this._frameEnvelope.acceptedSequence === arrival.acceptedSequence) {
          this._notifyEvidence();
        }
        return;
      }
      this._expiryTimers[source] = this._timing.setTimeout(
        wake, SCOPE_FRAME_SILENCE_MS - age,
      );
    };
    this._expiryTimers[source] = this._timing.setTimeout(wake, SCOPE_FRAME_SILENCE_MS);
  }

  private _clearExpiry(source: ScopeSource): void {
    const timer = this._expiryTimers[source];
    if (timer !== null) this._timing.clearTimeout(timer);
    this._expiryTimers[source] = null;
  }

  private _invalidateSource(source: ScopeSource, clearEnvelope = true): void {
    this._clearExpiry(source);
    this._arrivals[source] = null;
    if (source === 'hardware') this.scopeFrame = null;
    else this.audioScopeFrame = null;
    if (clearEnvelope && this._frameEnvelope?.source === source) this._frameEnvelope = null;
    const current = this._readHealth(source);
    if (current.frameSeen) this._setHealth(source, { ...current, frameSeen: false });
    this._notifyEvidence();
  }

  private _arrivalIsLive(source: ScopeSource): boolean {
    const arrival = this._arrivals[source];
    if (!arrival) return false;
    const age = this._timing.now() - arrival.receivedAt;
    return Number.isFinite(age) && age >= 0 && age < SCOPE_FRAME_SILENCE_MS;
  }

  private _notifyEvidence(): void {
    for (const listener of [...this._evidenceSubscribers.values()]) {
      try {
        listener();
      } catch (error) {
        console.warn('Scope frame evidence subscriber failed', error);
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
    for (const listener of [...this._healthSubscribers.values()]) {
      try {
        listener(source, snapshot);
      } catch (error) {
        console.warn('Scope health subscriber failed', error);
      }
    }
  }

  private _readHealth(source: ScopeSource): ScopeHealth {
    return untrack(() => this._health[source]);
  }
}

/** Singleton instance used by FrontendRuntime. */
export const scopeController = new ScopeController();
