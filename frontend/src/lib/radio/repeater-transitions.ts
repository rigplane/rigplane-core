/**
 * MOR-2111 — the pure repeater command-layer vocabulary shared by
 * `panel-commands.ts`, `SemanticRadioSurfaces.svelte` and
 * `RepeaterSurface.svelte`. No store, transport, or radio import. The
 * OFF/TONE/TSQL selector's transition table is the literal dispatch in
 * `panel-commands.ts::makeRepeaterHandlers` (kept there so the conformance
 * ledger sees each `name:` literal).
 *
 * `RepeaterToneMode`/`RepeaterShift` mirror the semantic view-model's
 * same-named types structurally; they are declared here too so
 * `panel-commands.ts` can import them without a `lib/runtime -> semantic`
 * dependency (v3 ADR invariant 1).
 */
export type RepeaterToneMode = 'off' | 'tone' | 'tsql';
export type RepeaterShift = 'simplex' | 'minus' | 'plus';

/** One receiver's in-flight repeater targets; `null`/`false` = nothing
 *  pending. */
export interface RepeaterPending {
  readonly toneMode: RepeaterToneMode | null;
  readonly shift: RepeaterShift | null;
  readonly toneFreq: boolean;
}

/** The wire `OS` P2 direction for a shift choice (ARS 3 is never offered). */
export function shiftDirection(shift: RepeaterShift): number {
  return shift === 'simplex' ? 0 : shift === 'plus' ? 1 : 2;
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
