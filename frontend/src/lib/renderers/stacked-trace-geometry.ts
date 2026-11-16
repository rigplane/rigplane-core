// Pure geometry of the stacked-trace ("3D") panadapter view (MOR-4463):
// where a stored spectrum row sits in frequency, how it maps onto the
// current view, and how depth recedes on screen. No DOM, no WebGL.

import type { WaterfallOptions } from './waterfall-renderer';
import type { SpectrumOptions } from './spectrum-renderer';
import { getPassbandGeometry } from '../../components/spectrum/passband-geometry';

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
  /** Perspective strength: the last row is drawn at 1 / (1 + recede) of the front size; 0 is no perspective. */
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
  /** Size of the row about the vanishing point: its width and its trace height. */
  scale: number;
  /** 0 = full colour, 1 = dissolved into the background. */
  fog: number;
}

/**
 * Depth 0 is the live front row, depth 1 the oldest. One-point perspective:
 * a row shrinks toward the vanishing point above the middle of the pane, in
 * width and in height alike, so only the front row spans the pane and its
 * frequency axis; `stackedScreenX` and `unprojectStackedFloor` carry a view
 * fraction to the screen and back at any depth.
 */
export function projectStackedDepth(depth: number, settings: StackedTraceSettings, out: StackedDepth): void {
  const z = depth < 0 ? 0 : depth > 1 ? 1 : depth;
  out.scale = 1 / (1 + settings.recede * z);
  out.baseline = settings.floorRise * z * (1 + settings.recede) * out.scale;
  const t = z <= 0.15 ? 0 : (z - 0.15) / 0.85;
  out.fog = 0.9 * t * t * (3 - 2 * t);
}

/** Screen x (fraction of the pane width) of view fraction `fraction` on a row drawn at `scale`. */
export function stackedScreenX(fraction: number, scale: number): number {
  return 0.5 + (fraction - 0.5) * scale;
}

/** The floor point drawn at a screen point: its depth and its view fraction. */
export interface StackedFloorPoint {
  depth: number;
  fraction: number;
}

/**
 * Inverse of the projection on the floor (the row baselines). `x` is a
 * fraction of the pane width, `rise` the height above the front baseline as
 * a fraction of the pane height; a point in front of the front row or beyond
 * the last one takes that row's depth.
 */
export function unprojectStackedFloor(x: number, rise: number, settings: StackedTraceSettings, out: StackedFloorPoint): void {
  const t = rise / (settings.floorRise * (1 + settings.recede));
  out.depth = t <= 0 ? 0 : t * (1 + settings.recede) >= 1 ? 1 : t / (1 - settings.recede * t);
  out.fraction = 0.5 + (x - 0.5) * (1 + settings.recede * out.depth);
}

/**
 * Pointer-to-frequency map of the scene: pane fractions (`y` downward) to the
 * view fraction 0..1 drawn there; null when the scene cannot answer.
 */
export type StackedTraceLocate = (x: number, y: number) => number | null;

/**
 * The tuning marker, the passband and the covered part of the view as view
 * fractions — where the flat renderer draws them on its full-width trace.
 */
export interface StackedTraceMarkers {
  /** Tuning marker; negative hides it. */
  tune: number;
  /** Passband edges; `bandHi <= bandLo` hides it. */
  bandLo: number;
  bandHi: number;
  /** The view holds samples for fractions in [coverLo, coverHi). */
  coverLo: number;
  coverHi: number;
}

export function stackedTraceMarkers(options: SpectrumOptions, out: StackedTraceMarkers): void {
  const { spanHz, centerHz, tuneHz } = options;
  out.tune = -1; out.bandLo = out.bandHi = 0; out.coverLo = 0; out.coverHi = 1;
  if (!(spanHz > 0)) return;
  const startHz = centerHz - spanHz / 2, clamp = (v: number) => v < 0 ? 0 : v > 1 ? 1 : v;
  if (options.showRfOverlays !== false) {
    const fixed = options.scopeMode === 1 || options.scopeMode === 3;
    const tune = fixed && tuneHz > 0 ? clamp((tuneHz - startHz) / spanHz) : 0.5;
    const band = getPassbandGeometry(options.mode ?? '', options.passbandHz, options.passbandShiftHz, spanHz, 1, tune);
    if (band && band.widthPx > 0) { out.bandLo = band.leftPx; out.bandHi = band.rightPx; }
    if (options.showTuneIndicator !== false) out.tune = tune;
  }
  const sampleSpan = options.sampleSpanHz || spanHz, sourceCenter = centerHz - options.panoramaShiftHz;
  out.coverLo = clamp(((options.coverage?.startHz ?? sourceCenter - sampleSpan / 2) - startHz) / spanHz);
  out.coverHi = clamp(((options.coverage?.endHz ?? sourceCenter + sampleSpan / 2) - startHz) / spanHz);
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
