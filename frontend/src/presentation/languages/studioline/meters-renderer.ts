/**
 * `studioline` meter renderer (MOR-1073, VFO slice) — a two-tone fill split
 * at S9, a 1px peak tick, and the scale rendered as sparse text ticks BELOW
 * the rail rather than as marks on it.
 *
 * Pure geometry, expressed as fractions of the track so the consumer owns
 * the pixel width. Ballistics are NOT here: smoothing is a per-grammar
 * tuning applied at the smoother (MOR-977 §1.1), not a renderer behaviour.
 */
import type { DesignLanguageTokens, RendererViewModel } from '../contract';

/** Sparse text ticks — the rail itself carries no marks. */
export const STUDIOLINE_SCALE_TICKS = [1, 3, 5, 7, 9] as const;
export const PEAK_TICK_WIDTH_PX = 1;

export interface StudiolineMeter {
  readonly kind: 'studioline-meter';
  readonly trackWidth: string;
  readonly segmentGap: string;
  /**
   * MOR-2214: `segmentCount: 20` restores the pre-PR default look (the same
   * 20-segment geometry every language had before this PR). The real
   * per-language S-meter design for `studioline` — matching the operator's
   * IC-7300 reference photos (thin segments, blue-to-S9/red-beyond zoning,
   * tick labels, a shared SWR/Po scale) — is tracked in a separate
   * follow-up ticket, not this one (owner ruling, 2026-09-02 20:59 UTC).
   */
  readonly segmentCount: number;
  readonly segmentGapPx: number;
  /** 0..1 of the track; `null` when the reading is unobserved — never 0. */
  readonly fill: number | null;
  /** Where the pre-S9 tone hands over to the post-S9 one, 0..1. */
  readonly crossover: number;
  readonly tone: string;
  readonly overTone: string;
  readonly peak: number | null;
  readonly peakWidthPx: number;
  readonly scaleTicks: readonly number[];
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
  const s9 = finiteNumber(viewModel.fields, 's9') ?? 9;
  const value = finiteNumber(viewModel.fields, 'value');
  const peak = finiteNumber(viewModel.fields, 'peak');
  return {
    kind: 'studioline-meter',
    trackWidth: tokens.meters.trackWidth,
    segmentGap: tokens.meters.segmentGap,
    segmentCount: 20,
    segmentGapPx: Number.parseFloat(tokens.meters.segmentGap),
    fill: value === null ? null : clampFraction(value, max),
    crossover: clampFraction(s9, max),
    tone: tokens.rx.active,
    overTone: tokens.tx.tuning,
    peak: peak === null ? null : clampFraction(peak, max),
    peakWidthPx: PEAK_TICK_WIDTH_PX,
    scaleTicks: STUDIOLINE_SCALE_TICKS,
    unknown: value === null,
  };
}
