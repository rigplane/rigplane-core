/**
 * `studioline` meter renderer (MOR-1073, VFO slice) — a two-tone fill split
 * at S9.
 *
 * Pure geometry, expressed as fractions of the track so the consumer owns
 * the pixel width. Ballistics are NOT here: smoothing is a per-grammar
 * tuning applied at the smoother (MOR-977 §1.1), not a renderer behaviour.
 */
import type { DesignLanguageTokens, RendererViewModel, Zone } from '../contract';

/**
 * MOR-2255 (slice A): studioline's bar-gauge zone palette. These three values
 * are the ones `BarGauge` already draws — `DEFAULT_ZONES` in
 * `components-v2/meters/bar-gauge-utils.ts` — so wiring the palette through
 * this seam changes no pixel. Giving each language its own palette is a
 * separate ticket. The literal is repeated in `fieldline` and `segmentline`
 * rather than shared through a constant (coordinator ruling, MOR-2255): a
 * language declares its own data.
 */
export const STUDIOLINE_METER_ZONES: readonly Zone[] = [
  { end: 0.6, color: '#14A665' },
  { end: 0.8, color: '#F2CF4A' },
  { end: 1.0, color: '#F14C42' },
];

/**
 * Peak-hold: a 1px tick, in contrast with `fieldline`'s whole-segment hold —
 * read directly (not through `design-language-renderers.ts`'s `renderSlot`
 * extraction, which never pulls this field) by
 * `fieldline/__tests__/meters-renderer.test.ts`'s own cross-language
 * comparison (`theirs.peakWidthPx`), which is why this constant and the
 * `peakWidthPx` field below survived the MOR-2250 dead-field sweep that
 * removed `peak`/`scaleTicks` (no consumer, anywhere, for either).
 */
export const PEAK_TICK_WIDTH_PX = 1;

export interface StudiolineMeter {
  readonly kind: 'studioline-meter';
  readonly trackWidth: string;
  readonly segmentGap: string;
  /**
   * MOR-2214: `segmentCount: 20` together with `segmentGapPx` (below, sourced
   * from studioline's own `segmentGap` token) restores the pre-PR default
   * geometry, `{20, 1}` (`DEFAULT_METER_DISPLAY`) — segment count alone is
   * not enough: at `segmentGapPx: 0` the same 20 segments render with no
   * inter-segment gap, a visibly different, more solid-bar look. The real
   * per-language S-meter design for `studioline` — matching the operator's
   * IC-7300 reference photos (thin segments, blue-to-S9/red-beyond zoning,
   * tick labels, a shared SWR/Po scale) — is tracked in a separate
   * follow-up ticket, not this one (owner ruling, 2026-09-02 20:59 UTC).
   */
  readonly segmentCount: number;
  readonly segmentGapPx: number;
  /** 0..1 of the track; `null` when the reading is unobserved — never 0. */
  readonly fill: number | null;
  readonly tone: string;
  readonly overTone: string;
  /**
   * MOR-2250: the same `rx.active`/`tx.tuning` reads as `tone`/`overTone`
   * above, exposed under the flat `MeterDisplay` field names `LinearSMeter`
   * consumes for its two-tone fill. The S9 crossover POSITION stays
   * calibration-derived in `LinearSMeter` itself (owner ruling, MOR-2250) —
   * this pair carries color only.
   */
  readonly toneBelowS9: string;
  readonly toneAboveS9: string;
  /** MOR-2255: `STUDIOLINE_METER_ZONES`, the `MeterDisplay` field `BarGauge`
   *  colors its segments from. */
  readonly zones: readonly Zone[];
  readonly peakWidthPx: number;
  readonly unknown: boolean;
}

const clampFraction = (value: number, max: number): number => Math.min(1, Math.max(0, value / max));

const finiteNumber = (fields: RendererViewModel['fields'], key: string): number | null => {
  const value = fields[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
};

export function renderMeter(
  viewModel: RendererViewModel, tokens: DesignLanguageTokens,
): StudiolineMeter {
  const max = finiteNumber(viewModel.fields, 'max') ?? 15;
  const value = finiteNumber(viewModel.fields, 'value');
  return {
    kind: 'studioline-meter',
    trackWidth: tokens.meters.trackWidth,
    segmentGap: tokens.meters.segmentGap,
    segmentCount: 20,
    segmentGapPx: Number.parseFloat(tokens.meters.segmentGap),
    fill: value === null ? null : clampFraction(value, max),
    tone: tokens.rx.active,
    overTone: tokens.tx.tuning,
    toneBelowS9: tokens.rx.active,
    toneAboveS9: tokens.tx.tuning,
    zones: STUDIOLINE_METER_ZONES,
    peakWidthPx: PEAK_TICK_WIDTH_PX,
    unknown: value === null,
  };
}
