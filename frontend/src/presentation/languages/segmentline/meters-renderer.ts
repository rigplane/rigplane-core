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

export interface SegmentlineMeter {
  readonly kind: 'segmentline-meter';
  /** Fill as a fraction of the track, or 0 when unobserved (see `unknown`). */
  readonly fillFraction: number;
  /** Where the two-tone handover sits, as a fraction of the track. */
  readonly s9Fraction: number;
  /** True when the reading was never observed — CSS renders an empty track
   *  plus the unknown mark, never a gauge resting at zero. */
  readonly unknown: boolean;
  readonly hot: boolean;
  /** Segment pitch, straight off the tokens, so the CSS gradient and the
   *  language declaration can never disagree about what a segment is. */
  readonly segmentWidthPx: number;
  readonly segmentGapPx: number;
}

const finite = (fields: RendererViewModel['fields'], key: string): number | null => {
  const value = fields[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
};

export function renderMeter(
  viewModel: RendererViewModel, tokens: DesignLanguageTokens,
): SegmentlineMeter {
  // No fallback: `tokens` is always a real, valid `DesignLanguageTokens` —
  // this renderer is reachable only through `segmentline`'s own manifest
  // (`resolveRenderer`/`invokeRenderer`, `../contract.ts`), which always
  // supplies `SEGMENTLINE_TOKENS`. A silent fallback here would be dead code
  // ("reachable errors only") and would let the token read stop being
  // load-bearing without any test noticing.
  const segmentWidthPx = Number.parseFloat(tokens.meters.trackWidth);
  const segmentGapPx = Number.parseFloat(tokens.meters.segmentGap);

  const value = finite(viewModel.fields, 'value');
  const max = finite(viewModel.fields, 'max') ?? 1;
  const s9 = finite(viewModel.fields, 's9');
  const s9Fraction = s9 !== null && max > 0 ? Math.min(1, Math.max(0, s9 / max)) : 0;

  if (value === null || max <= 0) {
    return {
      kind: 'segmentline-meter', fillFraction: 0, s9Fraction,
      unknown: true, hot: false, segmentWidthPx, segmentGapPx,
    };
  }

  const fillFraction = Math.min(1, Math.max(0, value / max));
  return {
    kind: 'segmentline-meter',
    fillFraction,
    s9Fraction,
    unknown: false,
    hot: fillFraction >= HOT_THRESHOLD,
    segmentWidthPx,
    segmentGapPx,
  };
}
