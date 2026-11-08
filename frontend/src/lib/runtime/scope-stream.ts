import { writable } from 'svelte/store';
import type { Readable } from 'svelte/store';
import { ingressScopeFrame } from './adapters/scope-adapter';
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
  readonly sourceId: string;
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
  ) {
    this.geometry = { subscribe: this.geometryStore.subscribe };
    this.context = { ...context };
    this.sourceId = context.sourceId;
    this.resume();
  }

  resume(): void {
    if (this.disposed || this.detach) return;
    this.detach = this.source.subscribe((buffer) => {
      if (this.disposed) return;
      const ingressed = ingressScopeFrame(buffer, this.context);
      if (!ingressed.ok) { this.reset(); this.malformed?.(ingressed.reason); return; }
      this.accept(ingressed);
    });
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
    this.context = { ...this.context, epoch };
    this.reset();
  }

  reset(): void {
    if (this.disposed) return;
    this.frames.clear();
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
