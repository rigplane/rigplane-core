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
import type { DesignLanguageTokens, RendererViewModel } from '../contract';

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
    };
  }

  const fillFraction = Math.min(1, Math.max(0, value / max));
  return {
    kind: 'segmentline-meter',
    unknown: false,
    hot: fillFraction >= HOT_THRESHOLD,
    segmentCount: SEGMENTLINE_SEGMENT_COUNT,
    segmentGapPx,
  };
}
