import { writable } from 'svelte/store';
import type { Readable } from 'svelte/store';
import { ingressScopeFrame, parseScopeGranted, scopeGrantedGeometry, scopeMatchesSubscription } from './adapters/scope-adapter';
import type { ScopeIngressResult } from './adapters/scope-adapter';
import type { ScopeGranted, ScopeSubscription } from './scope-view';
import type { CurrentScopeContext, ScopeFrame, ScopeFreshness, ScopeGeometry, ScopeParseError } from './scope-frame';

export interface ScopeMessageSource {
  subscribe(sink: (buffer: ArrayBuffer) => void): () => void;
  close(): void;
}

/**
 * One shared stream per source: pixels reach renderers through `subscribe`
 * (non-reactive), while the small `geometry` store is the only reactive
 * surface and changes only when the qualified facts change.
 */
export class ScopeStream {
  readonly geometry: Readable<ScopeGeometry | null>;
  get kind(): ScopeFrame['source'] { return this.context.source; }
  get sourceId(): string { return this.subscriptionValue?.sourceId ?? this.context.sourceId; }
  private readonly grantedStore = writable<ScopeGranted | null>(null);
  private readonly errorStore = writable<string | null>(null);
  readonly granted: Readable<ScopeGranted | null> = { subscribe: this.grantedStore.subscribe };
  readonly error: Readable<string | null> = { subscribe: this.errorStore.subscribe };
  private subscriptionValue: ScopeSubscription | null = null;
  private subscriptionKey: string | null = null;
  private retryNeeded = false;
  private subscriptionsValue: readonly ScopeSubscription[] = [];
  private surfaces = new Map<symbol, ScopeSubscription>();
  private readonly geometryStore = writable<ScopeGeometry | null>(null);
  private geometryValue: ScopeGeometry | null = null;
  private readonly frames = new Map<string, ScopeFrame>();
  private readonly sinks = new Map<number, (frame: ScopeFrame, slotKey: string) => void>();
  private nextSinkId = 0;
  private detach: (() => void) | null = null;
  private geometryKey: string | null = null;
  private context: CurrentScopeContext;
  private slotFilter: string | null = null;
  private accepted = 0;
  private geometryAccepted = 0;
  private disposed = false;

  constructor(
    private readonly source: ScopeMessageSource,
    context: CurrentScopeContext,
    private readonly ownership: 'owned' | 'borrowed',
    private readonly malformed?: (reason: ScopeParseError) => void,
    private readonly publishSubscription?: (subscription: ScopeSubscription | null, previous: ScopeSubscription | null) => void,
  ) {
    this.geometry = { subscribe: this.geometryStore.subscribe };
    this.context = { ...context };
    this.resume();
  }

  get subscription(): ScopeSubscription | null { return this.subscriptionValue; }

  updateSurfaceSubscription(token: symbol, subscription: ScopeSubscription): void {
    this.surfaces.set(token, subscription);
    this.publishSurfaceDemand();
  }

  removeSurfaceSubscription(token: symbol): void {
    this.surfaces.delete(token);
    this.publishSurfaceDemand();
  }

  get subscriptions(): readonly ScopeSubscription[] { return this.subscriptionsValue; }

  private publishSurfaceDemand(): void {
    const groups = new Map<string, ScopeSubscription[]>();
    for (const request of this.surfaces.values()) {
      const key = `${request.sourceId}:${request.receiver}`;
      const group = groups.get(key) ?? []; group.push(request); groups.set(key, group);
    }
    const next: ScopeSubscription[] = [];
    for (const requests of groups.values()) {
      const visible = requests.filter(request => request.maxFps > 0);
      const candidates = visible.length > 0 ? visible : requests;
      const selected = candidates.reduce((current, request) => request.width >= current.width ? request : current);
      next.push({ ...selected, width: Math.max(...candidates.map(request => request.width)),
        maxFps: Math.max(...candidates.map(request => request.maxFps)) });
    }
    this.applySubscriptions(next);
  }

  setSubscription(subscription: ScopeSubscription): void {
    if (this.disposed || subscription.width < 1) return;
    this.applySubscriptions([subscription]);
  }

  private applySubscriptions(next: readonly ScopeSubscription[]): void {
    const key = JSON.stringify(next);
    if (key === this.subscriptionKey) {
      if (this.retryNeeded) {
        this.retryNeeded = false;
        for (const request of next) this.publishSubscription?.(request, request);
      }
      return;
    }
    const previous = this.subscriptionsValue;
    this.subscriptionKey = key;
    this.subscriptionsValue = next.map(request => ({ ...request, view: { ...request.view } }));
    this.subscriptionValue = this.subscriptionsValue[0] ?? null;
    const selected = this.subscriptionValue;
    const priorSelected = previous[0];
    if (priorSelected?.sourceId !== selected?.sourceId || priorSelected?.receiver !== selected?.receiver) this.reset();
    this.errorStore.set(null);
    for (const old of previous) {
      if (!next.some(request => request.sourceId === old.sourceId && request.receiver === old.receiver)) this.publishSubscription?.(null, old);
    }
    for (const request of this.subscriptionsValue) {
      const old = previous.find(prior => prior.sourceId === request.sourceId && prior.receiver === request.receiver) ?? null;
      if (JSON.stringify(old) !== JSON.stringify(request)) this.publishSubscription?.(request, old);
    }
  }

  private matchingSubscription(facts: { readonly sourceId: string; readonly receiver: number | null }): ScopeSubscription | null {
    return this.subscriptionsValue.find(request => scopeMatchesSubscription(facts, request)) ?? null;
  }

  handleMessage(message: unknown): void {
    if (this.disposed) return;
    const granted = parseScopeGranted(message);
    if (granted && (this.subscriptionKey === null || this.matchingSubscription(granted))) {
      this.grantedStore.set(granted); this.errorStore.set(null);
      const selected = this.slotFilter === null || this.slotFilter === `${granted.sourceId}:${granted.receiver === null ? '*' : granted.receiver}`;
      if (selected && this.matchingSubscription(granted)?.maxFps !== 0) this.publishGeometry(scopeGrantedGeometry(granted, this.context));
    } else if (message && typeof message === 'object' && 'type' in message && message.type === 'error'
      && 'message' in message && typeof message.message === 'string') {
      this.reset(); this.retryNeeded = true; this.errorStore.set(message.message);
    }
  }

  setSource(source: ScopeFrame['source'], sourceId: string): void {
    if (this.context.source === source && this.context.sourceId === sourceId) return;
    this.context = { ...this.context, source, sourceId };
    if (this.surfaces.size > 0) {
      for (const [token, request] of this.surfaces) this.surfaces.set(token, { ...request, sourceId,
        receiver: source === 'radio' ? request.receiver : null });
      this.publishSurfaceDemand();
    } else if (this.subscriptionValue) this.setSubscription({ ...this.subscriptionValue, sourceId,
      receiver: source === 'radio' ? this.subscriptionValue.receiver : null });
    else this.reset();
  }

  setMode(mode: number): void {
    if (this.disposed || this.context.mode === mode) return;
    this.context = { ...this.context, mode };
    if (this.geometryValue) this.publishGeometry({ ...this.geometryValue, mode });
  }

  resume(): void {
    if (this.disposed || this.detach) return;
    this.detach = this.source.subscribe((buffer) => {
      if (this.disposed) return;
      this.ingress(ingressScopeFrame(buffer, this.context));
    });
  }

  /** The controller's one binary ingress routes already-qualified frames here. */
  ingress(ingressed: ScopeIngressResult): void {
    if (this.disposed || this.detach === null) return;
    if (!ingressed.ok) { this.reset(); this.malformed?.(ingressed.reason); return; }
    const subscription = this.matchingSubscription(ingressed.geometry);
    if ((this.subscriptionKey !== null && subscription === null) || subscription?.maxFps === 0) return;
    this.accept({ ...ingressed, geometry: { ...ingressed.geometry, mode: this.context.mode ?? 0 } });
  }

  pause(): void {
    const detach = this.detach; this.detach = null;
    try { detach?.(); } finally { this.reset(); }
  }

  get selectedSlot(): string | null { return this.geometryKey; }
  get sessionEpoch(): number { return this.context.epoch; }

  latest(slotKey: string): ScopeFrame | null {
    return this.frames.get(slotKey) ?? null;
  }

  subscribe(sink: (frame: ScopeFrame, slotKey: string) => void): () => void {
    const id = this.nextSinkId++;
    if (!this.disposed) this.sinks.set(id, sink);
    return () => { this.sinks.delete(id); };
  }

  /** Wait for one new selected geometry receipt without replaying the shared cache. */
  subscribeNextGeometry(afterReceipt: number, sink: (geometry: ScopeGeometry) => void): () => void {
    let unsubscribe = () => {};
    unsubscribe = this.subscribe((_frame, slotKey) => {
      const geometry = this.geometryValue;
      if (this.geometryAccepted <= afterReceipt || slotKey !== this.geometryKey || geometry?.freshness !== 'fresh') return;
      unsubscribe();
      sink(geometry);
    });
    return unsubscribe;
  }

  /** Frames of the bound slot publish geometry; `null` accepts every slot (unknown authority). */
  setSlotFilter(key: string | null): void {
    if (!this.disposed && this.slotFilter !== key) { this.slotFilter = key; this.reset(); }
  }

  /** Monotonic count of all accepted frames for diagnostics and sink delivery. */
  get receipt(): number {
    return this.accepted;
  }

  /** Last accepted ID for valid selected geometry; preserved across evidence boundaries. */
  get geometryReceipt(): number { return this.geometryAccepted; }

  /** A new transport session: the epoch stamps future frames and stale-session state retires. */
  setSessionEpoch(epoch: number): void {
    if (this.disposed) return;
    if (this.context.epoch === epoch) return;
    this.context = { ...this.context, epoch };
    this.reset();
  }

  reset(): void {
    if (this.disposed) return;
    this.frames.clear();
    this.grantedStore.set(null);
    this.geometryKey = null;
    this.publishGeometry(null);
  }

  /** The host liveness clock supplies stale transitions; no frame schedules a timer. */
  setFreshness(freshness: ScopeFreshness): void {
    if (!this.disposed && this.geometryValue && this.geometryValue.freshness !== freshness) {
      this.publishGeometry({ ...this.geometryValue, freshness });
    }
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    this.sinks.clear();
    this.frames.clear();
    try { this.detach?.(); this.detach = null; }
    finally {
      try { if (this.ownership === 'owned') this.source.close(); }
      finally { this.publishGeometry(null); }
    }
  }

  private accept(ingressed: { frame: ScopeFrame; geometry: ScopeGeometry; slotKey: string }): void {
    const { frame, geometry, slotKey } = ingressed;
    this.accepted += 1;
    this.frames.set(slotKey, frame);
    if (Number.isSafeInteger(this.context.epoch) && this.context.epoch > 0
      && (this.slotFilter === null || this.slotFilter === slotKey)) {
      this.geometryAccepted = this.accepted;
      this.geometryKey = slotKey;
      const current = this.geometryValue;
      if (!current || current.source !== geometry.source || current.sourceId !== geometry.sourceId
        || current.receiver !== geometry.receiver || current.mode !== geometry.mode || current.width !== geometry.width
        || current.view.startHz !== geometry.view.startHz
        || current.view.endHz !== geometry.view.endHz
        || current.coverage.startHz !== geometry.coverage.startHz
        || current.coverage.endHz !== geometry.coverage.endHz
        || current.outOfRange !== geometry.outOfRange
        || current.hasPixels !== geometry.hasPixels || current.freshness !== 'fresh') {
        this.publishGeometry(geometry);
      }
    }
    for (const sink of [...this.sinks.values()]) {
      try { sink(frame, slotKey); } catch (error) { console.warn('Scope stream subscriber failed', error); }
    }
  }

  private publishGeometry(geometry: ScopeGeometry | null): void {
    this.geometryValue = geometry;
    this.geometryStore.set(geometry);
  }
}
