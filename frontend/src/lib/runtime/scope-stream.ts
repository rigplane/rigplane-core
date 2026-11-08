import { writable } from 'svelte/store';
import type { Readable } from 'svelte/store';
import { parseCurrentScopeFrame } from './scope-frame';
import type { CurrentScopeContext, ScopeFrame, ScopeFreshness, ScopeGeometry, ScopeParseError } from './scope-frame';

export interface ScopeMessageSource {
  subscribe(sink: (buffer: ArrayBuffer) => void): () => void;
  close(): void;
}

export class ScopeStream {
  readonly geometry: Readable<ScopeGeometry | null>;
  private readonly geometryStore = writable<ScopeGeometry | null>(null);
  private geometryValue: ScopeGeometry | null = null;
  private readonly frames = new Map<string, Map<ScopeFrame['receiver'], ScopeFrame>>();
  private readonly sinks = new Set<(frame: ScopeFrame) => void>();
  private readonly detach: () => void;
  private disposed = false;

  constructor(
    private readonly source: ScopeMessageSource,
    context: CurrentScopeContext,
    private readonly ownership: 'owned' | 'borrowed',
    malformed?: (reason: ScopeParseError) => void,
  ) {
    this.geometry = { subscribe: this.geometryStore.subscribe };
    const identity = { ...context };
    this.detach = source.subscribe((buffer) => {
      if (this.disposed) return;
      const result = parseCurrentScopeFrame(buffer, identity);
      if (!result.ok) { malformed?.(result.reason); return; }
      this.accept(result.frame);
    });
  }

  latest(sourceId: string, receiver: ScopeFrame['receiver']): ScopeFrame | null {
    return this.frames.get(sourceId)?.get(receiver) ?? null;
  }

  subscribe(sink: (frame: ScopeFrame) => void): () => void {
    if (!this.disposed) this.sinks.add(sink);
    return () => { this.sinks.delete(sink); };
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

  private accept(frame: ScopeFrame): void {
    let receivers = this.frames.get(frame.sourceId);
    if (!receivers) { receivers = new Map(); this.frames.set(frame.sourceId, receivers); }
    receivers.set(frame.receiver, frame);
    const current = this.geometryValue;
    if (!current || current.source !== frame.source || current.sourceId !== frame.sourceId
      || current.receiver !== frame.receiver || current.view.startHz !== frame.viewStartHz
      || current.view.endHz !== frame.viewEndHz || current.coverage.startHz !== frame.coverageStartHz
      || current.coverage.endHz !== frame.coverageEndHz || current.outOfRange !== frame.outOfRange
      || current.hasPixels !== (frame.pixels !== null) || current.freshness !== 'fresh') {
      this.publishGeometry({ source: frame.source, sourceId: frame.sourceId, receiver: frame.receiver,
        view: { startHz: frame.viewStartHz, endHz: frame.viewEndHz },
        coverage: { startHz: frame.coverageStartHz, endHz: frame.coverageEndHz },
        outOfRange: frame.outOfRange, hasPixels: frame.pixels !== null, freshness: 'fresh',
      });
    }
    for (const sink of this.sinks) sink(frame);
  }

  private publishGeometry(geometry: ScopeGeometry | null): void {
    this.geometryValue = geometry;
    this.geometryStore.set(geometry);
  }
}
