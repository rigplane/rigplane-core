/**
 * `fieldline` meter renderer (MOR-1074, VFO slice) — MOR-977 §2.2's "discrete
 * segments": 12 chunky blocks separated by the token's gap, zoned by colour at
 * the S9 and redline boundaries, with peak-hold as a SINGLE segment held at
 * full brightness rather than a hairline tick.
 *
 * Pure geometry, expressed as a fixed segment count so the consumer owns the
 * pixel width. Ballistics are NOT here: smoothing is a per-grammar tuning
 * applied at the smoother (MOR-977 §1.1), not a renderer behaviour — fieldline
 * is the fastest and least smoothed of the three, and that lives with the
 * smoother, not with this descriptor.
 *
 * The contrast with studioline is structural, not cosmetic: a continuous rail
 * carries a fractional fill and a sub-pixel peak; a segment ladder quantises
 * both, so a reading is legible as a COUNT at arm's length with gloves on.
 */
import type { DesignLanguageTokens, RendererViewModel, Zone } from '../contract';

/**
 * MOR-2255 (slice A): fieldline's bar-gauge zone palette. These three values
 * are the ones `BarGauge` already draws — `DEFAULT_ZONES` in
 * `components-v2/meters/bar-gauge-utils.ts` — so wiring the palette through
 * this seam changes no pixel. Giving each language its own palette is a
 * separate ticket. The literal is repeated in `studioline` and `segmentline`
 * rather than shared through a constant (coordinator ruling, MOR-2255): a
 * language declares its own data.
 */
export const FIELDLINE_METER_ZONES: readonly Zone[] = [
  { end: 0.6, color: '#14A665' },
  { end: 0.8, color: '#F2CF4A' },
  { end: 1.0, color: '#F14C42' },
];

/** Few enough to count at a glance, coarse enough to read in sunlight. */
export const FIELDLINE_SEGMENT_COUNT = 12;
/** Scale marks live under the ladder, at the two zone boundaries only. */
export const FIELDLINE_SCALE_TICKS = [9, 15] as const;

export type SegmentZone = 'normal' | 'over';

export interface FieldlineSegment {
  readonly index: number;
  readonly zone: SegmentZone;
  readonly lit: boolean;
  readonly tone: string;
  /** Peak-hold: one segment held at full brightness, never a second fill. */
  readonly peakHold: boolean;
}

export interface FieldlineMeter {
  readonly kind: 'fieldline-meter';
  readonly trackWidth: string;
  readonly segmentGap: string;
  /**
   * MOR-2214: `FIELDLINE_SEGMENT_COUNT` is the same constant that already
   * sizes the `segments` array above — reused, not redefined, so this field
   * and that array can never disagree about how many segments fieldline
   * draws. `segmentGapPx` is `tokens.meters.segmentGap` parsed to a number,
   * the same parsing convention this file's own test already uses for its
   * own assertion (`Number.parseFloat(m.segmentGap)`).
   */
  readonly segmentCount: number;
  readonly segmentGapPx: number;
  readonly segments: readonly FieldlineSegment[];
  /** How many segments the reading lights; `null` when unobserved — never 0. */
  readonly litCount: number | null;
  /**
   * MOR-2250: the same `rx.active`/`tx.tuning` reads each segment's own
   * `tone` above already zones by, exposed flat under the `MeterDisplay`
   * field names `LinearSMeter` consumes. The S9 crossover POSITION stays
   * calibration-derived in `LinearSMeter` itself (owner ruling, MOR-2250) —
   * this pair carries color only.
   */
  readonly toneBelowS9: string;
  readonly toneAboveS9: string;
  /** MOR-2255: `FIELDLINE_METER_ZONES`, the `MeterDisplay` field `BarGauge`
   *  colors its segments from. */
  readonly zones: readonly Zone[];
  readonly scaleTicks: readonly number[];
  readonly unknown: boolean;
}

const clampFraction = (value: number, max: number): number => Math.min(1, Math.max(0, value / max));

const finiteNumber = (fields: RendererViewModel['fields'], key: string): number | null => {
  const value = fields[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
};

/** Segments are lit by quantised fraction; only a true zero lights nothing. */
const segmentsFor = (value: number, max: number): number =>
  Math.ceil(clampFraction(value, max) * FIELDLINE_SEGMENT_COUNT);

export function renderMeter(
  viewModel: RendererViewModel, tokens: DesignLanguageTokens,
): FieldlineMeter {
  const max = finiteNumber(viewModel.fields, 'max') ?? 15;
  const s9 = finiteNumber(viewModel.fields, 's9') ?? 9;
  const value = finiteNumber(viewModel.fields, 'value');
  const peak = finiteNumber(viewModel.fields, 'peak');

  const crossoverIndex = Math.round(clampFraction(s9, max) * FIELDLINE_SEGMENT_COUNT);
  const litCount = value === null ? null : segmentsFor(value, max);
  // A held peak below the live reading is already lit; it only earns its own
  // segment when it sits above, which is the whole point of holding it.
  const peakIndex = peak === null ? null : Math.max(0, segmentsFor(peak, max) - 1);

  const segments = Array.from({ length: FIELDLINE_SEGMENT_COUNT }, (_, index): FieldlineSegment => {
    const zone: SegmentZone = index >= crossoverIndex ? 'over' : 'normal';
    return {
      index,
      zone,
      lit: litCount !== null && index < litCount,
      tone: zone === 'over' ? tokens.tx.tuning : tokens.rx.active,
      peakHold: peakIndex !== null && index === peakIndex,
    };
  });

  return {
    kind: 'fieldline-meter',
    trackWidth: tokens.meters.trackWidth,
    segmentGap: tokens.meters.segmentGap,
    segmentCount: FIELDLINE_SEGMENT_COUNT,
    segmentGapPx: Number.parseFloat(tokens.meters.segmentGap),
    segments,
    litCount,
    toneBelowS9: tokens.rx.active,
    toneAboveS9: tokens.tx.tuning,
    zones: FIELDLINE_METER_ZONES,
    scaleTicks: FIELDLINE_SCALE_TICKS,
    unknown: value === null,
  };
}
