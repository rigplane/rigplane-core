/**
 * `fieldline` frequency renderer (MOR-1074, VFO slice) — MOR-977 §2.2's
 * "slab digits, left-aligned, no group de-emphasis".
 *
 * Pure projection of one frequency fact. It reads exactly two named fields and
 * nothing else, so an extra field on the view model — a smuggled capability
 * payload, a raw `ptt` — cannot reach the output. Digit semantics are identical
 * to `splitFrequencyToDigits()` and to studioline's: nine digits, leading MHz
 * zeros shifted off but never below one digit, grouped 10^8..10^6 / 10^5..10^3 /
 * 10^2..10^0. Only the PRESENTATION of those digits differs.
 *
 * Where studioline emits three groups at two type sizes with an underline
 * descriptor, this emits one flat list of equal-weight DIGIT CELLS: in the field
 * the Hz digits are read as often as the kHz ones, so nothing is demoted, the
 * grouping mark is a gap rather than any character, and the tuning affordance
 * inverts a whole cell to a filled block with a knocked-out glyph — visible at
 * arm's length, which an underline is not.
 */
import type { DesignLanguageTokens, RendererViewModel } from '../contract';

/** The grouping mark IS the gap — fieldline emits no separator character at all. */
export const GROUP_GAP_PX = 6;
export const DIGIT_SIZE_PX = 46;
/** Slab digits are set slightly tight; a mono face already advances uniformly. */
export const DIGIT_TRACKING = '-0.01em';
const UNKNOWN_TEXT = '—';
const GROUP_NAMES = ['mhz', 'khz', 'hz'] as const;

export type FrequencyGroupName = (typeof GROUP_NAMES)[number];

export interface FieldlineDigit {
  readonly char: string;
  /** The place value this cell tunes — the same handle studioline's underline uses. */
  readonly multiplier: number;
  readonly group: FrequencyGroupName;
  /** True for the tuned cell: solid fill, glyph knocked out (MOR-977 §2.2). */
  readonly inverted: boolean;
  /** First cell of a group, i.e. the cell a 6px gap is placed before. */
  readonly startsGroup: boolean;
}

export interface FieldlineFrequency {
  readonly kind: 'fieldline-frequency';
  readonly digits: readonly FieldlineDigit[];
  readonly groupGapPx: number;
  /** One size for every cell: the axis on which this grammar refuses to rank. */
  readonly fontSizePx: number;
  readonly fontWeight: number;
  /** Empty on purpose — geometry carries the grouping, not a glyph. */
  readonly separator: '';
  readonly align: 'start';
  readonly text: string;
  readonly unknown: boolean;
  readonly style: Readonly<Record<string, string>>;
}

const finiteNumber = (fields: RendererViewModel['fields'], key: string): number | null => {
  const value = fields[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
};

/** How many of the three MHz digits shift off — capped at 2 so the group never empties. */
const mhzShift = (digits: string): number =>
  Math.min(3 - digits.slice(0, 3).replace(/^0+/, '').length, 2);

/** The one multiplier that may invert a cell, or null when nothing is tuned. */
function tunedMultiplier(raw: number | null): number | null {
  if (raw === null || raw <= 0) return null;
  const exponent = Math.log10(raw);
  return Number.isInteger(exponent) && exponent <= 8 ? raw : null;
}

export function renderFrequency(
  viewModel: RendererViewModel, tokens: DesignLanguageTokens,
): FieldlineFrequency {
  const style = {
    fontFamily: tokens.typography.fontFamily,
    fontWeight: String(tokens.frequency.digitWeight),
    fontVariantNumeric: tokens.typography.fontVariantNumeric,
    letterSpacing: DIGIT_TRACKING,
  };
  const base = {
    kind: 'fieldline-frequency', groupGapPx: GROUP_GAP_PX, fontSizePx: DIGIT_SIZE_PX,
    fontWeight: tokens.frequency.digitWeight, separator: '', align: 'start', style,
  } as const;

  const hz = finiteNumber(viewModel.fields, 'frequencyHz');
  if (hz === null) return { ...base, digits: [], text: UNKNOWN_TEXT, unknown: true };

  const digits = String(Math.max(0, Math.floor(hz))).padStart(9, '0');
  const shift = mhzShift(digits);
  const tuned = tunedMultiplier(finiteNumber(viewModel.fields, 'tuningMultiplier'));
  const cells = [...digits].flatMap((char, index): FieldlineDigit[] => {
    if (index < shift) return []; // a leading MHz zero is shifted off, not ghosted
    const multiplier = 10 ** (8 - index);
    return [{
      char,
      multiplier,
      group: GROUP_NAMES[Math.floor(index / 3)],
      inverted: multiplier === tuned,
      startsGroup: index % 3 === 0 || index === shift,
    }];
  });

  return { ...base, digits: cells, text: cells.map((c) => c.char).join(''), unknown: false };
}
