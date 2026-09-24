/**
 * MOR-2111 PR2 — the pure repeater command-layer vocabulary shared by
 * `panel-commands.ts` and `VfoSurface.svelte`. No store, transport, or radio
 * import. The OFF/TONE/TSQL selector maps onto the Yaesu `CT` squelch-type
 * register (0 = off, 1 = TONE, 2 = TSQL), which cannot express tone-off +
 * TSQL-on; the transition TABLE is the literal dispatch in
 * `panel-commands.ts::makeRepeaterHandlers` (kept there so the conformance
 * ledger sees each `name:` literal). This module carries the shared helpers.
 *
 * `RepeaterToneMode`/`RepeaterShift` mirror the semantic view-model's
 * same-named types structurally; they are declared here too so
 * `panel-commands.ts` can import them without a `lib/runtime -> semantic`
 * dependency (v3 ADR invariant 1).
 */
export type RepeaterToneMode = 'off' | 'tone' | 'tsql';
export type RepeaterShift = 'simplex' | 'minus' | 'plus';

/** The per-receiver in-flight repeater targets folded into the strip's
 *  pending markers; `null` = nothing pending. */
export interface RepeaterStripPending {
  readonly toneMode: RepeaterToneMode | null;
  readonly shift: RepeaterShift | null;
  readonly toneFreq: boolean;
}

/** The wire `OS` P2 direction for a shift choice (ARS 3 is never offered). */
export function shiftDirection(shift: RepeaterShift): number {
  return shift === 'simplex' ? 0 : shift === 'plus' ? 1 : 2;
}

/** Step a CTCSS frequency (centiHz) through the profile's chart, clamped at
 *  both ends (never wraps). An off-chart value snaps to the nearest end. */
export function stepToneFreq(
  currentHz: number,
  tones: readonly number[],
  direction: 1 | -1,
): number {
  if (tones.length === 0) return currentHz;
  const index = tones.indexOf(currentHz);
  if (index === -1) return direction === 1 ? tones[0] : tones[tones.length - 1];
  const next = index + direction;
  if (next < 0) return tones[0];
  if (next >= tones.length) return tones[tones.length - 1];
  return tones[next];
}

/** One decimal place, e.g. 8850 centiHz → "88.5". */
export function formatToneHz(centiHz: number): string {
  return (centiHz / 100).toFixed(1);
}
