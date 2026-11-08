/**
 * ScopeController — singleton owner of the hardware and audio-scope
 * WebSockets (ADR INV-2, INV-5).
 *
 * MOR-4270: each source exposes ONE shared `ScopeStream` for the whole
 * page. The stream parses frames, keeps the latest frame per slot in a
 * non-reactive map and publishes the qualified `geometry` store; this
 * controller keeps only channel lifetime, demand health, trace receipts
 * and the shared liveness clock (stale transitions; no per-frame timers).
 *
 * @see docs/plans/2026-04-12-target-frontend-architecture.md §ScopeController
 */

import { getChannel } from '$lib/transport/ws-client';
import { markScopeFrame } from '$lib/stores/connection.svelte';
import { SCOPE_FRAME_SILENCE_MS } from '$lib/runtime/adapters/scope-adapter';
import { ScopeStream } from './scope-stream';
import type { ScopeMessageSource } from './scope-stream';
import { untrack } from 'svelte';
import type {
  ConnectionState,
  ControlSessionTransition,
  WsChannel,
} from '$lib/transport/ws-client';
import type { PresentationResourceDriver, PresentationResourceHost } from './resource-host';
import type { TraceReceipt } from './scope-render-trace';

export type ScopeSource = 'hardware' | 'audio_fft';
export type ScopeHealth = Readonly<{
  demanded: boolean; transport: ConnectionState; frameSeen: boolean;
}>;
type HealthHandler = (source: ScopeSource, health: ScopeHealth) => void;
type AudioFftHandle = Readonly<{ token: symbol }>;
type HardwareHandle = Readonly<{ generation: number }>;
export type ScopeFrameAuthority = Readonly<{ source: ScopeSource; receiver: 0 | 1 | null; providerGeneration: number }>;
type ChannelBinding = {
  source: ScopeSource;
  revision: number;
  active: boolean;
  channel: WsChannel;
  unsubscribeState: () => void;
  unsubscribeSession: () => void;
};
type TimerHandle = ReturnType<typeof setTimeout>;
type IntervalHandle = ReturnType<typeof setInterval>;
export interface ScopeFrameTiming {
  now(): number;
  setTimeout(callback: () => void, delayMs: number): TimerHandle;
  clearTimeout(handle: TimerHandle): void;
  setInterval(callback: () => void, delayMs: number): IntervalHandle;
  clearInterval(handle: IntervalHandle): void;
}

const DEFAULT_TIMING: ScopeFrameTiming = {
  now: () => performance.now(),
  setTimeout: (callback, delayMs) => setTimeout(callback, delayMs),
  clearTimeout: (handle) => clearTimeout(handle),
  setInterval: (callback, delayMs) => setInterval(callback, delayMs),
  clearInterval: (handle) => clearInterval(handle),
};

const LIVENESS_TICK_MS = 250;

export class ScopeController {
  get hardwareScopeDemanded() { return this._health.hardware.demanded; }
  get hardwareScopeConnected() { return this._health.hardware.transport === 'connected'; }
  get hardwareScopeFrameLive() { return this._health.hardware.frameSeen; }

  private readonly _streams: Partial<Record<ScopeSource, ScopeStream>> = {};
  private _authority: ScopeFrameAuthority | null = null;
  private _additionalSources = new Set<ScopeSource>();
  private _authorityRevision = 0;
  private readonly _slotArrivals: Record<ScopeSource, Map<string, number>> = { hardware: new Map(), audio_fft: new Map() };
  private _healthSubscribers = new Map<number, HealthHandler>();
  private _traceObservers: Map<number, (receipt: TraceReceipt) => void> | null = null;
  private _nextId = 0;
  private _bindings = new Map<AudioFftHandle | HardwareHandle, ChannelBinding>();
  private _hardwareGeneration = 0;
  private _acceptedSequence = 0;
  private _arrivals: Record<ScopeSource, number | null> = { hardware: null, audio_fft: null };
  private _clockHandle: IntervalHandle | null = null;
  private _registeredHosts = new WeakSet<object>();
  private _health: Record<ScopeSource, ScopeHealth> = $state({
    hardware: { demanded: false, transport: 'disconnected', frameSeen: false },
    audio_fft: { demanded: false, transport: 'disconnected', frameSeen: false },
  });
  private _getChannel: (name: string) => WsChannel;
  private _timing: ScopeFrameTiming;
  readonly audioFftDriver: PresentationResourceDriver<unknown> = {
    start: () => this._connect('audio_fft'),
    stop: (handle) => this._disconnect(handle as AudioFftHandle),
    dispose: (handle) => this._disconnect(handle as AudioFftHandle),
  };
  readonly hardwareScopeDriver: PresentationResourceDriver<unknown> = {
    start: () => this._connect('hardware'),
    stop: (handle) => this._disconnect(handle as HardwareHandle),
    dispose: (handle) => this._disconnect(handle as HardwareHandle),
  };

  constructor(
    channelFactory: (name: string) => WsChannel = (name) => getChannel(name),
    timing: ScopeFrameTiming = DEFAULT_TIMING,
  ) {
    this._getChannel = channelFactory;
    this._timing = timing;
  }

  /** One shared stream per source; sinks survive demand stop/restart and provider rebind. */
  stream(source: ScopeSource): ScopeStream {
    if (!this._streams[source]) this._streams[source] = this._createStream(source);
    return this._streams[source]!;
  }

  /** Control-plane identity qualifies geometry; pixel delivery stays on the shared stream. */
  setFrameAuthority(authority: ScopeFrameAuthority | null, additionalSources: readonly ScopeSource[] = []): void {
    const previous = this._authority;
    const additional = new Set(authority ? additionalSources.filter(source => source !== authority.source) : []);
    const sameAuthority = previous?.source === authority?.source && previous?.receiver === authority?.receiver
      && previous?.providerGeneration === authority?.providerGeneration;
    if (sameAuthority && additional.size === this._additionalSources.size
      && [...additional].every(source => this._additionalSources.has(source))) return;
    const providerChanged = previous?.providerGeneration !== authority?.providerGeneration;
    if (providerChanged) {
      this._authorityRevision += 1;
      for (const stream of Object.values(this._streams)) stream?.reset();
    }
    const newlyQualified = [...additional].filter(source => !this._additionalSources.has(source));
    this._additionalSources = additional;
    this._authority = authority === null ? null : Object.freeze({ ...authority });
    if (!sameAuthority && previous) this._streams[previous.source]?.reset();
    if (!authority) return;
    const stream = this.stream(authority.source);
    if (!sameAuthority) {
      stream.setSlotFilter(authority.receiver === null ? null
        : `${authority.source === 'hardware' ? 'scope' : 'audio-scope'}:${authority.receiver}`);
      stream.reset();
    }
    for (const source of [authority.source, ...additional]) {
      if (newlyQualified.includes(source)) this._streams[source]?.reset();
      const binding = [...this._bindings.values()].find(item => item.source === source);
      if ((source === authority.source && authority.receiver === null)
        || !binding || binding.revision === this._authorityRevision) continue;
      this._retire(binding);
      try { this._installBinding(binding); }
      catch (error) {
        this._setHealth(source, { demanded: true, transport: 'disconnected', frameSeen: false });
        console.warn('Scope authority rebind failed', error);
      }
    }
  }

  private _channelFor(source: ScopeSource): WsChannel {
    return this._getChannel(source === 'hardware' ? 'scope' : 'audio-scope');
  }

  /** Optional diagnostic observation; never acquires a channel or changes frame subscribers. */
  subscribeTraceReceipt(observer: (receipt: TraceReceipt) => void): () => void {
    const id = this._nextId++;
    (this._traceObservers ??= new Map()).set(id, observer);
    return () => {
      this._traceObservers?.delete(id);
      if (this._traceObservers?.size === 0) this._traceObservers = null;
    };
  }

  private _publishTraceReceipt(receipt: TraceReceipt): void {
    const observers = this._traceObservers;
    if (!observers) return;
    for (const observer of [...observers.values()]) {
      try { observer(receipt); } catch { /* Diagnostics never interrupt delivery. */ }
    }
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
      ...(initialConfig ?? { available: true, selected: true }),
      driver: this.audioFftDriver,
    });
  }

  private _createStream(source: ScopeSource): ScopeStream {
    const messageSource: ScopeMessageSource = {
      subscribe: (sink) => {
        const binding = [...this._bindings.values()].find(item => item.source === source);
        if (!binding) return () => {};
        const revision = binding.revision;
        return binding.channel.onBinary((buffer) => {
          if (!binding.active || binding.revision !== revision || !this._sourceDemanded(source)
            || this._readHealth(source).transport !== 'connected') return;
          const now = this._timing.now();
          if (!Number.isFinite(now) || now < 0) {
            this._streams[source]?.reset();
            this._arrivals[source] = null; this._slotArrivals[source].clear();
            this._publishTraceReceipt({ kind: 'rejected', source, transportEpoch: binding.channel.sessionEpoch,
              callbackAt: now, reason: 'clock' });
            this._setHealth(source, { ...this._readHealth(source), frameSeen: false });
            return;
          }
          sink(buffer);
        });
      },
      close: () => {},
    };
    const stream = new ScopeStream(
      messageSource,
      { source: source === 'hardware' ? 'radio' : 'audio_fft', sourceId: source === 'hardware' ? 'scope' : 'audio-scope', epoch: 0 },
      'borrowed',
      () => {
        const binding = [...this._bindings.values()].find(item => item.source === source);
        this._publishTraceReceipt({ kind: 'rejected', source,
          transportEpoch: binding?.channel.sessionEpoch ?? 0, callbackAt: this._timing.now(), reason: 'parse' });
        this._arrivals[source] = null; this._slotArrivals[source].clear();
        this._setHealth(source, { ...this._readHealth(source), frameSeen: false });
      },
    );
    stream.subscribe((frame, slotKey) => {
      const receivedAt = this._timing.now();
      const acceptedSequence = ++this._acceptedSequence;
      this._arrivals[source] = receivedAt;
      this._slotArrivals[source].set(slotKey, receivedAt);
      markScopeFrame();
      this._setHealth(source, { ...this._readHealth(source), frameSeen: true });
      this._ensureClock();
      if (this._traceObservers) this._publishTraceReceipt({ kind: 'accepted', frame,
        token: { source, transportEpoch: frame.epoch, wireSequence: frame.seq, acceptedSequence },
        callbackAt: receivedAt, acceptedAt: receivedAt });
    });
    stream.pause();
    return stream;
  }

  private _connect(source: ScopeSource): AudioFftHandle | HardwareHandle {
    const handle = source === 'hardware'
      ? Object.freeze({ generation: ++this._hardwareGeneration }) as HardwareHandle
      : Object.freeze({ token: Symbol('audio-fft') }) as AudioFftHandle;
    const shared = [...this._bindings.values()].find(item => item.source === source);
    if (shared) { this._bindings.set(handle, shared); return handle; }
    const binding: ChannelBinding = {
      channel: this._channelFor(source), source, revision: this._authorityRevision, active: false,
      unsubscribeState: () => {}, unsubscribeSession: () => {},
    };
    this._bindings.set(handle, binding);
    try { this._installBinding(binding); return handle; }
    catch (error) {
      this._bindings.delete(handle);
      this._setHealth(source, { demanded: false, transport: 'disconnected', frameSeen: false });
      throw error;
    }
  }

  private _installBinding(binding: ChannelBinding): void {
    const { source, channel } = binding;
    const stream = this.stream(source);
    binding.revision = this._authorityRevision;
    binding.active = true;
    const revision = binding.revision;
    this._setHealth(source, { demanded: true, transport: channel.state, frameSeen: false });
    try {
      binding.unsubscribeState = channel.onStateChange((state) => {
        if (!binding.active || binding.revision !== revision) return;
        if (state !== 'connected') {
          stream.reset(); this._arrivals[source] = null; this._slotArrivals[source].clear();
        }
        this._setHealth(source, { demanded: true, transport: state,
          frameSeen: state === 'connected' && this._arrivalIsLive(source) });
        this._ensureClock();
      });
      binding.unsubscribeSession = this._subscribeSession(source, channel, (transition) => {
        if (!binding.active || binding.revision !== revision) return;
        if (transition.state === 'connected') {
          const epoch = channel.sessionEpoch;
          stream.setSessionEpoch(Number.isSafeInteger(epoch) && epoch > 0 ? epoch : 0);
        } else stream.reset();
      });
      stream.resume();
      channel.connect(source === 'hardware' ? '/api/v1/scope' : '/api/v1/audio-scope');
      if (channel.state === 'connected' && Number.isSafeInteger(channel.sessionEpoch) && channel.sessionEpoch > 0) {
        stream.setSessionEpoch(channel.sessionEpoch);
      }
    } catch (error) { this._retire(binding); throw error; }
  }

  private _retire(binding: ChannelBinding): void {
    const { source, channel } = binding;
    binding.active = false;
    try { this._detachBinding(binding); }
    finally {
      try { this._streams[source]?.pause(); }
      finally {
        channel.disconnect(); this._arrivals[source] = null; this._slotArrivals[source].clear();
        this._setHealth(source, { demanded: this._sourceDemanded(source), transport: 'disconnected', frameSeen: false });
        this._ensureClock();
      }
    }
  }

  private _disconnect(handle: AudioFftHandle | HardwareHandle): void {
    const binding = this._bindings.get(handle);
    if (!binding) return;
    this._bindings.delete(handle);
    if ([...this._bindings.values()].includes(binding)) return;
    this._retire(binding);
  }

  private _detachBinding(binding: ChannelBinding): void {
    try { binding.unsubscribeState(); } finally { binding.unsubscribeSession(); }
  }

  private _subscribeSession(
    source: ScopeSource,
    channel: WsChannel,
    handler: (transition: ControlSessionTransition) => void,
  ): () => void {
    const wrapped = typeof channel.onSessionTransition === 'function'
      ? channel.onSessionTransition(handler)
      : () => {};
    return wrapped;
  }

  private _sourceDemanded(source: ScopeSource): boolean {
    return [...this._bindings.values()].some((binding) => binding.source === source);
  }

  private _arrivalIsLive(source: ScopeSource): boolean {
    const receivedAt = this._arrivals[source];
    if (receivedAt === null) return false;
    const age = this._timing.now() - receivedAt;
    return Number.isFinite(age) && age >= 0 && age < SCOPE_FRAME_SILENCE_MS;
  }

  /** One shared liveness clock: stale transitions without per-frame timers. */
  private _ensureClock(): void {
    const anyLive = (['hardware', 'audio_fft'] as const).some((source) => {
      const health = this._readHealth(source);
      return health.demanded && health.transport === 'connected';
    });
    if (anyLive && this._clockHandle === null) {
      this._clockHandle = this._timing.setInterval(() => this._tick(), LIVENESS_TICK_MS);
    } else if (!anyLive && this._clockHandle !== null) {
      this._timing.clearInterval(this._clockHandle);
      this._clockHandle = null;
    }
  }

  private _tick(): void {
    for (const source of ['hardware', 'audio_fft'] as const) {
      const health = this._readHealth(source);
      if (!health.demanded || health.transport !== 'connected') continue;
      const live = this._arrivalIsLive(source);
      const stream = this._streams[source]!;
      const selectedAt = stream.selectedSlot === null ? undefined : this._slotArrivals[source].get(stream.selectedSlot);
      const age = selectedAt === undefined ? NaN : this._timing.now() - selectedAt;
      stream.setFreshness(Number.isFinite(age) && age >= 0 && age < SCOPE_FRAME_SILENCE_MS ? 'fresh' : 'stale');
      if (health.frameSeen !== live) this._setHealth(source, { ...health, frameSeen: live });
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
