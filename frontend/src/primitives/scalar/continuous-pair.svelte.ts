import {
  projectControlFeedbackPresentation,
  type ControlFeedbackPresentation,
  type ControlFeedbackPresentationState,
} from '../control-feedback/control-feedback-presentation';
import {
  createContinuousScalar,
  type CommandScalarFeedback,
  type ContinuousScalarBinding,
  type ContinuousScalarPolicy,
  type ContinuousScalarRendererLease,
  type ScalarDomain,
  type ScalarKeyInput,
  type ScalarPhase,
  type ScalarReading,
  type ScalarSource,
  type ScalarStepInput,
} from './continuous-scalar.svelte';
import {
  clamp,
  dualParamNormXFromValues,
  dualParamStepAlongAxis,
  dualParamValuesFromNormX,
} from './value-control-core';

export interface ContinuousPairValues {
  readonly rf: number;
  readonly sql: number;
}

export interface PairReadingLane {
  readonly reading: ScalarReading;
  readonly availability: 'available' | 'unavailable';
}

export interface PairCommandLane {
  readonly command: string;
  readonly feedback: Readonly<CommandScalarFeedback>;
}

interface ContinuousPairInputBase {
  readonly domain: ScalarDomain;
  readonly enabled: boolean;
  readonly requestRf: (value: number) => void;
  readonly requestSql: (value: number) => void;
}

export interface ReadingContinuousPairInput extends ContinuousPairInputBase {
  readonly evidence: 'reading';
  readonly ownerKey: string;
  readonly rf: Readonly<PairReadingLane>;
  readonly sql: Readonly<PairReadingLane>;
}

export interface CommandFeedbackContinuousPairInput extends ContinuousPairInputBase {
  readonly evidence: 'command-feedback';
  readonly rf: Readonly<PairCommandLane>;
  readonly sql: Readonly<PairCommandLane>;
}

export type ContinuousPairInput =
  | ReadingContinuousPairInput
  | CommandFeedbackContinuousPairInput;

export interface ContinuousPairPolicy {
  readonly name: string;
  readonly preview: 'optimistic' | 'confirmed';
  wheel(
    current: Readonly<ContinuousPairValues>,
    event: ScalarStepInput,
    domain: ScalarDomain,
  ): Readonly<ContinuousPairValues> | null;
  key(
    current: Readonly<ContinuousPairValues>,
    event: ScalarKeyInput,
    domain: ScalarDomain,
  ): Readonly<ContinuousPairValues> | null;
  reset(domain: ScalarDomain): Readonly<ContinuousPairValues> | null;
  dispatch(source: ScalarSource): 'immediate' | Readonly<{ debounceMs: number }>;
  readonly wheelIdleMs: 0 | 300;
}

const nativePolicy: ContinuousPairPolicy = {
  name: 'native-range-pair',
  preview: 'optimistic',
  wheel: () => null,
  key: () => null,
  reset: () => null,
  dispatch: () => 'immediate',
  wheelIdleMs: 0,
};
export const nativeRangeContinuousPairPolicy: Readonly<ContinuousPairPolicy> =
  Object.freeze(nativePolicy);

export function createLegacyContinuousPairPolicy(
  options: Readonly<{ keyboardDebounceMs: number }>,
): Readonly<ContinuousPairPolicy> {
  const keyboardDispatch = options.keyboardDebounceMs > 0
    ? Object.freeze({ debounceMs: options.keyboardDebounceMs })
    : 'immediate';
  const legacyPolicy: ContinuousPairPolicy = {
    name: 'legacy-rf-sql-pair',
    preview: 'optimistic' as const,
    wheel: (current, event, domain) => {
      const adaptive = Math.max(1, Math.ceil((domain.max - domain.min) / 255));
      const step = event.fine
        ? domain.step / domain.fineStepDivisor
        : domain.step * 4 * adaptive;
      return dualParamStepAlongAxis(
        current.rf, current.sql, event.direction, step, domain.fineStepDivisor,
        domain.min, domain.max, false,
      );
    },
    key: (current, event, domain) => {
      if (event.key !== 'ArrowLeft' && event.key !== 'ArrowRight') return null;
      return dualParamStepAlongAxis(
        current.rf, current.sql, event.key === 'ArrowRight' ? 1 : -1,
        domain.step, domain.fineStepDivisor, domain.min, domain.max, event.fine,
      );
    },
    reset: (domain) => ({ rf: domain.max, sql: domain.min }),
    dispatch: (source) => source === 'keyboard' ? keyboardDispatch : 'immediate',
    wheelIdleMs: 300,
  };
  return Object.freeze(legacyPolicy);
}

interface PairReadingLaneView {
  readonly evidence: 'reading';
  readonly reading: ScalarReading;
  readonly availability: 'available' | 'unavailable';
  readonly canonical: number | null;
  readonly localRequested: number | null;
  readonly feedback: null;
  readonly phase: null;
  readonly error: null;
  readonly presentation: null;
  readonly announcement: null;
}

interface PairCommandLaneView {
  readonly evidence: 'command-feedback';
  readonly command: string;
  readonly feedback: Readonly<CommandScalarFeedback>;
  readonly availability: 'available' | 'unavailable';
  readonly canonical: number | null;
  readonly localRequested: number | null;
  readonly phase: ScalarPhase;
  readonly error: string | null;
  readonly presentation: Readonly<ControlFeedbackPresentation>;
  readonly announcement: string | null;
}

export type ContinuousPairLaneView = Readonly<PairReadingLaneView | PairCommandLaneView>;

export interface ContinuousPairView {
  readonly evidence: ContinuousPairInput['evidence'];
  readonly domain: Readonly<ScalarDomain>;
  readonly domainValid: boolean;
  readonly canonical: Readonly<{ rf: number | null; sql: number | null }>;
  readonly position: number | null;
  readonly axisDraft: number | null;
  readonly draft: Readonly<ContinuousPairValues> | null;
  readonly displayedPosition: number | null;
  readonly editable: boolean;
  readonly busy: boolean;
  readonly interaction: 'idle' | ScalarSource;
  readonly lanes: Readonly<{ rf: ContinuousPairLaneView; sql: ContinuousPairLaneView }>;
}

export interface ContinuousPairRendererLease {
  readonly view: Readonly<ContinuousPairView>;
  beginPointer(): number | null;
  pointer(token: number, candidate: number): void;
  endPointer(token: number): void;
  cancelPointer(token: number): void;
  nativeInput(candidate: number): void;
  wheel(event: ScalarStepInput): void;
  key(event: ScalarKeyInput): boolean;
  reset(): void;
  dispose(): void;
}

export interface ContinuousPairBinding {
  readonly view: Readonly<ContinuousPairView>;
  attachRenderer(): ContinuousPairRendererLease;
  cancel(reason: 'authority' | 'availability' | 'terminal' | 'owner-dispose'): void;
  destroy(): void;
}

type Identity = readonly unknown[];
type LaneName = 'rf' | 'sql';
interface LocalRequest {
  readonly value: number;
  readonly observation: Identity;
}
interface ProjectedCandidate {
  readonly axis: number;
  readonly values: Readonly<ContinuousPairValues>;
  readonly authority: number;
  readonly source: 'wheel' | 'keyboard' | 'reset';
}

const sameIdentity = (left: Identity | null, right: Identity): boolean =>
  left !== null && left.length === right.length
  && left.every((value, index) => Object.is(value, right[index]));
const finiteValue = (value: number | null | undefined): number | null =>
  value !== null && value !== undefined && Number.isFinite(value) ? value : null;

function canonicalLane(input: Readonly<ContinuousPairInput>, lane: LaneName): number | null {
  return input.evidence === 'reading'
    ? input[lane].reading.status === 'known' ? finiteValue(input[lane].reading.value) : null
    : finiteValue(input[lane].feedback.confirmed);
}

function laneAvailable(input: Readonly<ContinuousPairInput>, lane: LaneName): boolean {
  return input.evidence === 'reading'
    ? input[lane].availability === 'available'
    : input[lane].feedback.availability === 'available';
}

function laneObservation(input: Readonly<ContinuousPairInput>, lane: LaneName): Identity {
  if (input.evidence === 'reading') {
    return Object.freeze([
      input[lane].availability, input[lane].reading.status, canonicalLane(input, lane),
    ]);
  }
  const feedback = input[lane].feedback;
  return Object.freeze([
    feedback.confirmed, feedback.target, feedback.requestedTarget, feedback.phase,
    feedback.lifecycleId, feedback.transitionId, feedback.outcome?.phase, feedback.outcome?.error,
  ]);
}

function authorityOf(input: Readonly<ContinuousPairInput>): Identity {
  const domain = input.domain;
  const shared = [
    input.evidence, input.enabled, domain.min, domain.max, domain.step,
    domain.defaultValue, domain.fineStepDivisor, domain.keyboardStep,
  ];
  if (input.evidence === 'reading') {
    return Object.freeze([
      ...shared, input.ownerKey, input.rf.availability, input.rf.reading.status,
      input.sql.availability, input.sql.reading.status,
    ]);
  }
  return Object.freeze([
    ...shared,
    input.rf.command, input.rf.feedback.sessionEpoch, input.rf.feedback.availability,
    input.rf.feedback.scope.control, input.rf.feedback.scope.receiver, input.rf.feedback.scope.slot,
    input.sql.command, input.sql.feedback.sessionEpoch, input.sql.feedback.availability,
    input.sql.feedback.scope.control, input.sql.feedback.scope.receiver, input.sql.feedback.scope.slot,
  ]);
}

const TERMINAL_PHASES: ReadonlySet<ScalarPhase> = new Set([
  'failed', 'timed-out', 'cancelled', 'superseded',
]);

function terminalOf(input: Readonly<ContinuousPairInput>): Identity | null {
  if (input.evidence === 'reading') return null;
  const identities = (['rf', 'sql'] as const).flatMap((lane) => {
    const feedback = input[lane].feedback;
    const phase = feedback.outcome !== null && TERMINAL_PHASES.has(feedback.outcome.phase)
      ? feedback.outcome.phase : TERMINAL_PHASES.has(feedback.phase) ? feedback.phase : null;
    return phase === null ? [] : [
      lane, feedback.lifecycleId, feedback.transitionId, phase,
    ];
  });
  return identities.length === 0 ? null : Object.freeze(identities);
}

function axisDomain(domain: ScalarDomain): ScalarDomain {
  const min = Number.isFinite(domain.min) ? 0 : domain.min;
  const max = Number.isFinite(domain.max) && domain.max >= domain.min ? 1 : domain.max - domain.min;
  return {
    min, max, step: domain.step, fineStepDivisor: domain.fineStepDivisor,
    defaultValue: domain.defaultValue === null ? null
      : Number.isFinite(domain.defaultValue) ? 0.5 : domain.defaultValue,
    ...(domain.keyboardStep === undefined ? {} : { keyboardStep: domain.keyboardStep }),
  };
}

function snapshotDomain(domain: ScalarDomain): Readonly<ScalarDomain> {
  return Object.freeze({
    min: domain.min, max: domain.max, step: domain.step,
    defaultValue: domain.defaultValue, fineStepDivisor: domain.fineStepDivisor,
    ...(domain.keyboardStep === undefined ? {} : { keyboardStep: domain.keyboardStep }),
  });
}

function validCommandLane(lane: Readonly<PairCommandLane>): boolean {
  const feedback = lane.feedback;
  return Number.isFinite(feedback.sessionEpoch)
    && (feedback.scope.receiver === 0 || feedback.scope.receiver === 1)
    && (feedback.target === null || Number.isFinite(feedback.target))
    && (feedback.requestedTarget === null || Number.isFinite(feedback.requestedTarget));
}

export function createContinuousPair(
  read: () => Readonly<ContinuousPairInput>,
  policy: Readonly<ContinuousPairPolicy>,
): ContinuousPairBinding {
  let localRf = $state<LocalRequest | null>(null);
  let localSql = $state<LocalRequest | null>(null);
  let lastAuthority: Identity | null = null;
  let handledTerminal: Identity | null = null;
  let authorityGeneration = 0;
  let projectedCandidate: ProjectedCandidate | null = null;
  let rfPresentation: Readonly<ControlFeedbackPresentationState> = { announcedTransitionIds: [] };
  let sqlPresentation: Readonly<ControlFeedbackPresentationState> = { announcedTransitionIds: [] };
  let scalar: ContinuousScalarBinding;

  const localOf = (lane: LaneName): LocalRequest | null => lane === 'rf' ? localRf : localSql;
  const setLocal = (lane: LaneName, value: LocalRequest | null): void => {
    if (lane === 'rf') localRf = value;
    else localSql = value;
  };

  function reconcile(input: Readonly<ContinuousPairInput>): void {
    const authority = authorityOf(input);
    if (lastAuthority !== null && !sameIdentity(lastAuthority, authority)) {
      authorityGeneration += 1;
      localRf = null;
      localSql = null;
      projectedCandidate = null;
      rfPresentation = { announcedTransitionIds: [] };
      sqlPresentation = { announcedTransitionIds: [] };
      scalar?.cancel('authority');
      handledTerminal = null;
    }
    lastAuthority = authority;
    for (const lane of ['rf', 'sql'] as const) {
      const local = localOf(lane);
      if (local !== null && !sameIdentity(local.observation, laneObservation(input, lane))) {
        setLocal(lane, null);
      }
    }
    const terminal = terminalOf(input);
    if (terminal !== null && !sameIdentity(handledTerminal, terminal)) {
      projectedCandidate = null;
      scalar?.cancel('terminal');
      handledTerminal = terminal;
    }
  }

  function current(): Readonly<ContinuousPairInput> {
    const input = read();
    reconcile(input);
    return input;
  }

  function pairEditable(input: Readonly<ContinuousPairInput>): boolean {
    if (!input.enabled || canonicalLane(input, 'rf') === null || canonicalLane(input, 'sql') === null
      || !laneAvailable(input, 'rf') || !laneAvailable(input, 'sql')) return false;
    return input.evidence === 'reading'
      || (validCommandLane(input.rf) && validCommandLane(input.sql));
  }

  function effectiveLane(input: Readonly<ContinuousPairInput>, lane: LaneName): number | null {
    if (input.evidence === 'command-feedback') {
      const target = finiteValue(input[lane].feedback.target);
      if (target !== null) return target;
    }
    return localOf(lane)?.value ?? canonicalLane(input, lane);
  }

  function effectivePair(input: Readonly<ContinuousPairInput>): ContinuousPairValues | null {
    const rf = effectiveLane(input, 'rf');
    const sql = effectiveLane(input, 'sql');
    return rf === null || sql === null ? null : { rf, sql };
  }

  function rememberCandidate(
    values: Readonly<ContinuousPairValues>,
    input: Readonly<ContinuousPairInput>,
    source: ProjectedCandidate['source'],
  ): number {
    const axis = dualParamNormXFromValues(
      values.rf, values.sql, input.domain.min, input.domain.max,
    );
    projectedCandidate = {
      axis, values: Object.freeze({ ...values }), authority: authorityGeneration, source,
    };
    return axis;
  }

  function requestAxis(axis: number): void {
    const input = current();
    const exact = projectedCandidate !== null
      && projectedCandidate.authority === authorityGeneration
      && projectedCandidate.source === scalar.view.interaction
      && Object.is(projectedCandidate.axis, axis)
      ? projectedCandidate.values : null;
    const values = exact ?? dualParamValuesFromNormX(
      axis, input.domain.min, input.domain.max, input.domain.step,
    );
    const changeRf = !Object.is(values.rf, effectiveLane(input, 'rf'));
    const changeSql = !Object.is(values.sql, effectiveLane(input, 'sql'));
    if (changeRf) {
      localRf = { value: values.rf, observation: laneObservation(input, 'rf') };
      input.requestRf(values.rf);
    }
    if (changeSql) {
      localSql = { value: values.sql, observation: laneObservation(input, 'sql') };
      input.requestSql(values.sql);
    }
  }

  const scalarPolicy: ContinuousScalarPolicy = {
    name: policy.name,
    preview: policy.preview,
    normalize: (value) => Number.isFinite(value) ? clamp(value, 0, 1) : null,
    wheel: (_axis, event) => {
      const input = current();
      const pair = effectivePair(input);
      const values = pair === null ? null : policy.wheel(pair, event, input.domain);
      return values === null ? null : rememberCandidate(values, input, 'wheel');
    },
    key: (_axis, event) => {
      const input = current();
      const pair = effectivePair(input);
      const values = pair === null ? null : policy.key(pair, event, input.domain);
      return values === null ? null : rememberCandidate(values, input, 'keyboard');
    },
    reset: () => {
      const input = current();
      const values = policy.reset(input.domain);
      return values === null ? null : rememberCandidate(values, input, 'reset');
    },
    dispatch: (source) => policy.dispatch(source),
    dispatchesCanonical: () => true,
    wheelIdleMs: policy.wheelIdleMs,
    describeTarget: String,
  };

  scalar = createContinuousScalar(() => {
    const input = current();
    const rf = canonicalLane(input, 'rf');
    const sql = canonicalLane(input, 'sql');
    return {
      evidence: 'reading', ownerKey: `continuous-pair:${authorityGeneration}`,
      domain: axisDomain(input.domain), enabled: pairEditable(input), request: requestAxis,
      reading: rf === null || sql === null
        ? { status: 'unknown' }
        : { status: 'known', value: dualParamNormXFromValues(
          rf, sql, input.domain.min, input.domain.max,
        ) },
    };
  }, scalarPolicy);

  function laneView(input: Readonly<ContinuousPairInput>, lane: LaneName): ContinuousPairLaneView {
    const canonical = canonicalLane(input, lane);
    const localRequested = localOf(lane)?.value ?? null;
    if (input.evidence === 'reading') {
      return Object.freeze({
        evidence: 'reading' as const, reading: input[lane].reading,
        availability: input[lane].availability, canonical, localRequested,
        feedback: null, phase: null, error: null, presentation: null, announcement: null,
      });
    }
    const feedback = input[lane].feedback;
    const prior = lane === 'rf' ? rfPresentation : sqlPresentation;
    const presentation = projectControlFeedbackPresentation(feedback, prior, String);
    if (lane === 'rf') rfPresentation = presentation.state;
    else sqlPresentation = presentation.state;
    return Object.freeze({
      evidence: 'command-feedback' as const, command: input[lane].command,
      feedback, availability: feedback.availability, canonical, localRequested,
      phase: feedback.phase, error: feedback.outcome?.error ?? null, presentation,
      announcement: presentation.politeAnnouncement?.message ?? null,
    });
  }

  function viewOf(): Readonly<ContinuousPairView> {
    const input = current();
    const axis = scalar.view;
    const rf = canonicalLane(input, 'rf');
    const sql = canonicalLane(input, 'sql');
    const position = axis.domainValid && rf !== null && sql !== null ? axis.canonical : null;
    const exactDraft = projectedCandidate !== null
      && projectedCandidate.authority === authorityGeneration
      && projectedCandidate.source === axis.interaction
      && axis.draft !== null && Object.is(projectedCandidate.axis, axis.draft)
      ? projectedCandidate.values : null;
    const draft = axis.draft === null ? null : exactDraft ?? Object.freeze(dualParamValuesFromNormX(
      axis.draft, input.domain.min, input.domain.max, input.domain.step,
    ));
    return Object.freeze({
      evidence: input.evidence,
      domain: snapshotDomain(input.domain), domainValid: axis.domainValid,
      canonical: Object.freeze({ rf, sql }), position, axisDraft: axis.draft,
      draft, displayedPosition: axis.domainValid ? axis.displayed : null,
      editable: axis.editable, busy: input.evidence === 'command-feedback'
        ? input.rf.feedback.busy || input.sql.feedback.busy : false,
      interaction: axis.interaction,
      lanes: Object.freeze({ rf: laneView(input, 'rf'), sql: laneView(input, 'sql') }),
    });
  }

  function wrapLease(lease: ContinuousScalarRendererLease): ContinuousPairRendererLease {
    return {
      get view() { void lease.view; return viewOf(); },
      beginPointer: () => lease.beginPointer(),
      pointer: (token, candidate) => lease.pointer(token, candidate),
      endPointer: (token) => lease.endPointer(token),
      cancelPointer: (token) => lease.cancelPointer(token),
      nativeInput: (candidate) => lease.nativeInput(candidate),
      wheel: (event) => lease.wheel(event),
      key: (event) => lease.key(event),
      reset: () => lease.reset(),
      dispose: () => lease.dispose(),
    };
  }

  return {
    get view() { return viewOf(); },
    attachRenderer: () => wrapLease(scalar.attachRenderer()),
    cancel: (reason) => scalar.cancel(reason),
    destroy: () => scalar.destroy(),
  };
}
