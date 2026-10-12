/**
 * IF-shift feedback merge and filter-width command lifecycle view — the
 * pure decision core behind the `panel-adapters.ts` accessors
 * `getIfShiftControlFeedback` and `getFilterWidthCommandLifecycle`. Every
 * function here is a function of its arguments; no runtime singleton,
 * store value, or clock is read.
 */

import { deriveIfShift, measuredPbtRawToHz, pbtRangeFromCaps } from '$lib/radio/filter-controls';
import type { ControlFeedbackScope } from '$lib/stores/commands.svelte';
import type { ServerState } from '$lib/types/state';
import type { Capabilities } from '$lib/types/capabilities';
import type {
  ControlFeedback,
  ControlFeedbackOutcome,
  ControlFeedbackPhase,
  FilterWidthCommandLifecycleView,
  FilterWidthLifecyclePresentation,
} from './control-feedback-projection';

/**
 * IF-shift feedback is always Hz-domain, never PBT's raw BCD domain: real on
 * a radio with its own `if_shift` command (raw IS Hz there, identity-
 * mapped), or derived from the two PBT feedbacks by converting each side's
 * raw value on the measured lattice first. `domain` is a real, checkable
 * field — not a comment that can rot — so a caller or test can assert it
 * instead of trusting prose about which domain `confirmed`/`target` are in.
 */
export interface IfShiftControlFeedback extends ControlFeedback<number> {
  readonly domain: 'hz';
}

/**
 * Non-terminal phases in lifecycle order, least advanced first. Read only
 * from a side whose `busy` is true, which (per `projectControlFeedback`)
 * means its `phase` is one of exactly these four — never `'idle'` or a
 * terminal phase. A `'queued'` (held-for-tx) overlay can land on a command
 * that is already `'acknowledged'` underneath
 * (`commands.svelte.ts: applyCommandLifecycleProjection`'s `'held'` branch
 * accepts both `'pending'` and `'acknowledged'`), so it ranks ahead of
 * `'awaiting-confirmation'` here: a held command has made less progress
 * toward confirmation than one merely awaiting its echo, regardless of the
 * status it was held from.
 */
const IN_FLIGHT_ORDER: readonly ControlFeedbackPhase[] = Object.freeze([
  'submitted', 'queued', 'dispatched', 'awaiting-confirmation',
]);

/**
 * The least-advanced of two busy sides' phases, by `IN_FLIGHT_ORDER` — the
 * derived IF-shift feedback is never more settled than the side that has
 * made the least progress toward confirmation.
 */
function mergeBusyPhase(
  inner: Readonly<ControlFeedback<number>>, outer: Readonly<ControlFeedback<number>>,
): ControlFeedbackPhase {
  if (inner.busy && outer.busy) {
    return IN_FLIGHT_ORDER.indexOf(inner.phase) <= IN_FLIGHT_ORDER.indexOf(outer.phase)
      ? inner.phase : outer.phase;
  }
  return inner.busy ? inner.phase : outer.phase;
}

/**
 * Terminal outcomes ranked worst-first, used only when both sides' records
 * are live and terminal: a non-`'confirmed'` outcome on either side beats a
 * `'confirmed'` one on the other. The order among the non-`'confirmed'`
 * outcomes is otherwise arbitrary but fixed, so two differently-failed
 * sides still resolve to one deterministic outcome.
 */
const TERMINAL_OUTCOME_PRIORITY: readonly ControlFeedbackOutcome[] = Object.freeze([
  'failed', 'timed-out', 'cancelled', 'superseded', 'confirmed',
]);

/**
 * Combines two non-busy sides' phase/outcome. `outcome === null` here means
 * idle under `getPbtInner/OuterControlFeedback`'s own definition: no
 * lifecycle ever ran, or one did and its record already retired
 * (`commands.svelte.ts: retainTerminalOutcome` expires each record
 * `OUTCOME_RETENTION_MS` after its own terminal transition, independent of
 * the other side's), or `projectControlFeedback` filtered it out
 * (`locallyObsolete`, provider-generation mismatch). Idle is therefore not
 * "no lifecycle ran" — it can equally mean "ran and the evidence is gone" —
 * which is why an idle side may never promote the other to `'confirmed'`:
 *
 *   idle | idle                -> idle       (case A: no evidence either side)
 *   idle | confirmed           -> idle       (case B: a lone confirmed half
 *                                              is not corroborated once the
 *                                              other half's record is gone;
 *                                              not reported)
 *   idle | non-confirmed       -> that outcome (case C: while its record
 *                                                stays live)
 *   live terminal | live terminal -> `TERMINAL_OUTCOME_PRIORITY` (case D)
 *
 * `'confirmed'` is reachable only through case D with both sides confirmed:
 * a half-failed gesture is never derived as `'confirmed'`, at any point in
 * its retention window or after.
 */
function mergeTerminalOutcome(
  inner: Readonly<ControlFeedback<number>>, outer: Readonly<ControlFeedback<number>>,
): { phase: ControlFeedbackPhase; outcome: Readonly<{ phase: ControlFeedbackOutcome; error?: string }> | null } {
  const bothIdle = inner.outcome === null && outer.outcome === null;
  if (bothIdle) return { phase: 'idle', outcome: null }; // case A

  const bothLiveTerminal = inner.outcome !== null && outer.outcome !== null;
  if (bothLiveTerminal) { // case D
    const winner = TERMINAL_OUTCOME_PRIORITY.indexOf(inner.outcome.phase)
      <= TERMINAL_OUTCOME_PRIORITY.indexOf(outer.outcome.phase) ? inner : outer;
    return { phase: winner.phase, outcome: winner.outcome };
  }

  const live = inner.outcome !== null ? inner : outer; // exactly one side idle
  if (live.outcome!.phase === 'confirmed') return { phase: 'idle', outcome: null }; // case B
  return { phase: live.phase, outcome: live.outcome }; // case C
}

function unavailableIfShiftFeedback(
  scope: Readonly<ControlFeedbackScope>, sessionEpoch: number,
  providerGeneration: number | null,
): Readonly<IfShiftControlFeedback> {
  return Object.freeze({
    confirmed: null, target: null, requestedTarget: null,
    phase: 'unavailable' as const, busy: false, availability: 'unavailable' as const,
    outcome: null, lifecycleId: null, transitionId: null,
    providerGeneration, sessionEpoch, scope,
    repeatPolicy: 'latest-target-wins' as const, domain: 'hz' as const,
  });
}

/**
 * Derived IF-shift feedback from the two PBT feedbacks: each side's raw
 * value converts on the MEASURED lattice (the current mode's declared
 * `pbtStepHz` and the observed filter width, the same conversion
 * `deriveFilterPassband` reads its `ifShift` fallback with) before
 * combining with `deriveIfShift` — this is that same formula's
 * pending-target-aware lifecycle layer, not a second, independent
 * re-derivation of the confirmed reading. `readLattice` supplies the
 * measured twin-PBT lattice for the active receiver, or null when none
 * forms. No lattice — a legacy payload without `pbtStepHz`, or a width
 * that has not been observed — converts nothing, so the derived feedback
 * is unavailable: no fabricated +/-1200-scale Hz, the same fail-closed
 * refusal the reading path applies.
 */
export function deriveIfShiftFeedback(
  inner: Readonly<ControlFeedback<number>>,
  outer: Readonly<ControlFeedback<number>>,
  state: ServerState | null,
  caps: Capabilities | null | undefined,
  epoch: number,
  readLattice: () => Readonly<{ filterWidthHz: number; stepHz: number }> | null,
): Readonly<IfShiftControlFeedback> {
  const receiver: 0 | 1 = state?.active === 'SUB' ? 1 : 0;
  const scope = Object.freeze({ control: 'if-shift', receiver });
  const hasDeclaredScale = pbtRangeFromCaps(caps) !== undefined;
  const providerGeneration = typeof inner.providerGeneration === 'number' ? inner.providerGeneration : null;
  if (inner.availability !== 'available' || outer.availability !== 'available' || !hasDeclaredScale
    || inner.confirmed === null || outer.confirmed === null) {
    return unavailableIfShiftFeedback(scope, epoch, providerGeneration);
  }
  // Raw -> Hz on the measured lattice — the mode's declared step at the
  // observed width — the same conversion the reading path uses. A raw that
  // converts to nothing (no step for the mode, no observed width) fails the
  // whole derived feedback closed rather than fabricating Hz.
  const lattice = readLattice();
  const toHz = (raw: number): number | null => lattice === null
    ? null : measuredPbtRawToHz(raw, lattice.filterWidthHz, lattice.stepHz);
  const pairHz = (a: number, b: number): number | null => {
    const innerHz = toHz(a);
    const outerHz = toHz(b);
    return innerHz === null || outerHz === null ? null : deriveIfShift(innerHz, outerHz);
  };
  const confirmed = pairHz(inner.confirmed, outer.confirmed);
  if (confirmed === null) {
    return unavailableIfShiftFeedback(scope, epoch, providerGeneration);
  }
  const busy = inner.busy || outer.busy;
  const innerForTarget = inner.busy && inner.target !== null ? inner.target : inner.confirmed;
  const outerForTarget = outer.busy && outer.target !== null ? outer.target : outer.confirmed;
  const target = busy ? pairHz(innerForTarget, outerForTarget) : null;
  const innerForRequested = inner.requestedTarget ?? inner.confirmed;
  const outerForRequested = outer.requestedTarget ?? outer.confirmed;
  const requestedTarget = inner.requestedTarget === null && outer.requestedTarget === null
    ? null : pairHz(innerForRequested, outerForRequested);
  const { phase, outcome } = busy
    ? { phase: mergeBusyPhase(inner, outer), outcome: null }
    : mergeTerminalOutcome(inner, outer);
  // Idle carries no ids here, so that
  // `mergeTerminalOutcome`'s idle-on-a-retired-confirmation case (case B
  // above) does not compose a fresh id out of the surviving side's live
  // outcome and trigger a one-shot announcement for a phase nobody reports.
  const lifecycleId = phase === 'idle' || (inner.lifecycleId === null && outer.lifecycleId === null)
    ? null : JSON.stringify([inner.lifecycleId, outer.lifecycleId]);
  const transitionId = phase === 'idle' || (inner.transitionId === null && outer.transitionId === null)
    ? null : JSON.stringify([inner.transitionId, outer.transitionId]);
  return Object.freeze({
    confirmed, target, requestedTarget, phase, busy, availability: 'available' as const,
    outcome, lifecycleId, transitionId,
    providerGeneration, sessionEpoch: inner.sessionEpoch,
    scope, repeatPolicy: 'latest-target-wins' as const, domain: 'hz' as const,
  });
}

function filterWidthPresentation(
  feedback: Readonly<ControlFeedback<number>>,
): Readonly<FilterWidthLifecyclePresentation> {
  const status = feedback.phase === 'submitted' ? 'pending'
    : feedback.phase === 'queued' || feedback.phase === 'dispatched' ? 'pending'
    : feedback.phase === 'awaiting-confirmation' ? 'acknowledged'
    : feedback.phase === 'superseded' ? 'cancelled' : feedback.phase;
  const error = feedback.outcome?.error;
  return Object.freeze({
    lifecycleId: feedback.lifecycleId!, transitionId: feedback.transitionId!,
    receiver: feedback.scope.receiver, sessionEpoch: feedback.sessionEpoch,
    target: feedback.requestedTarget!, status: status as FilterWidthLifecyclePresentation['status'],
    ...(error === undefined ? {} : { error }),
  });
}

/**
 * Maps a filter-width `ControlFeedback` onto the lifecycle view the
 * spectrum panel renders: unavailable or idle feedback yields an id-less
 * view with no presentation; `confirmed` reports a confirmed outcome; the
 * other terminal phases report their outcome at an idle phase (the
 * confirmed reading stays authoritative); every in-flight phase stays busy
 * with a presentation record.
 */
export function toFilterWidthCommandLifecycle(
  feedback: Readonly<ControlFeedback<number>>,
): FilterWidthCommandLifecycleView {
  if (feedback.availability === 'unavailable') {
    return { confirmed: null, target: null, phase: 'unavailable', busy: false, outcome: null, presentation: null };
  }
  if (feedback.phase === 'idle') {
    return { confirmed: feedback.confirmed, target: null, phase: 'idle', busy: false, outcome: null, presentation: null };
  }
  const presentation = filterWidthPresentation(feedback);
  if (feedback.phase === 'confirmed') return { confirmed: feedback.confirmed, target: null,
    phase: 'confirmed', busy: false, outcome: { phase: 'confirmed' }, presentation };
  if (feedback.phase === 'failed' || feedback.phase === 'timed-out'
    || feedback.phase === 'cancelled' || feedback.phase === 'superseded') {
    return { confirmed: feedback.confirmed, target: null, phase: 'idle', busy: false,
      outcome: { phase: feedback.phase, error: feedback.outcome?.error }, presentation };
  }
  return { confirmed: feedback.confirmed, target: feedback.target,
    phase: feedback.phase === 'awaiting-confirmation' ? 'acknowledged' : 'pending',
    busy: true, outcome: null, presentation };
}
