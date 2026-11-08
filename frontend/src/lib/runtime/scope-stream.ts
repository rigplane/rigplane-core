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
  private readonly sinks = new Set<(frame: ScopeFrame, slotKey: string) => void>();
  private readonly detach: () => void;
  private context: CurrentScopeContext;
  private slotFilter: string | null = null;
  private accepted = 0;
  private disposed = false;

  constructor(
    private readonly source: ScopeMessageSource,
    context: CurrentScopeContext,
    private readonly ownership: 'owned' | 'borrowed',
    malformed?: (reason: ScopeParseError) => void,
  ) {
    this.geometry = { subscribe: this.geometryStore.subscribe };
    this.context = { ...context };
    this.sourceId = context.sourceId;
    this.detach = source.subscribe((buffer) => {
      if (this.disposed) return;
      const ingressed = ingressScopeFrame(buffer, this.context);
      if (!ingressed.ok) { malformed?.(ingressed.reason); return; }
      this.accept(ingressed);
    });
  }

  latest(slotKey: string): ScopeFrame | null {
    return this.frames.get(slotKey) ?? null;
  }

  subscribe(sink: (frame: ScopeFrame, slotKey: string) => void): () => void {
    if (!this.disposed) this.sinks.add(sink);
    return () => { this.sinks.delete(sink); };
  }

  /** Frames of the bound slot publish geometry; `null` accepts every slot (unknown authority). */
  setSlotFilter(key: string | null): void {
    if (!this.disposed && this.slotFilter !== key) this.slotFilter = key;
  }

  /** Monotonic count of accepted frames — the receipt clock downstream facts read. */
  get receipt(): number {
    return this.accepted;
  }

  /** A new transport session: the epoch stamps future frames and stale-session state retires. */
  setSessionEpoch(epoch: number): void {
    if (this.disposed) return;
    this.context = { ...this.context, epoch };
    this.reset();
  }

  reset(): void {
    if (this.disposed) return;
    this.frames.clear();
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
    try { this.detach(); }
    finally {
      try { if (this.ownership === 'owned') this.source.close(); }
      finally { this.publishGeometry(null); }
    }
  }

  private accept(ingressed: { frame: ScopeFrame; geometry: ScopeGeometry; slotKey: string }): void {
    const { frame, geometry, slotKey } = ingressed;
    this.accepted += 1;
    this.frames.set(slotKey, frame);
    if (this.slotFilter === null || this.slotFilter === slotKey) {
      const current = this.geometryValue;
      if (!current || current.source !== geometry.source || current.sourceId !== geometry.sourceId
        || current.receiver !== geometry.receiver || current.mode !== geometry.mode
        || current.view.startHz !== geometry.view.startHz
        || current.view.endHz !== geometry.view.endHz
        || current.coverage.startHz !== geometry.coverage.startHz
        || current.coverage.endHz !== geometry.coverage.endHz
        || current.outOfRange !== geometry.outOfRange
        || current.hasPixels !== geometry.hasPixels || current.freshness !== 'fresh') {
        this.publishGeometry(geometry);
      }
    }
    for (const sink of this.sinks) sink(frame, slotKey);
  }

  private publishGeometry(geometry: ScopeGeometry | null): void {
    this.geometryValue = geometry;
    this.geometryStore.set(geometry);
  }
}
