// Pure geometry of the stacked-trace ("3D") panadapter view (MOR-4463):
// where a stored spectrum row sits in frequency, how it maps onto the
// current view, and how depth recedes on screen. No DOM, no WebGL.

import type { WaterfallOptions } from './waterfall-renderer';

export interface StackedTraceSettings {
  /** History rows kept behind the live trace. */
  readonly rows: number;
  /** Milliseconds of signal one history row stands for. */
  readonly rowPeriodMs: number;
  /** Front baseline above the pane bottom, CSS px (room for the frequency labels). */
  readonly frontInsetPx: number;
  /** Fraction of the pane height the floor climbs from the front row to the last. */
  readonly floorRise: number;
  /** Fraction of the pane height a full-scale signal reaches on the front row. */
  readonly traceHeight: number;
  /** Perspective strength; 0 spaces the rows evenly at equal height. */
  readonly recede: number;
}

export const defaultStackedTraceSettings: StackedTraceSettings = {
  rows: 60,
  rowPeriodMs: 100,
  frontInsetPx: 16,
  floorRise: 0.5,
  traceHeight: 0.6,
  recede: 1.5,
};

/** Absolute-frequency placement of one stored row; Hz stay float64 here. */
export interface StackedRowGeometry {
  /** Frequency of sample coordinate 0. */
  startHz: number;
  /** Hz spanned by sample coordinates 0..1; negative for a reversed window. */
  spanHz: number;
  coverStartHz: number;
  coverEndHz: number;
  samples: number;
  sampleOffset: number;
  amplitudeMax: number;
}

/**
 * Fills `out` with the frequency placement of a frame delivered under
 * `options` — the source window, not the viewport: `centerHz -
 * panoramaShiftHz` is the centre the samples were taken at. Returns false
 * when the frame carries no samples. The sample count is whatever the frame
 * holds; nothing here assumes a fixed number of points.
 */
export function stackedRowGeometry(
  options: WaterfallOptions, dataLength: number, out: StackedRowGeometry,
): boolean {
  const sampleOffset = options.sampleOffset ?? 0, samples = dataLength - sampleOffset;
  if (samples <= 0) return false;
  const span = options.sampleSpanHz || options.spanHz || 1;
  const start = options.sampleWindow?.start ?? 0, range = (options.sampleWindow?.end ?? 1) - start;
  out.startHz = options.centerHz - options.panoramaShiftHz - span / 2 - start * span / range;
  out.spanHz = span / range;
  const endHz = out.startHz + out.spanHz;
  out.coverStartHz = options.coverage?.startHz ?? Math.min(out.startHz, endHz);
  out.coverEndHz = options.coverage?.endHz ?? Math.max(out.startHz, endHz);
  out.samples = samples;
  out.sampleOffset = sampleOffset;
  out.amplitudeMax = options.sampleAmplitudeMax ?? 80;
  return true;
}

/**
 * A stored row seen through the current view: the sample coordinate at view
 * fraction x is `offset + scale * x`, and the row has data for view
 * fractions in [coverLo, coverHi).
 */
export interface StackedRowView {
  scale: number;
  offset: number;
  coverLo: number;
  coverHi: number;
}

export function projectStackedRow(
  row: StackedRowGeometry, viewStartHz: number, viewSpanHz: number, out: StackedRowView,
): void {
  out.scale = viewSpanHz / row.spanHz;
  out.offset = (viewStartHz - row.startHz) / row.spanHz;
  out.coverLo = (row.coverStartHz - viewStartHz) / viewSpanHz;
  out.coverHi = (row.coverEndHz - viewStartHz) / viewSpanHz;
}

/** Screen placement of a row at a depth; fractions of the pane height. */
export interface StackedDepth {
  /** Baseline above the front baseline. */
  baseline: number;
  /** Trace height multiplier. */
  scale: number;
  /** 0 = full colour, 1 = dissolved into the background. */
  fog: number;
}

/**
 * Depth 0 is the live front row, depth 1 the oldest. Only height recedes:
 * every row spans the full width, so one screen column is one frequency on
 * every row and the frequency axis, the tuning marker and click-to-tune stay
 * exact at any depth.
 */
export function projectStackedDepth(depth: number, settings: StackedTraceSettings, out: StackedDepth): void {
  const z = depth < 0 ? 0 : depth > 1 ? 1 : depth;
  out.scale = 1 / (1 + settings.recede * z);
  out.baseline = settings.floorRise * z * (1 + settings.recede) * out.scale;
  const t = z <= 0.15 ? 0 : (z - 0.15) / 0.85;
  out.fog = 0.9 * t * t * (3 - 2 * t);
}

/** Trace vertices per row: one per two backing pixels, bounded. */
export function stackedColumnCount(backingWidth: number): number {
  return Math.max(64, Math.min(2048, Math.ceil(backingWidth / 2)));
}

/** 0 at the commit of a history row, 1 once it has moved one slot back. */
export function stackedGlidePhase(now: number, committedAt: number, periodMs: number): number {
  const phase = (now - committedAt) / periodMs;
  return phase < 0 ? 0 : phase > 1 ? 1 : phase;
}
