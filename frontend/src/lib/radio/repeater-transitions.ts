/**
 * MOR-2111 PR2 — the pure repeater command-layer vocabulary shared by the
 * command handlers (`panel-commands.ts`) and the repeater strip's
 * presentation (`VfoSurface.svelte`). No store, transport, or radio import:
 * every function is a pure mapping whose inputs the caller supplies.
 *
 * The tone-mode selector (OFF / TONE / TSQL) maps onto the Yaesu `CT`
 * squelch-type register, which cannot express the pair (tone off, TSQL on).
 * The transition TABLE itself is the literal command dispatch in
 * `panel-commands.ts::makeRepeaterHandlers` — kept there so the conformance
 * completeness ledger sees each `dispatchRadioIntent({ name: '<literal>' })`
 * frame. This module carries only the pure helpers that table and the strip
 * share: the shift direction codec, the chart stepper, and the display
 * formatter.
 *
 *   CT code: 0 = both off, 1 = ENC on (TONE), 2 = ENC+DEC on (TSQL).
 *
 * `RepeaterToneMode`/`RepeaterShift` mirror the semantic view-model's
 * same-named types (MOR-2111 PR1) structurally; they are declared here too
 * because this module is the command-layer vocabulary and must stay
 * importable by `panel-commands.ts` without a `lib/runtime -> semantic`
 * dependency (v3 ADR invariant 1).
 */
export type RepeaterToneMode = 'off' | 'tone' | 'tsql';
export type RepeaterShift = 'simplex' | 'minus' | 'plus';

/**
 * The per-receiver in-flight repeater targets the wiring folds into the
 * strip's pending markers. `null` = nothing pending; each member is derived
 * from the existing command-lifecycle accessors (`getPendingRepeater*`).
 */
export interface RepeaterStripPending {
  readonly toneMode: RepeaterToneMode | null;
  readonly shift: RepeaterShift | null;
  readonly toneFreq: boolean;
}

/** The wire `OS` P2 direction for a shift choice (ARS 3 is never offered). */
export function shiftDirection(shift: RepeaterShift): number {
  return shift === 'simplex' ? 0 : shift === 'plus' ? 1 : 2;
}

/**
 * Step a CTCSS frequency (centiHz) through the profile's chart. Clamps at
 * both ends — never wraps, so a rapid tap cannot silently jump the chart.
 * A current value outside the chart snaps to the nearest end rather than
 * failing: the radio only ever holds exact members, so this is a guard, not
 * a normal path.
 */
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
