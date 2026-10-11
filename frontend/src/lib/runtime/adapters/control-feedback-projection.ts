/**
 * Control-feedback projection — the pure core split out of
 * `panel-adapters.ts`. Every function here is a function of its
 * arguments, with one acknowledged environment read:
 * `projectControlFeedback` resolves stored refusal codes through
 * `blockedReasonLabel` (`$lib/i18n/blocked-reasons`), whose sentence
 * depends on the active locale. The store module below is imported
 * type-only; no runtime singleton, store value, or clock is read.
 */

import { blockedReasonLabel } from '$lib/i18n/blocked-reasons';
import type {
  CommandLifecycle,
  ControlFeedbackScope,
  StateBackedCommandDescriptor,
  StateBackedRepeatPolicy,
} from '$lib/stores/commands.svelte';
import type { ServerState } from '$lib/types/state';
import type { Capabilities } from '$lib/types/capabilities';
import type { DisplayObservation } from '../../../semantic/radio-view-model';
import {
  projectNrLevel,
  resolveControlContract,
} from '$lib/radio/filter-controls';

export type FilterWidthCommandPhase = 'unavailable' | 'idle' | 'pending' | 'acknowledged' | 'confirmed';
export type FilterWidthCommandOutcome = 'confirmed' | 'failed' | 'timed-out' | 'cancelled' | 'superseded';
export interface FilterWidthLifecyclePresentation {
  /** Opaque correlation value; consumers must compare it, never parse it. */
  readonly lifecycleId: string;
  /** Opaque correlation value for this lifecycle status transition. */
  readonly transitionId: string;
  readonly receiver: 0 | 1;
  readonly sessionEpoch: number;
  readonly target: number;
  readonly status: 'pending' | 'acknowledged' | FilterWidthCommandOutcome;
  readonly error?: string;
}
export interface FilterWidthCommandLifecycleView {
  confirmed: number | null; target: number | null; phase: FilterWidthCommandPhase; busy: boolean;
  outcome: { phase: FilterWidthCommandOutcome; error?: string } | null;
  presentation: Readonly<FilterWidthLifecyclePresentation> | null;
}

export type ControlFeedbackPhase = 'unavailable' | 'idle' | 'submitted' | 'queued' | 'dispatched'
  | 'awaiting-confirmation' | 'confirmed' | 'failed' | 'timed-out' | 'cancelled' | 'superseded';
export type ControlFeedbackOutcome = 'confirmed' | 'failed' | 'timed-out' | 'cancelled' | 'superseded';
export interface ControlFeedback<T> {
  readonly confirmed: T | null;
  readonly target: T | null;
  readonly requestedTarget: T | null;
  readonly phase: ControlFeedbackPhase;
  readonly busy: boolean;
  readonly availability: 'available' | 'unavailable';
  readonly outcome: Readonly<{ phase: ControlFeedbackOutcome; error?: string }> | null;
  readonly lifecycleId: string | null;
  readonly transitionId: string | null;
  readonly providerGeneration?: number | null;
  readonly sessionEpoch: number;
  readonly scope: Readonly<ControlFeedbackScope>;
  readonly repeatPolicy: StateBackedRepeatPolicy;
}

const sameFeedbackScope = (left: ControlFeedbackScope | null, right: ControlFeedbackScope): boolean =>
  left !== null && left.control === right.control && left.receiver === right.receiver
    && (left.slot ?? null) === (right.slot ?? null);

/** Pure projection of explicit lifecycle input plus canonical StateStore truth. */
export function projectControlFeedback<T>(
  descriptor: StateBackedCommandDescriptor<T>, state: ServerState | null,
  commands: readonly CommandLifecycle[], scope: ControlFeedbackScope, currentSessionEpoch: number,
  superseded: (command: CommandLifecycle) => boolean,
): Readonly<ControlFeedback<T>> {
  const candidateProviderGeneration = state?.providerGeneration;
  const currentProviderGeneration = typeof candidateProviderGeneration === 'number'
    && Number.isSafeInteger(candidateProviderGeneration) && candidateProviderGeneration >= 0
    ? candidateProviderGeneration : null;
  const empty = (availability: 'available' | 'unavailable', confirmed: T | null): Readonly<ControlFeedback<T>> => Object.freeze({
    confirmed, target: null, requestedTarget: null,
    phase: availability === 'available' ? 'idle' : 'unavailable', busy: false, availability,
    outcome: null, lifecycleId: null, transitionId: null,
    providerGeneration: currentProviderGeneration, sessionEpoch: currentSessionEpoch,
    scope: Object.freeze({ ...scope }), repeatPolicy: descriptor.repeatPolicy,
  });
  if (state === null) return empty('unavailable', null);
  const field = state.fieldStatus?.[descriptor.fieldPath(scope)];
  const confirmed = descriptor.confirmed(state, scope);
  // R29(1): a field that has been observed and carries a value stays
  // available even once it goes stale — only a field the server has never
  // resolved (`availability: 'missing'`, or any other non-evidentiary
  // value) gates the control unavailable. Freshness alone is deliberately
  // not consulted here: `availability` already folds it in 1:1 on the wire
  // (`_freshness_availability`, `src/rigplane/web/runtime_helpers.py`).
  if (confirmed === null || field?.observed !== true
    || (field.availability !== 'available' && field.availability !== 'stale')
    || typeof field.lastObservedMonotonic !== 'number'
    || !Number.isFinite(field.lastObservedMonotonic)) return empty('unavailable', null);

  let latest: CommandLifecycle | null = null;
  for (const command of commands) {
    const commandProviderGeneration = command.providerGeneration;
    const remoteSuperseded = command.terminalOutcome === 'superseded';
    if (currentProviderGeneration === null
      || typeof commandProviderGeneration !== 'number'
      || !Number.isSafeInteger(commandProviderGeneration) || commandProviderGeneration < 0
      || commandProviderGeneration !== currentProviderGeneration
      || command.originalEpoch !== currentSessionEpoch || command.name !== descriptor.intentName
      || !sameFeedbackScope(descriptor.scope(command), scope) || descriptor.target(command) === null
      || command.locallyObsolete === true || (superseded(command) && !remoteSuperseded)) continue;
    if (latest === null || command.createdAt >= latest.createdAt) latest = command;
  }
  if (latest === null) return empty('available', confirmed);
  const requestedTarget = descriptor.target(latest)!;
  const terminal = latest.terminalOutcome === 'superseded' || latest.status === 'confirmed' || latest.status === 'failed'
    || latest.status === 'timed-out' || latest.status === 'cancelled';
  const phase: ControlFeedbackPhase = latest.terminalOutcome === 'superseded' ? 'superseded'
    : latest.status === 'confirmed' || latest.status === 'failed'
      || latest.status === 'timed-out' || latest.status === 'cancelled' ? latest.status
    : latest.hold !== undefined ? 'queued'
    : latest.status === 'acknowledged' ? 'awaiting-confirmation'
    : latest.dispatchedEventEpoch !== undefined ? 'dispatched' : 'submitted';
  const outcomePhase: ControlFeedbackOutcome = latest.terminalOutcome === 'superseded'
    ? 'superseded' : latest.status as ControlFeedbackOutcome;
  // MOR-1890: a TX-interlock refusal stores its reason as the kebab-case
  // semantic code (ws-client mapped it on the wire); resolve that code to
  // the operator-legible sentence here, at the one outcome-build point, so
  // every renderer (scalar renderers, per-panel issued-status formatters,
  // polite announcements) reads the SAME text. Unrecognised text — a raw
  // server message — falls back verbatim, sliced as before.
  const error = terminal && typeof latest.error === 'string' && latest.error.length > 0
    ? (blockedReasonLabel(latest.error) ?? latest.error.slice(0, 256)) : undefined;
  const transitionId = phase === 'dispatched'
    ? JSON.stringify([latest.originalEpoch, latest.id, phase, latest.dispatchedEventEpoch])
    : phase === 'queued'
      ? JSON.stringify([latest.originalEpoch, latest.id, phase, latest.hold?.eventEpoch, latest.hold?.expiresAt])
      : phase === 'superseded'
        ? JSON.stringify([latest.originalEpoch, latest.id, phase, latest.eventEpoch])
        : JSON.stringify([latest.originalEpoch, latest.id, latest.status]);
  return Object.freeze({
    confirmed, target: terminal ? null : requestedTarget, requestedTarget, phase, busy: !terminal,
    availability: 'available', outcome: terminal ? Object.freeze({ phase: outcomePhase,
      ...(error === undefined ? {} : { error }) }) : null,
    lifecycleId: JSON.stringify([latest.originalEpoch, latest.id]),
    transitionId,
    providerGeneration: currentProviderGeneration, sessionEpoch: currentSessionEpoch,
    scope: Object.freeze({ ...scope }), repeatPolicy: descriptor.repeatPolicy,
  });
}

/**
 * R29(1): a `'stale'` `DisplayObservation` still carries a real last-observed
 * value (`display-observation.ts: qualifyEvidence`) — only `'unknown'`/
 * `'unsupported'` mean the field was never resolved. Used by every accessor
 * below that qualifies a `DisplayObservation` before trusting
 * `projectControlFeedback`'s result. A type predicate (not a plain boolean
 * over `.state`) so callers keep type-narrowed access to `.value`.
 */
export function hasUsableObservation<T extends number | string | boolean>(
  observation: DisplayObservation<T>,
): observation is Extract<DisplayObservation<T>, { state: 'current' | 'stale' }> {
  return observation.state === 'current' || observation.state === 'stale';
}

export function unavailableControlFeedback(
  feedback: Readonly<ControlFeedback<number>>,
): Readonly<ControlFeedback<number>> {
  return Object.freeze({
    ...feedback, confirmed: null, target: null, requestedTarget: null,
    phase: 'unavailable' as const, busy: false, availability: 'unavailable' as const,
    outcome: null, lifecycleId: null, transitionId: null,
  });
}

type TransformedDspControlFeedbackField = 'nrLevel' | 'nbDepth';

function projectDspRawValueToDisplay(
  field: TransformedDspControlFeedbackField,
  raw: number,
  caps: Capabilities | null | undefined,
): number | null {
  if (field === 'nrLevel') {
    const value = projectNrLevel(caps, raw, true).value;
    return value !== null && Number.isFinite(value) ? value : null;
  }
  // nb_depth has no legacy default: a radio publishing no usable
  // `controls.nb_depth` domain resolves to the empty contract, whose
  // `acceptsRaw`/`rawToDisplay` fail closed instead of fabricating a
  // 1..10 display value.
  const contract = resolveControlContract(caps, 'nb_depth');
  if (!contract.acceptsRaw(raw)) return null;
  const display = contract.rawToDisplay(raw);
  return display !== null && Number.isFinite(display) ? display : null;
}

/**
 * Projects a raw feedback's `confirmed`/`target`/`requestedTarget` through
 * `project` into the display unit. A null raw stays null; any non-null raw
 * whose projection is null fails the whole feedback closed
 * (`unavailableControlFeedback`). Every other field passes through into a
 * frozen copy, so an unavailable input stays unavailable.
 */
export function projectControlFeedbackToDisplay(
  feedback: Readonly<ControlFeedback<number>>,
  project: (raw: number) => number | null,
): Readonly<ControlFeedback<number>> {
  const confirmed = feedback.confirmed === null ? null : project(feedback.confirmed);
  const target = feedback.target === null ? null : project(feedback.target);
  const requestedTarget = feedback.requestedTarget === null
    ? null : project(feedback.requestedTarget);
  if ((feedback.confirmed !== null && confirmed === null)
    || (feedback.target !== null && target === null)
    || (feedback.requestedTarget !== null && requestedTarget === null)) {
    return unavailableControlFeedback(feedback);
  }
  return Object.freeze({ ...feedback, confirmed, target, requestedTarget });
}

/** MOR-2635: the confirmed slot is already display units (the echo above
 *  decoded it). Only the pending target and its requested echo are still the
 *  raw CAT code, so only those two are decoded. A null stays null; a raw the
 *  contract rejects fails the whole feedback closed. */
export function projectPendingTargetToDisplay(
  feedback: Readonly<ControlFeedback<number>>,
  project: (raw: number) => number | null,
): Readonly<ControlFeedback<number>> {
  const target = feedback.target === null ? null : project(feedback.target);
  const requestedTarget = feedback.requestedTarget === null
    ? null : project(feedback.requestedTarget);
  if ((feedback.target !== null && target === null)
    || (feedback.requestedTarget !== null && requestedTarget === null)) {
    return unavailableControlFeedback(feedback);
  }
  return Object.freeze({ ...feedback, target, requestedTarget });
}

/** Pure raw-to-display projection; descriptor-unit confirmation remains untouched. */
export function projectDspControlFeedbackToDisplay(
  field: TransformedDspControlFeedbackField,
  rawFeedback: Readonly<ControlFeedback<number>>,
  caps: Capabilities | null | undefined,
): Readonly<ControlFeedback<number>> {
  return projectControlFeedbackToDisplay(
    rawFeedback, (raw) => projectDspRawValueToDisplay(field, raw, caps),
  );
}

export function normalizedUsableObservation(
  observation: DisplayObservation<number>,
): number | null {
  if (!hasUsableObservation(observation)) return null;
  const value = observation.value;
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1
    ? value : null;
}
