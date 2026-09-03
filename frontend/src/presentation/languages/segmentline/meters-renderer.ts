/**
 * `segmentline` meter renderer (MOR-2149, VFO slice) — the segmented LCD bar
 * graph.
 *
 * READ THE SEAM BEFORE CHANGING THE FIELD NAMES. This renderer is consumed
 * through `semantic/design-language-renderers.ts`'s `renderSlot('meters',
 * ...)`, and its one production caller is `semantic/MetersSurface.svelte`'s
 * `signalDisplay`, which calls it with exactly three fields:
 *
 *     { value: 0..1 | null, max: 1, s9: number }
 *
 * `value` is already calibrated onto the 0..1 UI scale by `meter-utils`
 * (`sLevel`), `s9` is the S9 crossover expressed on that SAME scale, and an
 * unobserved meter passes `value: null` rather than 0 — "not measured" and
 * "zero signal" are different claims and this renderer must keep them apart.
 *
 * WHAT ACTUALLY REACHES THE DOM. `renderSlot` annotates only TOP-LEVEL
 * PRIMITIVES onto the element as `data-dl-<kebab>`; nested objects and arrays
 * are skipped, so every field below is flat on purpose. `segmentline.css`'s
 * `[data-dl-unknown='true']`/`[data-dl-hot='true']` rules are what draw from
 * `unknown`/`hot`.
 *
 * It does not draw, animate, or smooth. Ballistics belong to the shipped
 * gauge components (`LinearSMeter`/`BarGauge` via `utils/smoothing.svelte`),
 * which already snap rather than animate under prefers-reduced-motion; a
 * second loop here would be an unaudited one.
 */
import type { DesignLanguageTokens, RendererViewModel, Zone } from '../contract';

/**
 * MOR-2255 (slice A): segmentline's bar-gauge zone palette. These three values
 * are the ones `BarGauge` already draws — `DEFAULT_ZONES` in
 * `components-v2/meters/bar-gauge-utils.ts` — so wiring the palette through
 * this seam changes no pixel. Giving each language its own palette is a
 * separate ticket. The literal is repeated in `studioline` and `fieldline`
 * rather than shared through a constant (coordinator ruling, MOR-2255): a
 * language declares its own data.
 */
export const SEGMENTLINE_METER_ZONES: readonly Zone[] = [
  { end: 0.6, color: '#14A665' },
  { end: 0.8, color: '#F2CF4A' },
  { end: 1.0, color: '#F14C42' },
];

/** Above this fraction of full scale the family marks the track hot. */
export const HOT_THRESHOLD = 0.8;

/**
 * MOR-2214: NOT an invented number. This is `DEFAULT_METER_DISPLAY.segmentCount`
 * in `frontend/src/components-v2/meters/meter-display.ts`, whose own comment
 * says it matches "the literal constants [LinearSMeter] drew from before this
 * prop existed" — `segmentline` is documented elsewhere in this codebase
 * (`declarations.ts`'s MOR-2148 comment) as "the amber-LCD instrument family",
 * i.e. `LinearSMeter`'s original hardcoded look WAS segmentline's look, before
 * design languages existed to name it.
 */
export const SEGMENTLINE_SEGMENT_COUNT = 20;

export interface SegmentlineMeter {
  readonly kind: 'segmentline-meter';
  /** True when the reading was never observed — CSS renders an empty track
   *  plus the unknown mark, never a gauge resting at zero. */
  readonly unknown: boolean;
  readonly hot: boolean;
  readonly segmentCount: number;
  /**
   * `tokens.meters.segmentGap` parsed to a number (`'3px'` → 3). This
   * deliberately differs from `DEFAULT_METER_DISPLAY.segmentGapPx` (1) — that
   * default was a rough placeholder; this is segmentline's real declared
   * token.
   */
  readonly segmentGapPx: number;
  /**
   * MOR-2250: `tokens.rx.active`/`tokens.tx.tuning`, the same token paths
   * `studioline`/`fieldline` already read for their own tone fields —
   * segmentline reads neither elsewhere, so this is a new read, not a reuse
   * of an existing one within this file. The S9 crossover POSITION stays
   * calibration-derived in `LinearSMeter` itself (owner ruling, MOR-2250) —
   * this pair carries color only.
   */
  readonly toneBelowS9: string;
  readonly toneAboveS9: string;
  /** MOR-2255: `SEGMENTLINE_METER_ZONES`, the `MeterDisplay` field `BarGauge`
   *  colors its segments from. Emitted on BOTH return paths below — an
   *  unobserved reading still declares the palette, exactly as it already
   *  declares `segmentCount`/`segmentGapPx`/the tone pair. */
  readonly zones: readonly Zone[];
}

const finite = (fields: RendererViewModel['fields'], key: string): number | null => {
  const value = fields[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
};

export function renderMeter(
  viewModel: RendererViewModel, tokens: DesignLanguageTokens,
): SegmentlineMeter {
  const value = finite(viewModel.fields, 'value');
  const max = finite(viewModel.fields, 'max') ?? 1;
  const segmentGapPx = Number.parseFloat(tokens.meters.segmentGap);

  if (value === null || max <= 0) {
    return {
      kind: 'segmentline-meter', unknown: true, hot: false,
      segmentCount: SEGMENTLINE_SEGMENT_COUNT, segmentGapPx,
      toneBelowS9: tokens.rx.active, toneAboveS9: tokens.tx.tuning,
      zones: SEGMENTLINE_METER_ZONES,
    };
  }

  const fillFraction = Math.min(1, Math.max(0, value / max));
  return {
    kind: 'segmentline-meter',
    unknown: false,
    hot: fillFraction >= HOT_THRESHOLD,
    segmentCount: SEGMENTLINE_SEGMENT_COUNT,
    segmentGapPx,
    toneBelowS9: tokens.rx.active,
    toneAboveS9: tokens.tx.tuning,
    zones: SEGMENTLINE_METER_ZONES,
  };
}
