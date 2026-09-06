import {
  projectControlFeedbackPresentation,
  type ControlFeedbackPresentation,
  type ControlFeedbackPresentationInput,
  type ControlFeedbackPresentationState,
  type PresentationOutcome,
} from '../control-feedback/control-feedback-presentation';
export type ScalarFeedback = Readonly<ControlFeedbackPresentationInput<number>>;
export interface CommittedScalarInput {
  readonly feedback: ScalarFeedback;
  readonly editable: boolean;
  readonly contextKey: string;
}
export interface CommittedScalarPolicy {
  accepts(value: number): boolean;
  normalize(value: number): number | null;
  readonly draftPolicy: 'reject-invalid' | 'normalize';
  describeTarget(value: number): string;
}
export interface CommittedScalarView {
  readonly feedback: ScalarFeedback;
  readonly confirmed: number | null;
  readonly draft: number | null;
  readonly displayed: number | null;
  readonly editable: boolean;
  readonly editing: boolean;
  readonly presentation: Readonly<ControlFeedbackPresentation>;
  readonly announcement: string | null;
}
export interface CommittedScalar {
  readonly view: Readonly<CommittedScalarView>;
  input(value: number): void;
  commit(value: number): number | null;
  cancel(): number | null;
}

type DraftIdentity = {
  contextKey: string;
  editable: boolean;
  confirmed: number | null;
  target: number | null;
  requestedTarget: number | null;
  phase: ScalarFeedback['phase'];
  transitionId: string | null;
  outcomePhase: PresentationOutcome | null;
  outcomeError: string | undefined;
};
function identityOf(input: Readonly<CommittedScalarInput>): DraftIdentity {
  return Object.freeze({
    contextKey: input.contextKey,
    editable: input.editable,
    confirmed: input.feedback.confirmed,
    target: input.feedback.target,
    requestedTarget: input.feedback.requestedTarget,
    phase: input.feedback.phase,
    transitionId: input.feedback.transitionId,
    outcomePhase: input.feedback.outcome?.phase ?? null,
    outcomeError: input.feedback.outcome?.error,
  });
}

function sameIdentity(left: DraftIdentity, right: DraftIdentity): boolean {
  return left.contextKey === right.contextKey
    && left.editable === right.editable
    && Object.is(left.confirmed, right.confirmed)
    && Object.is(left.target, right.target)
    && Object.is(left.requestedTarget, right.requestedTarget)
    && left.phase === right.phase
    && left.transitionId === right.transitionId
    && left.outcomePhase === right.outcomePhase
    && left.outcomeError === right.outcomeError;
}

export function createCommittedScalar(
  read: () => Readonly<CommittedScalarInput>,
  policy: Readonly<CommittedScalarPolicy>,
  request: (value: number) => void,
): CommittedScalar {
  let draft = $state<number | null>(null);
  let draftIdentity: DraftIdentity | null = null;
  let suppressCommit = false;
  let presentationState: Readonly<ControlFeedbackPresentationState> = { announcedTransitionIds: [] };
  let announcement: string | null = null;

  function isEditable(input: Readonly<CommittedScalarInput>): boolean {
    return input.editable && input.feedback.phase !== 'unavailable';
  }

  function authoritativeDisplayed(
    input: Readonly<CommittedScalarInput>,
    presentation: Readonly<ControlFeedbackPresentation>,
  ): number | null {
    if (input.feedback.phase === 'unavailable') return null;
    if (presentation.attributes['aria-busy'] === 'true') {
      return input.feedback.target ?? input.feedback.requestedTarget ?? input.feedback.confirmed;
    }
    return input.feedback.confirmed;
  }

  function reconcile(input: Readonly<CommittedScalarInput>): void {
    if (draft === null || draftIdentity === null) return;
    if (sameIdentity(draftIdentity, identityOf(input))) return;
    draftIdentity = null;
    suppressCommit = true;
  }

  function activeDraft(input: Readonly<CommittedScalarInput>): number | null {
    if (draft === null || draftIdentity === null) return null;
    return sameIdentity(draftIdentity, identityOf(input)) ? draft : null;
  }

  function project(input: Readonly<CommittedScalarInput>): Readonly<ControlFeedbackPresentation> {
    const presentation = projectControlFeedbackPresentation(
      input.feedback,
      presentationState,
      policy.describeTarget,
    );
    presentationState = presentation.state;
    if (presentation.politeAnnouncement !== null) {
      announcement = presentation.politeAnnouncement.message;
    }
    return presentation;
  }

  function normalize(value: number): number | null {
    const normalized = policy.normalize(value);
    return normalized !== null && Number.isFinite(normalized) && policy.accepts(normalized)
      ? normalized : null;
  }

  function viewOf(input: Readonly<CommittedScalarInput>): Readonly<CommittedScalarView> {
    reconcile(input);
    const presentation = project(input);
    const confirmed = input.feedback.phase === 'unavailable' ? null : input.feedback.confirmed;
    const currentDraft = activeDraft(input);
    return Object.freeze({
      feedback: input.feedback,
      confirmed,
      draft: currentDraft,
      displayed: currentDraft ?? authoritativeDisplayed(input, presentation),
      editable: isEditable(input),
      editing: currentDraft !== null,
      presentation,
      announcement,
    });
  }
  function restore(input: Readonly<CommittedScalarInput>): number | null {
    return authoritativeDisplayed(input,
      projectControlFeedbackPresentation(input.feedback, presentationState, policy.describeTarget));
  }

  return {
    get view() { return viewOf(read()); },
    input(value: number): void {
      const current = read();
      reconcile(current);
      suppressCommit = false;
      if (!isEditable(current)) return;
      const candidate = policy.draftPolicy === 'normalize'
        ? normalize(value)
        : Number.isFinite(value) && policy.accepts(value) ? value : null;
      if (candidate === null) return;
      draft = candidate;
      draftIdentity = identityOf(current);
    },
    commit(value: number): number | null {
      const current = read();
      reconcile(current);
      if (suppressCommit) {
        suppressCommit = false;
        return restore(current);
      }
      if (!isEditable(current)) return restore(current);
      const candidate = normalize(activeDraft(current) ?? value);
      draft = null;
      draftIdentity = null;
      if (candidate === null) return restore(current);
      request(candidate);
      return null;
    },
    cancel(): number | null {
      const current = read();
      reconcile(current);
      draft = null;
      draftIdentity = null;
      suppressCommit = true;
      return restore(current);
    },
  };
}
