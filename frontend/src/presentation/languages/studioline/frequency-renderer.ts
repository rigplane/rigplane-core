/**
 * `studioline` frequency renderer (MOR-1073, VFO slice) — MOR-977 §2.3's
 * "ultralight, group-ranked" readout.
 *
 * Pure projection of one frequency fact. It reads exactly two named fields
 * and nothing else, so an extra field on the view model — a smuggled
 * capability payload, a raw `ptt` — cannot reach the output. Digit semantics
 * are identical to `splitFrequencyToDigits()`: nine digits, leading MHz zeros
 * shifted off but never below one digit, grouped 10^8..10^6 / 10^5..10^3 /
 * 10^2..10^0.
 */
import type { DesignLanguageTokens, RendererViewModel } from '../contract';

/** The grouping mark IS the space (U+2009) — studioline uses no dot separator. */
export const THIN_SPACE = ' ';
export const HERO_SIZE_PX = 56;
/** Half the hero step: the two-tier read has to be unmistakable, not a nuance. */
export const HZ_GROUP_SIZE_PX = 28;
/** The tuning affordance is an underline, never a weight change — 200 would reflow. */
export const UNDERLINE_THICKNESS_PX = 2;
const UNKNOWN_TEXT = '—';
const GROUP_NAMES = ['mhz', 'khz', 'hz'] as const;

export type FrequencyGroupName = (typeof GROUP_NAMES)[number];

export interface StudiolineFrequencyGroup {
  readonly group: FrequencyGroupName;
  readonly text: string;
  readonly fontSizePx: number;
  readonly fontWeight: number;
  readonly rank: 'hero' | 'ranked';
  readonly tone: 'primary' | 'muted';
}

export interface StudiolineFrequencyUnderline {
  readonly multiplier: number;
  readonly group: FrequencyGroupName;
  readonly indexInGroup: number;
  readonly thicknessPx: number;
}

export interface StudiolineFrequency {
  readonly kind: 'studioline-frequency';
  readonly groups: readonly StudiolineFrequencyGroup[];
  readonly separator: string;
  readonly text: string;
  readonly unknown: boolean;
  readonly underline: StudiolineFrequencyUnderline | null;
  readonly style: Readonly<Record<string, string>>;
}

const finiteNumber = (fields: RendererViewModel['fields'], key: string): number | null => {
  const value = fields[key];
  return typeof value === 'number' && Number.isFinite(value) ? value : null;
};

/** How many of the three MHz digits shift off — capped at 2 so the group never empties. */
const mhzShift = (digits: string): number =>
  Math.min(3 - digits.slice(0, 3).replace(/^0+/, '').length, 2);

/**
 * Locates the tuned digit by multiplier alone, then subtracts the MHz shift
 * so the underline lands under the digit as DRAWN rather than as stored — the
 * one place the leading-zero shift has to be undone.
 */
function locateUnderline(multiplier: number | null, shift: number): StudiolineFrequencyUnderline | null {
  if (multiplier === null || multiplier <= 0) return null;
  const exponent = Math.log10(multiplier);
  if (!Number.isInteger(exponent) || exponent > 8) return null;
  const digitIndex = 8 - exponent;
  const indexInGroup = (digitIndex % 3) - (digitIndex < 3 ? shift : 0);
  if (indexInGroup < 0) return null;
  return {
    multiplier,
    group: GROUP_NAMES[Math.floor(digitIndex / 3)],
    indexInGroup,
    thicknessPx: UNDERLINE_THICKNESS_PX,
  };
}

export function renderFrequency(
  viewModel: RendererViewModel, tokens: DesignLanguageTokens,
): StudiolineFrequency {
  const style = {
    fontFamily: tokens.typography.fontFamily,
    fontWeight: String(tokens.frequency.digitWeight),
    fontVariantNumeric: tokens.typography.fontVariantNumeric,
    letterSpacing: '-0.02em',
  };
  const hz = finiteNumber(viewModel.fields, 'frequencyHz');
  if (hz === null) {
    return {
      kind: 'studioline-frequency', groups: [], separator: THIN_SPACE,
      text: UNKNOWN_TEXT, unknown: true, underline: null, style,
    };
  }

  const digits = String(Math.max(0, Math.floor(hz))).padStart(9, '0');
  const shift = mhzShift(digits);
  const groups = GROUP_NAMES.map((group, index): StudiolineFrequencyGroup => {
    const start = index * 3;
    const ranked = group === 'hz';
    return {
      group,
      text: digits.slice(group === 'mhz' ? shift : start, start + 3),
      fontSizePx: ranked ? HZ_GROUP_SIZE_PX : HERO_SIZE_PX,
      fontWeight: tokens.frequency.digitWeight,
      rank: ranked ? 'ranked' : 'hero',
      tone: ranked ? 'muted' : 'primary',
    };
  });

  return {
    kind: 'studioline-frequency',
    groups,
    separator: THIN_SPACE,
    text: groups.map((g) => g.text).join(THIN_SPACE),
    unknown: false,
    underline: locateUnderline(finiteNumber(viewModel.fields, 'tuningMultiplier'), shift),
    style,
  };
}
