/**
 * ScopeController — singleton owner of the shared subscribed scope
 * WebSocket (ADR INV-2, INV-5).
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
import { ingressScopeFrame, SCOPE_FRAME_SILENCE_MS } from '$lib/runtime/adapters/scope-adapter';
import { ScopeStream } from './scope-stream';
import type { ScopeMessageSource } from './scope-stream';
import { untrack } from 'svelte';
import type {
  ConnectionState,
  ControlSessionTransition,
  WsChannel,
} from '$lib/transport/ws-client';
import type { PresentationResourceDriver, PresentationResourceHost } from './resource-host';
import type { ScopeSubscription } from './scope-view';
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
  private _channel: WsChannel | null = null;
  private _channelGeneration = 0;
  private _sentSubscriptions = new Map<string, string>();
  private _detachChannel: (() => void) | null = null;
  private _acceptedSequence = 0;
  private _arrivals: Record<ScopeSource, number | null> = { hardware: null, audio_fft: null };
  private _clockHandle: TimerHandle | null = null;
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

  configureSources(sourceIds: readonly string[]): void {
    const selected = sourceIds.includes('radio') ? 'radio' : sourceIds.includes('iq_fft') ? 'iq_fft' : null;
    if (selected) this.stream('hardware').setSource(selected === 'radio' ? 'radio' : 'iq_fft', selected);
    if (sourceIds.includes('audio_fft')) this.stream('audio_fft').setSource('audio_fft', 'audio_fft');
  }

  /** Control-plane identity qualifies geometry; pixel delivery stays on the shared stream. */
  setFrameAuthority(authority: ScopeFrameAuthority | null, additionalSources: readonly ScopeSource[] = []): void {
    if (authority && this.stream(authority.source).kind === 'iq_fft') authority = { ...authority, receiver: null };
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
    if (providerChanged && this._detachChannel) {
      const detach = this._detachChannel; this._detachChannel = null; this._channelGeneration += 1;
      try { detach(); } finally { this._channel?.disconnect(); }
      this._sentSubscriptions.clear();
      for (const binding of new Set(this._bindings.values())) binding.active = false;
    }
    const stream = this.stream(authority.source);
    if (!sameAuthority) {
      stream.setSlotFilter(authority.receiver === null ? null
        : `${stream.sourceId}:${authority.receiver}`);
      stream.reset();
    }
    for (const source of new Set([authority.source, ...additional, ...[...this._bindings.values()].map(binding => binding.source)])) {
      if (newlyQualified.includes(source)) this._streams[source]?.reset();
      const binding = [...this._bindings.values()].find(item => item.source === source);
      if (!binding || (binding.active && binding.revision === this._authorityRevision)) continue;
      if (binding.active) this._retire(binding);
      try { this._installBinding(binding); }
      catch (error) {
        this._setHealth(source, { demanded: true, transport: 'disconnected', frameSeen: false });
        console.warn('Scope authority rebind failed', error);
      }
    }
  }

  private _channelFor(_source: ScopeSource): WsChannel {
    return this._channel ??= this._getChannel('scope');
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
      subscribe: () => () => {},
      close: () => {},
    };
    const stream = new ScopeStream(
      messageSource,
      { source: source === 'hardware' ? 'radio' : 'audio_fft', sourceId: source === 'hardware' ? 'radio' : 'audio_fft', epoch: 0 },
      'borrowed',
      () => {
        const binding = [...this._bindings.values()].find(item => item.source === source);
        this._publishTraceReceipt({ kind: 'rejected', source,
          transportEpoch: binding?.channel.sessionEpoch ?? 0, callbackAt: this._timing.now(), reason: 'parse' });
        this._arrivals[source] = null; this._slotArrivals[source].clear();
        this._setHealth(source, { ...this._readHealth(source), frameSeen: false });
      },
      (subscription, previous) => {
        if (previous && (subscription === null || previous.sourceId !== subscription.sourceId || previous.receiver !== subscription.receiver)) {
          this._channel?.sendRaw({ type: 'unsubscribe', sourceId: previous.sourceId, receiver: previous.receiver });
          this._sentSubscriptions.delete(`${previous.sourceId}:${previous.receiver}`);
        }
        if (subscription) this._sendSubscription(source, subscription);

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
        token: { source, transportEpoch: this._channel?.sessionEpoch ?? 0, wireSequence: frame.seq, acceptedSequence },
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

  private _sendSubscription(source: ScopeSource, subscription: ScopeSubscription): void {
    if (!this._sourceDemanded(source) || this._channel?.state !== 'connected') return;
    const key = `${subscription.sourceId}:${subscription.receiver}`;
    const identity = JSON.stringify([this._channel.sessionEpoch, subscription]);
    if (this._sentSubscriptions.get(key) === identity) return;
    if (this._channel.sendRaw({ type: 'subscribe', ...subscription })) this._sentSubscriptions.set(key, identity);
  }

  private _installBinding(binding: ChannelBinding): void {
    const { source, channel } = binding;
    binding.revision = this._authorityRevision;
    binding.active = true;
    const stream = this.stream(source);
    stream.resume();
    this._setHealth(source, { demanded: true, transport: channel.state, frameSeen: false });
    if (this._detachChannel === null) {
      const generation = ++this._channelGeneration;
      const current = () => this._channelGeneration === generation && this._detachChannel !== null;
      let unsubscribeState = () => {}, unsubscribeSession = () => {}, unsubscribeBinary = () => {}, unsubscribeMessage = () => {};
      this._detachChannel = () => {
        try { unsubscribeState(); } finally {
          try { unsubscribeSession(); } finally { try { unsubscribeBinary(); } finally { unsubscribeMessage(); } }
        }
      };
      try {
      unsubscribeState = channel.onStateChange(state => {
        if (!current()) return;
        for (const active of new Set(this._bindings.values())) {
          if (!active.active) continue;
          const selected = this.stream(active.source);
          if (state !== 'connected') {
            this._sentSubscriptions.clear(); selected.setSessionEpoch(0); this._arrivals[active.source] = null; this._slotArrivals[active.source].clear();
          }
          if (state === 'connected') {
            selected.setSessionEpoch(Number.isSafeInteger(channel.sessionEpoch) && channel.sessionEpoch > 0 ? channel.sessionEpoch : 0);
            for (const request of selected.subscriptions) this._sendSubscription(active.source, request);
          }
          this._setHealth(active.source, { demanded: true, transport: state,
            frameSeen: state === 'connected' && this._arrivalIsLive(active.source) });
        }
        this._ensureClock();
      });
      unsubscribeSession = this._subscribeSession(source, channel, transition => {
        if (!current()) return;
        for (const active of new Set(this._bindings.values())) {
          if (!active.active) continue;
          const selected = this.stream(active.source);
          selected.setSessionEpoch(transition.state === 'connected' && Number.isSafeInteger(channel.sessionEpoch) && channel.sessionEpoch > 0 ? channel.sessionEpoch : 0);
          if (transition.state === 'connected' && selected.subscription) this._sendSubscription(active.source, selected.subscription);
        }
      });
      unsubscribeBinary = channel.onBinary(buffer => {
        if (!current() || channel.state !== 'connected') return;
        const now = this._timing.now();
        const ingressed = ingressScopeFrame(buffer, { source: 'radio', sourceId: 'scope', epoch: channel.sessionEpoch });
        if (ingressed.ok) {
          const target = ingressed.geometry.source === 'audio_fft' ? 'audio_fft' : 'hardware';
          if (!this._sourceDemanded(target)) return;
          if (!Number.isFinite(now) || now < 0) {
            this._streams[target]?.reset(); this._arrivals[target] = null; this._slotArrivals[target].clear();
            this._publishTraceReceipt({ kind: 'rejected', source: target, transportEpoch: channel.sessionEpoch,
              callbackAt: now, reason: 'clock' });
            this._setHealth(target, { ...this._readHealth(target), frameSeen: false });
          } else this._streams[target]?.ingress(ingressed);
        } else {
          for (const active of new Set(this._bindings.values())) if (active.active) this._streams[active.source]?.ingress(ingressed);
        }
      });
      unsubscribeMessage = channel.onMessage(message => {
        if (!current() || channel.state !== 'connected') return;
        for (const active of new Set(this._bindings.values())) {
          if (!active.active) continue;
          const selected = this.stream(active.source);
          if ('sourceId' in message && !selected.subscriptions.some(request => message.sourceId === request.sourceId
            && (!('receiver' in message) || message.receiver === request.receiver))) continue;
          if (message.type === 'error') for (const request of selected.subscriptions) this._sentSubscriptions.delete(`${request.sourceId}:${request.receiver}`);
          selected.handleMessage(message);
        }
      });
      channel.connect('/api/v1/scope'); }
      catch (error) { this._retire(binding); throw error; }
    }
    if (channel.state === 'connected') {
      stream.setSessionEpoch(Number.isSafeInteger(channel.sessionEpoch) && channel.sessionEpoch > 0 ? channel.sessionEpoch : 0);
      for (const request of stream.subscriptions) this._sendSubscription(source, request);
    }
  }

  private _retire(binding: ChannelBinding): void {
    const { source, channel } = binding;
    binding.active = false;
    const stream = this._streams[source];
    for (const subscription of stream?.subscriptions ?? []) {
      channel.sendRaw({ type: 'unsubscribe', sourceId: subscription.sourceId, receiver: subscription.receiver });
      this._sentSubscriptions.delete(`${subscription.sourceId}:${subscription.receiver}`);
    }
    stream?.pause(); this._arrivals[source] = null; this._slotArrivals[source].clear();
    if (![...this._bindings.values()].some(active => active.active)) {
      const detach = this._detachChannel; this._detachChannel = null; this._channelGeneration += 1;
      try { detach?.(); } finally { channel.disconnect(); this._channel = null; }
    }
    this._setHealth(source, { demanded: this._sourceDemanded(source), transport: 'disconnected', frameSeen: false });
    this._ensureClock();
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

  /** Keep the pending earliest wake; new arrivals update timestamps without rearming it. */
  private _ensureClock(): void {
    const now = this._timing.now();
    let deadline = Infinity;
    for (const source of ['hardware', 'audio_fft'] as const) {
      const health = this._readHealth(source);
      if (!health.demanded || health.transport !== 'connected') continue;
      const slot = this._streams[source]?.selectedSlot;
      const selectedAt = slot == null ? undefined : this._slotArrivals[source].get(slot);
      for (const arrival of [this._arrivals[source], selectedAt]) {
        if (arrival == null) continue;
        const age = now - arrival;
        if (Number.isFinite(age) && age >= 0 && age < SCOPE_FRAME_SILENCE_MS) {
          deadline = Math.min(deadline, arrival + SCOPE_FRAME_SILENCE_MS);
        }
      }
    }
    if (!Number.isFinite(deadline)) {
      if (this._clockHandle !== null) this._timing.clearTimeout(this._clockHandle);
      this._clockHandle = null;
    } else if (this._clockHandle === null) {
      this._clockHandle = this._timing.setTimeout(() => {
        this._clockHandle = null;
        this._tick();
        this._ensureClock();
      }, deadline - now);
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
