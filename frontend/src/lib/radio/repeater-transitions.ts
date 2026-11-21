/**
 * MOR-2111 — the pure repeater command-layer vocabulary shared by
 * `panel-commands.ts`, `radio-view-model-adapter.ts`,
 * `SemanticRadioSurfaces.svelte` and `RepeaterSurface.svelte`. No store,
 * transport, or radio import. The OFF/TONE/TSQL selector's transition table
 * is the literal dispatch in `panel-commands.ts::makeRepeaterHandlers` (kept
 * there so the conformance ledger sees each `name:` literal).
 *
 * `RepeaterToneMode`/`RepeaterShift` mirror the semantic view-model's
 * same-named types structurally; they are declared here too so
 * `panel-commands.ts` can import them without a `lib/runtime -> semantic`
 * dependency.
 */
export type RepeaterToneMode = 'off' | 'tone' | 'tsql';
export type RepeaterShift = 'simplex' | 'plus' | 'minus' | 'ars';

/** One receiver's in-flight repeater targets; `null`/`false` = nothing
 *  pending. */
export interface RepeaterPending {
  readonly toneMode: RepeaterToneMode | null;
  readonly shift: RepeaterShift | null;
  readonly toneFreq: boolean;
  readonly offsetHz: boolean;
}

/** Indexed by the wire `OS` P2 direction. */
const SHIFT_BY_DIRECTION: readonly RepeaterShift[] = ['simplex', 'plus', 'minus', 'ars'];

/** The wire `OS` P2 direction for a shift choice. */
export function shiftDirection(shift: RepeaterShift): number {
  return SHIFT_BY_DIRECTION.indexOf(shift);
}

/** The shift choice for a wire `OS` P2 direction, or `null` for any other
 *  number. */
export function shiftFromDirection(direction: number): RepeaterShift | null {
  return SHIFT_BY_DIRECTION[direction] ?? null;
}

/** Step a CTCSS frequency (centiHz) through the profile's chart, clamped at
 *  both ends (never wraps). An on-chart value moves one chart position. An
 *  off-chart value moves to the nearest chart value in the pressed direction,
 *  or to the chart's end in that direction when no such value exists. */
export function stepToneFreq(
  currentHz: number,
  tones: readonly number[],
  direction: 1 | -1,
): number {
  if (tones.length === 0) return currentHz;
  const index = tones.indexOf(currentHz);
  if (index !== -1) {
    const next = Math.min(Math.max(index + direction, 0), tones.length - 1);
    return tones[next];
  }
  const ahead = tones.filter((tone) => (direction === 1 ? tone > currentHz : tone < currentHz));
  if (ahead.length > 0) return direction === 1 ? Math.min(...ahead) : Math.max(...ahead);
  return direction === 1 ? Math.max(...tones) : Math.min(...tones);
}

/** One decimal place, e.g. 8850 centiHz → "88.5". */
export function formatToneHz(centiHz: number): string {
  return (centiHz / 100).toFixed(1);
}

/** The duplex offset stepper's step (MOR-3624): 100 kHz per press — the
 * common 600 kHz / 1.6 MHz / 5 MHz / 7.6 MHz offsets all land on it, and
 * it clears the wire's 100 Hz resolution by three orders. */
export const OFFSET_STEP_HZ = 100_000;

/** The 3-byte BCD wire ceiling for one duplex offset (99,999,900 Hz); the
 * stepper clamps there. The profile's own (lower) declared maximum is
 * enforced server-side by `set_repeater_offset`. */
export const OFFSET_MAX_HZ = 99_999_900;

/** Step a duplex offset by OFFSET_STEP_HZ, clamped at
 * [0, OFFSET_MAX_HZ] (never wraps). */
export function stepOffsetHz(currentHz: number, direction: 1 | -1): number {
  return Math.min(Math.max(currentHz + direction * OFFSET_STEP_HZ, 0), OFFSET_MAX_HZ);
}

/** Megahertz with trailing zeros trimmed, e.g. 600000 → "0.6M",
 *  7600000 → "7.6M", 20000000 → "20M". */
export function formatOffsetHz(hz: number): string {
  return `${(hz / 1e6).toFixed(3).replace(/\.?0+$/, '')}M`;
}
