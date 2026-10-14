import type { ScopeFrame } from './adapters/scope-adapter';

export type TraceSource = 'hardware' | 'audio_fft';
export type TraceToken = Readonly<{
  runId: string;
  source: TraceSource;
  transportEpoch: number;
  wireSequence: number;
  acceptedSequence: number;
}>;
export type TraceReceipt = Readonly<{
  kind: 'accepted';
  frame: ScopeFrame;
  token: Omit<TraceToken, 'runId'>;
  callbackAt: number;
  acceptedAt: number;
}> | Readonly<{
  kind: 'rejected';
  source: TraceSource;
  transportEpoch: number;
  callbackAt: number;
  reason: 'parse' | 'clock';
}>;
export type TraceStage =
  | 'callback' | 'accepted' | 'rejected' | 'fanout' | 'waterfall_suppressed'
  | 'waterfall_options' | 'waterfall_push' | 'spectrum_push'
  | 'raf' | 'spectrum_render' | 'coalesced' | 'wire_gap' | 'lag' | 'blur' | 'focus';
export type TraceStopReason = 'operator' | 'deadline' | 'hidden' | 'disposed' | 'overflow' | 'source_changed' | 'observer_error';
export type TraceStatus = 'recording' | 'stopped' | 'overflow';

const MAX_MS = 120_000;
const MAX_BYTES = 16 * 1024 * 1024;
const MAX_BUCKETS = 121;
const MAX_BURST = 2_048;
const MAX_INDEX = 512;
const LAG_INTERVAL_MS = 100;

type StageCount = { count: number; totalMs: number; maxMs: number; firstSequence?: number; lastSequence?: number };
type Bucket = { second: number; stages: Partial<Record<TraceStage, StageCount>> };
type Burst = { atMs: number; stage: TraceStage; durationMs: number; acceptedSequence?: number; wireSequence?: number; transportEpoch?: number; source?: TraceSource };
type Geometry = { cssWidth: number; cssHeight: number; backingWidth: number; backingHeight: number; dpr: number };
type TraceClock = {
  now(): number;
  setTimeout(callback: () => void, ms: number): ReturnType<typeof setTimeout>;
  clearTimeout(id: ReturnType<typeof setTimeout>): void;
  setInterval(callback: () => void, ms: number): ReturnType<typeof setInterval>;
  clearInterval(id: ReturnType<typeof setInterval>): void;
};
const browserClock: TraceClock = {
  now: () => performance.now(),
  setTimeout: (callback, ms) => setTimeout(callback, ms),
  clearTimeout: (id) => clearTimeout(id),
  setInterval: (callback, ms) => setInterval(callback, ms),
  clearInterval: (id) => clearInterval(id),
};

export class ScopeRenderTrace {
  readonly runId: string;
  readonly startedAt: number;
  private _status: TraceStatus = 'recording';
  private _reason: TraceStopReason | null = null;
  private readonly buckets: Bucket[] = [];
  private readonly burst: Burst[] = [];
  private burstDropped = 0;
  private accepted = 0;
  private rejected = 0;
  private wireGaps = 0;
  private readonly lastCallbackAt = new Map<TraceSource, number>();
  private readonly lastWire = new Map<TraceSource, { epoch: number; sequence: number }>();
  private frames = new WeakMap<ScopeFrame, TraceToken>();
  private readonly acceptedIndex = new Map<number, TraceToken>();
  private readonly geometry: Partial<Record<'waterfall' | 'spectrum', Geometry>> = {};
  private deadlineTimer: ReturnType<typeof setTimeout> | null = null;
  private lagTimer: ReturnType<typeof setInterval> | null = null;
  private expectedLagAt = 0;
  private frozen: string | null = null;

  constructor(
    private readonly onStop: (status: TraceStatus) => void = () => {},
    private readonly clock: TraceClock = browserClock,
    runId?: string,
  ) {
    this.runId = runId && runId.length <= 64 ? runId
      : (globalThis.crypto?.randomUUID?.() ?? `${Date.now()}-${Math.random()}`);
    this.startedAt = clock.now();
    document.addEventListener('visibilitychange', this.onVisibility);
    window.addEventListener('blur', this.onBlur);
    window.addEventListener('focus', this.onFocus);
    if (document.hidden) {
      this.stop('hidden');
      return;
    }
    this.deadlineTimer = clock.setTimeout(() => this.stop('deadline'), MAX_MS);
    this.expectedLagAt = clock.now() + LAG_INTERVAL_MS;
    this.lagTimer = clock.setInterval(() => {
      const now = clock.now();
      this.record('lag', null, Math.max(0, now - this.expectedLagAt), now - this.expectedLagAt > 100);
      this.expectedLagAt = now + LAG_INTERVAL_MS;
    }, LAG_INTERVAL_MS);
  }

  get status(): TraceStatus { return this._status; }
  get reason(): TraceStopReason | null { return this._reason; }

  observe(observation: TraceReceipt): void {
    if (!this.active()) return;
    if (observation.kind === 'rejected') {
      this.rejected += 1;
      this.record('rejected', null, 0, true, observation.callbackAt);
      return;
    }
    const { frame, callbackAt, acceptedAt } = observation;
    const token: TraceToken = Object.freeze({ runId: this.runId, ...observation.token });
    if (!Number.isSafeInteger(token.acceptedSequence) || token.acceptedSequence <= 0
      || !Number.isSafeInteger(token.transportEpoch) || token.transportEpoch <= 0
      || !Number.isSafeInteger(token.wireSequence) || token.wireSequence < 0
      || token.wireSequence > 0xffff) {
      this.rejected += 1;
      this.record('rejected', null, 0, true, callbackAt);
      return;
    }
    this.frames.set(frame, token);
    this.acceptedIndex.set(token.acceptedSequence, token);
    if (this.acceptedIndex.size > MAX_INDEX) this.acceptedIndex.delete(this.acceptedIndex.keys().next().value!);
    this.accepted += 1;
    const previous = this.lastWire.get(token.source);
    if (previous?.epoch === token.transportEpoch && token.wireSequence !== ((previous.sequence + 1) & 0xffff)) {
      this.wireGaps += 1;
      this.record('wire_gap', token, 0, true, callbackAt);
    }
    this.lastWire.set(token.source, { epoch: token.transportEpoch, sequence: token.wireSequence });
    const previousCallback = this.lastCallbackAt.get(token.source);
    const gap = previousCallback === undefined ? null : callbackAt - previousCallback;
    this.lastCallbackAt.set(token.source, callbackAt);
    this.record('callback', token, gap ?? 0, gap !== null && (gap < 20 || gap > 150), callbackAt);
    this.record('accepted', token, Math.max(0, acceptedAt - callbackAt), false, acceptedAt);
  }

  tokenForFrame(frame: ScopeFrame): TraceToken | null {
    return this._status === 'recording' ? this.frames.get(frame) ?? null : null;
  }

  tokenForAcceptedSequence(sequence: number, source: TraceSource): TraceToken | null {
    const token = this.acceptedIndex.get(sequence);
    return this._status === 'recording' && token?.source === source ? token : null;
  }

  recordGeometry(canvas: 'waterfall' | 'spectrum', cssWidth: number, cssHeight: number,
    backingWidth: number, backingHeight: number, dpr: number): void {
    if (!this.active()) return;
    const finite = [cssWidth, cssHeight, backingWidth, backingHeight, dpr]
      .every((value) => Number.isFinite(value) && value >= 0);
    if (!finite) return;
    this.geometry[canvas] = { cssWidth: Math.round(cssWidth), cssHeight: Math.round(cssHeight),
      backingWidth: Math.round(backingWidth), backingHeight: Math.round(backingHeight),
      dpr: Math.round(dpr * 100) / 100 };
  }

  record(stage: TraceStage, token: TraceToken | null = null, durationMs = 0, interesting = false, at = this.clock.now()): void {
    if (!this.active(at)) return;
    const second = Math.floor((at - this.startedAt) / 1000);
    if (second < 0 || second >= MAX_BUCKETS) { this.stop('overflow'); return; }
    const bucket = this.buckets[second] ?? (this.buckets[second] = { second, stages: {} });
    const count = bucket.stages[stage] ?? (bucket.stages[stage] = { count: 0, totalMs: 0, maxMs: 0 });
    count.count += 1;
    const duration = Number.isFinite(durationMs) ? Math.max(0, Math.min(durationMs, MAX_MS)) : 0;
    count.totalMs += duration;
    count.maxMs = Math.max(count.maxMs, duration);
    if (token) {
      count.firstSequence ??= token.acceptedSequence;
      count.lastSequence = token.acceptedSequence;
    }
    if (interesting) {
      if (this.burst.length === MAX_BURST) { this.burst.shift(); this.burstDropped += 1; }
      this.burst.push({ atMs: at - this.startedAt, stage, durationMs: duration,
        ...(token ? { source: token.source, transportEpoch: token.transportEpoch,
          wireSequence: token.wireSequence, acceptedSequence: token.acceptedSequence } : {}) });
    }
  }

  stop(reason: TraceStopReason = 'operator'): void {
    if (this._status !== 'recording') return;
    this._status = reason === 'overflow' ? 'overflow' : 'stopped';
    this._reason = reason;
    if (this.deadlineTimer !== null) this.clock.clearTimeout(this.deadlineTimer);
    if (this.lagTimer !== null) this.clock.clearInterval(this.lagTimer);
    this.deadlineTimer = null;
    this.lagTimer = null;
    document.removeEventListener('visibilitychange', this.onVisibility);
    window.removeEventListener('blur', this.onBlur);
    window.removeEventListener('focus', this.onFocus);
    this.frames = new WeakMap();
    this.acceptedIndex.clear();
    this.lastWire.clear();
    this.lastCallbackAt.clear();
    const snapshot = () => JSON.stringify({ schema: 'rigplane.scope-render-trace.v1', runId: this.runId,
      clockDomain: 'browser-performance-now', startedAt: this.startedAt, stoppedAt: this.clock.now(),
      status: this._status, reason: this._reason,
      counts: { accepted: this.accepted, rejected: this.rejected, wireGaps: this.wireGaps,
        burstDropped: this.burstDropped }, geometry: this.geometry, buckets: this.buckets, burst: this.burst });
    this.frozen = snapshot();
    if (new TextEncoder().encode(this.frozen).byteLength > MAX_BYTES) {
      this._status = 'overflow'; this._reason = 'overflow';
      this.buckets.length = 0; this.burst.length = 0;
      this.frozen = snapshot();
    }
    this.onStop(this._status);
  }

  export(): string | null { this.active(); return this._status === 'recording' ? null : this.frozen; }
  dispose(): void { this.stop('disposed'); }

  private active(now = this.clock.now()): boolean {
    if (this._status !== 'recording') return false;
    if (now - this.startedAt >= MAX_MS) { this.stop('deadline'); return false; }
    return true;
  }

  private readonly onVisibility = () => { if (document.hidden) this.stop('hidden'); };
  private readonly onBlur = () => { this.record('blur', null, 0, true); };
  private readonly onFocus = () => { this.record('focus', null, 0, true); };
}
