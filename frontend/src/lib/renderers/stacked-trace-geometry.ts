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
  /** Perspective strength: the last row is drawn at 1 / (1 + recede) of the front size; 0 is no perspective. */
  readonly recede: number;
  /** How far the last row dissolves into the background, 0..1. */
  readonly haze: number;
  /** Share of a new row in the history row it becomes; the rest is the row before it. 1 is no smoothing. */
  readonly rowBlend: number;
  /** How much a history row is smoothed across frequency: 1 is a quarter, a half, a quarter; 0 is none. */
  readonly rowSoften: number;
  /** How far below the noise floor the surface starts, in units of the 0..80 level scale. */
  readonly floorDrop: number;
  /** Colour gain, 0..100: 50 colours the levels evenly, more colours down to the noise, less only the peaks. */
  readonly colorGain: number;
}

export const defaultStackedTraceSettings: StackedTraceSettings = {
  rows: 96,
  rowPeriodMs: 100,
  frontInsetPx: 16,
  floorRise: 0.58,
  traceHeight: 0.46,
  recede: 2 / 3,
  haze: 0.16,
  rowBlend: 0.6,
  rowSoften: 1,
  floorDrop: 8,
  colorGain: 70,
};

/** The settings a user may adjust, each with the range it may take. */
export const stackedTraceTunables = [
  { key: 'recede', min: 0, max: 2, step: 0.05 },
  { key: 'rows', min: 24, max: 160, step: 8 },
  { key: 'rowPeriodMs', min: 40, max: 250, step: 10 },
  { key: 'floorRise', min: 0.2, max: 0.7, step: 0.02 },
  { key: 'traceHeight', min: 0.2, max: 0.8, step: 0.02 },
  { key: 'haze', min: 0, max: 0.9, step: 0.05 },
  { key: 'rowBlend', min: 0.2, max: 1, step: 0.05 },
  { key: 'rowSoften', min: 0, max: 1, step: 0.25 },
  { key: 'floorDrop', min: 0, max: 24, step: 1 },
  { key: 'colorGain', min: 0, max: 100, step: 5 },
] as const satisfies readonly { key: keyof StackedTraceSettings; min: number; max: number; step: number }[];

/**
 * Reads stored or entered settings: the defaults, with every adjustable
 * value that is a finite number taken and held inside its range. Anything
 * else in `raw` is ignored.
 */
export function parseStackedTraceSettings(raw: unknown): StackedTraceSettings {
  const settings: { -readonly [K in keyof StackedTraceSettings]: StackedTraceSettings[K] } = { ...defaultStackedTraceSettings };
  if (typeof raw !== 'object' || raw === null) return settings;
  for (const { key, min, max } of stackedTraceTunables) {
    const value = (raw as Record<string, unknown>)[key];
    if (typeof value !== 'number' || !Number.isFinite(value)) continue;
    const held = Math.min(max, Math.max(min, value));
    settings[key] = key === 'rows' ? Math.round(held) : held;
  }
  return settings;
}

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
 * width and in height alike. Only the front row spans exactly the view; the
 * frequency axis, the tuning marker and click-to-tune read that row, as on
 * the flat trace. A row further back shows `1 / scale` of the view across
 * the pane, so it fills the pane edge to edge where it has the samples.
 */
export function projectStackedDepth(depth: number, settings: StackedTraceSettings, out: StackedDepth): void {
  const z = depth < 0 ? 0 : depth > 1 ? 1 : depth;
  out.scale = 1 / (1 + settings.recede * z);
  out.baseline = settings.floorRise * z * (1 + settings.recede) * out.scale;
  const t = z <= 0.15 ? 0 : (z - 0.15) / 0.85;
  out.fog = settings.haze * t * t * (3 - 2 * t);
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
