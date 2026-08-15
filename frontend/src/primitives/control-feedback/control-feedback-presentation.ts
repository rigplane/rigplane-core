/** Pure consumer projection for state-backed control feedback (MOR-1711). */

export type PresentationPhase =
  | 'unavailable' | 'idle' | 'submitted' | 'queued' | 'dispatched'
  | 'awaiting-confirmation' | 'confirmed' | 'failed' | 'timed-out'
  | 'cancelled' | 'superseded';
export type PresentationOutcome = 'confirmed' | 'failed' | 'timed-out' | 'cancelled' | 'superseded';

/** Structural subset accepted from ControlFeedback<T>; no runtime owner import. */
export interface ControlFeedbackPresentationInput<T> {
  readonly confirmed: T | null;
  readonly target: T | null;
  readonly requestedTarget: T | null;
  readonly phase: PresentationPhase;
  readonly transitionId: string | null;
  readonly outcome: Readonly<{ phase: PresentationOutcome; error?: string }> | null;
}

export interface ControlFeedbackPresentationState {
  readonly announcedTransitionIds: readonly string[];
}

export interface PoliteControlAnnouncement {
  readonly politeness: 'polite';
  readonly transitionId: string;
  readonly phase: PresentationPhase;
  readonly targetDescription: string | null;
  readonly message: string;
}

export interface ControlFeedbackPresentation {
  readonly attributes: Readonly<{
    'data-command-phase': PresentationPhase;
    'aria-busy': 'true' | 'false';
  }>;
  readonly targetDescription: string | null;
  readonly politeAnnouncement: Readonly<PoliteControlAnnouncement> | null;
  readonly state: Readonly<ControlFeedbackPresentationState>;
}

const BUSY_PHASES: ReadonlySet<PresentationPhase> = new Set([
  'submitted', 'queued', 'dispatched', 'awaiting-confirmation',
]);
const PHASE_TEXT: Readonly<Record<PresentationPhase, string>> = Object.freeze({
  unavailable: 'Control unavailable', idle: 'Control idle', submitted: 'Submitting',
  queued: 'Queued', dispatched: 'Dispatched',
  'awaiting-confirmation': 'Awaiting confirmation', confirmed: 'Confirmed',
  failed: 'Failed', 'timed-out': 'Timed out', cancelled: 'Cancelled', superseded: 'Superseded',
});

/** Caller owns the returned single transition-id token; this function owns no state. */
export function projectControlFeedbackPresentation<T>(
  feedback: Readonly<ControlFeedbackPresentationInput<T>>,
  previous: Readonly<ControlFeedbackPresentationState>,
  describeTarget: (target: T) => string,
): Readonly<ControlFeedbackPresentation> {
  const target = feedback.target ?? feedback.requestedTarget;
  const targetDescription = target === null ? null : describeTarget(target);
  const shouldAnnounce = feedback.transitionId !== null
    && !previous.announcedTransitionIds.includes(feedback.transitionId);
  const state = shouldAnnounce
    ? Object.freeze({
      announcedTransitionIds: Object.freeze([...previous.announcedTransitionIds, feedback.transitionId!]),
    })
    : previous;
  const politeAnnouncement = shouldAnnounce
    ? Object.freeze({
      politeness: 'polite' as const,
      transitionId: feedback.transitionId!, phase: feedback.phase, targetDescription,
      message: targetDescription === null
        ? PHASE_TEXT[feedback.phase]
        : `${PHASE_TEXT[feedback.phase]}: ${targetDescription}`,
    })
    : null;
  return Object.freeze({
    attributes: Object.freeze({
      'data-command-phase': feedback.phase,
      'aria-busy': BUSY_PHASES.has(feedback.phase) ? 'true' : 'false',
    }),
    targetDescription, politeAnnouncement, state,
  });
}

type Confirmed<T> = Readonly<Pick<ControlFeedbackPresentationInput<T>, 'confirmed'>>;
export const confirmedPressed = (feedback: Confirmed<boolean>): boolean => feedback.confirmed === true;
export const confirmedChecked = (feedback: Confirmed<boolean>): boolean => feedback.confirmed === true;
export const confirmedSelected = <T>(feedback: Confirmed<T>, choice: T): boolean =>
  feedback.confirmed !== null && Object.is(feedback.confirmed, choice);
