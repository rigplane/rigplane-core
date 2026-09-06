import {
  projectControlFeedbackPresentation,
  type ControlFeedbackPresentation,
  type ControlFeedbackPresentationInput,
  type ControlFeedbackPresentationState,
  type PresentationPhase,
} from '../control-feedback/control-feedback-presentation';
import { clamp, handleKeyboardStep, snapToStep } from './value-control-core';

export type ScalarPhase = PresentationPhase;

export interface ScalarDomain {
  readonly min: number;
  readonly max: number;
  readonly step: number;
  readonly defaultValue: number | null;
  readonly fineStepDivisor: number;
  readonly keyboardStep?: number;
}

export interface CommandScalarFeedback extends ControlFeedbackPresentationInput<number> {
  readonly busy: boolean;
  readonly availability: 'available' | 'unavailable';
  readonly lifecycleId: string | null;
  readonly sessionEpoch: number;
  readonly scope: Readonly<{ control: string; receiver: 0 | 1; slot?: string }>;
  readonly repeatPolicy: 'latest-target-wins';
}

export type ScalarReading =
  | Readonly<{ status: 'known'; value: number }>
  | Readonly<{ status: 'unknown' }>;

interface ContinuousScalarInputBase {
  readonly domain: ScalarDomain;
  readonly enabled: boolean;
  readonly request: (value: number) => void;
}

export interface CommandFeedbackScalarInput extends ContinuousScalarInputBase {
  readonly evidence: 'command-feedback';
  readonly feedback: Readonly<CommandScalarFeedback>;
  readonly command: string;
}

export interface ReadingScalarInput extends ContinuousScalarInputBase {
  readonly evidence: 'reading';
  readonly reading: ScalarReading;
  readonly ownerKey: string;
}

export type ContinuousScalarInput = CommandFeedbackScalarInput | ReadingScalarInput;
export type ScalarSource = 'native-input' | 'pointer' | 'wheel' | 'keyboard' | 'reset';
export type ScalarDispatchMode = 'immediate' | Readonly<{ debounceMs: number }>;
export interface ScalarStepInput { readonly direction: -1 | 1; readonly fine: boolean }
export interface ScalarKeyInput { readonly key: string; readonly fine: boolean }
export interface ScalarDispatchContext {
  readonly canonical: number;
  readonly interactionBase: number;
}

export interface ContinuousScalarPolicy {
  readonly name: string;
  readonly preview: 'optimistic' | 'confirmed';
  resolveKeyboardStep?(domain: ScalarDomain): number | undefined;
  normalize(value: number, domain: ScalarDomain): number | null;
  wheel(current: number, event: ScalarStepInput, domain: ScalarDomain): number | null;
  key(current: number, event: ScalarKeyInput, domain: ScalarDomain): number | null;
  reset(domain: ScalarDomain): number | null;
  dispatch(source: ScalarSource): ScalarDispatchMode;
  dispatchesCanonical(
    source: ScalarSource,
    context?: Readonly<ScalarDispatchContext>,
    domain?: ScalarDomain,
  ): boolean;
  readonly wheelIdleMs: 0 | 300;
  describeTarget(value: number): string;
}

export interface ContinuousScalarViewBase {
  readonly domain: Readonly<ScalarDomain>;
  readonly domainValid: boolean;
  readonly canonical: number | null;
  readonly draft: number | null;
  readonly displayed: number | null;
  readonly interactionBase: number | null;
  readonly editable: boolean;
  readonly busy: boolean;
  readonly interaction: 'idle' | ScalarSource;
}

export type ContinuousScalarView = Readonly<ContinuousScalarViewBase & (
  | Readonly<{
    evidence: 'command-feedback';
    feedback: Readonly<CommandScalarFeedback>;
    confirmed: number | null;
    target: number | null;
    requested: number | null;
    phase: ScalarPhase;
    error: string | null;
    presentation: Readonly<ControlFeedbackPresentation>;
    announcement: string | null;
  }>
  | Readonly<{
    evidence: 'reading';
    reading: ScalarReading;
    confirmed: null;
    target: null;
    requested: null;
    phase: null;
    error: null;
    presentation: null;
    announcement: null;
  }>
)>;

export interface ContinuousScalarRendererLease {
  readonly view: Readonly<ContinuousScalarView>;
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

export interface ContinuousScalarBinding {
  readonly view: Readonly<ContinuousScalarView>;
  attachRenderer(): ContinuousScalarRendererLease;
  cancel(reason: 'authority' | 'availability' | 'terminal' | 'owner-dispose'): void;
  destroy(): void;
}

const finite = (value: number | null | undefined): boolean =>
  value === null || value === undefined || Number.isFinite(value);

function validDomain(domain: ScalarDomain): boolean {
  return Number.isFinite(domain.min) && Number.isFinite(domain.max) && domain.max >= domain.min
    && Number.isFinite(domain.step) && domain.step > 0
    && Number.isFinite(domain.fineStepDivisor) && domain.fineStepDivisor > 0
    && finite(domain.defaultValue)
    && (domain.keyboardStep === undefined
      || (Number.isFinite(domain.keyboardStep) && domain.keyboardStep > 0));
}

function snap(value: number, domain: ScalarDomain, quantum: number): number | null {
  if (!validDomain(domain) || !Number.isFinite(value) || !Number.isFinite(quantum) || quantum <= 0) {
    return null;
  }
  return clamp(snapToStep(value, quantum, domain.min), domain.min, domain.max);
}

export function createHBarContinuousScalarPolicy(
  options: Readonly<{
    preview: 'optimistic' | 'confirmed';
    debounceMs: number;
    describeTarget?: (value: number) => string;
  }>,
): Readonly<ContinuousScalarPolicy> {
  const debounce = options.debounceMs > 0
    ? Object.freeze({ debounceMs: options.debounceMs })
    : 'immediate';
  const policy: ContinuousScalarPolicy = {
    name: `hbar-${options.preview}`,
    preview: options.preview,
    normalize: (value, domain) => snap(value, domain, domain.step / domain.fineStepDivisor),
    wheel: (current, event, domain) => {
      const quantum = event.fine
        ? domain.step / domain.fineStepDivisor
        : domain.step * 4 * Math.max(1, Math.ceil((domain.max - domain.min) / 255));
      return snap(current + event.direction * quantum, domain, quantum);
    },
    key: (current, event, domain) => handleKeyboardStep(
      current,
      event.key,
      domain.keyboardStep ?? domain.step,
      domain.fineStepDivisor,
      domain.min,
      domain.max,
      event.fine,
    ),
    reset: (domain) => domain.defaultValue ?? domain.min,
    dispatch: (source) => source === 'keyboard' || source === 'reset' ? debounce : 'immediate',
    dispatchesCanonical: (source) => source === 'wheel',
    wheelIdleMs: 300,
    describeTarget: options.describeTarget ?? String,
  };
  return Object.freeze(policy);
}

export function createDiscreteContinuousScalarPolicy(
  options: Readonly<{
    debounceMs: number;
    describeTarget?: (value: number) => string;
  }>,
): Readonly<ContinuousScalarPolicy> {
  const debounce = options.debounceMs > 0
    ? Object.freeze({ debounceMs: options.debounceMs })
    : 'immediate';
  const policy: ContinuousScalarPolicy = {
    name: 'discrete',
    preview: 'optimistic',
    resolveKeyboardStep: () => undefined,
    normalize: (value, domain) => Number.isFinite(value)
      ? clamp(value, domain.min, domain.max) : null,
    wheel: (current, event, domain) => {
      const quantum = event.fine ? domain.step / domain.fineStepDivisor : domain.step;
      return snap(current + event.direction * quantum, domain, quantum);
    },
    key: (current, event, domain) => handleKeyboardStep(
      current,
      event.key,
      domain.step,
      domain.fineStepDivisor,
      domain.min,
      domain.max,
      event.fine,
    ),
    reset: (domain) => domain.defaultValue,
    dispatch: (source) => source === 'keyboard' || source === 'reset' ? debounce : 'immediate',
    dispatchesCanonical: (source) => source === 'wheel',
    wheelIdleMs: 300,
    describeTarget: options.describeTarget ?? String,
  };
  return Object.freeze(policy);
}

export function createKnobContinuousScalarPolicy(
  options: Readonly<{
    debounceMs: number;
    describeTarget?: (value: number) => string;
  }>,
): Readonly<ContinuousScalarPolicy> {
  const debounce = options.debounceMs > 0
    ? Object.freeze({ debounceMs: options.debounceMs })
    : 'immediate';
  const policy: ContinuousScalarPolicy = {
    name: 'knob',
    preview: 'confirmed',
    resolveKeyboardStep: () => undefined,
    normalize: (value, domain) => snap(value, domain, domain.step / domain.fineStepDivisor),
    wheel: (current, event, domain) => {
      const quantum = event.fine ? domain.step / domain.fineStepDivisor : domain.step * 4;
      return snap(current + event.direction * quantum, domain, quantum);
    },
    key: (current, event, domain) => handleKeyboardStep(
      current,
      event.key,
      domain.step,
      domain.fineStepDivisor,
      domain.min,
      domain.max,
      event.fine,
    ),
    reset: (domain) => domain.defaultValue ?? domain.min,
    dispatch: (source) => source === 'keyboard' || source === 'reset' ? debounce : 'immediate',
    dispatchesCanonical: () => false,
    wheelIdleMs: 0,
    describeTarget: options.describeTarget ?? String,
  };
  return Object.freeze(policy);
}

function bipolarLatticeCompatible(increment: number, domain: ScalarDomain): boolean {
  return Number.isFinite(increment)
    && increment > 0
    && Number.isFinite(domain.step)
    && domain.step > 0
    && Number.isInteger(increment / domain.step);
}

function bipolarKeyboardStep(
  current: number,
  key: string,
  increment: number,
  domain: ScalarDomain,
): number | null {
  const center = domain.defaultValue ?? 0;
  switch (key) {
    case 'ArrowRight':
    case 'ArrowUp':
      return clamp(center + Math.round((current + increment - center) / increment) * increment,
        domain.min, domain.max);
    case 'ArrowLeft':
    case 'ArrowDown':
      return clamp(center + Math.round((current - increment - center) / increment) * increment,
        domain.min, domain.max);
    case 'Home':
      return domain.min;
    case 'End':
      return domain.max;
    default:
      return null;
  }
}

export function createBipolarContinuousScalarPolicy(
  options: Readonly<{
    debounceMs: number;
    describeTarget?: (value: number) => string;
  }>,
): Readonly<ContinuousScalarPolicy> {
  const debounce = options.debounceMs > 0
    ? Object.freeze({ debounceMs: options.debounceMs })
    : 'immediate';
  const policy: ContinuousScalarPolicy = {
    name: 'bipolar',
    preview: 'optimistic',
    resolveKeyboardStep: (domain) => domain.keyboardStep === undefined
      || bipolarLatticeCompatible(domain.keyboardStep, domain)
      || !Number.isFinite(domain.step) || domain.step <= 0
      ? domain.keyboardStep
      : domain.step,
    normalize: (value, domain) => Number.isFinite(value)
      ? clamp(value, domain.min, domain.max) : null,
    wheel: (current, event, domain) => {
      const stepsInRange = Math.max(1, (domain.max - domain.min) / domain.step);
      const multiplier = stepsInRange > 500 ? Math.round(stepsInRange / 240) : 1;
      const quantum = event.fine
        ? domain.step / domain.fineStepDivisor
        : domain.step * multiplier;
      return clamp(snapToStep(current + event.direction * quantum, quantum, domain.min),
        domain.min, domain.max);
    },
    key: (current, event, domain) => {
      if (domain.keyboardStep === undefined) {
        return handleKeyboardStep(
          current, event.key, domain.step, domain.fineStepDivisor,
          domain.min, domain.max, event.fine,
        );
      }
      const requested = event.fine
        ? domain.keyboardStep / domain.fineStepDivisor
        : domain.keyboardStep;
      const increment = bipolarLatticeCompatible(requested, domain) ? requested : domain.step;
      return bipolarKeyboardStep(current, event.key, increment, domain);
    },
    reset: (domain) => domain.defaultValue ?? 0,
    dispatch: (source) => source === 'keyboard' || source === 'reset' ? debounce : 'immediate',
    dispatchesCanonical: (source, context, domain) => source === 'wheel'
      || (source === 'keyboard' && domain?.keyboardStep !== undefined
        && context !== undefined
        && !Object.is(context.interactionBase, context.canonical)),
    wheelIdleMs: 300,
    describeTarget: options.describeTarget ?? String,
  };
  return Object.freeze(policy);
}

const nativeRangePolicy: ContinuousScalarPolicy = {
  name: 'native-range',
  preview: 'optimistic',
  normalize: (value, domain) => snap(value, domain, domain.step),
  wheel: () => null,
  key: () => null,
  reset: (domain) => domain.defaultValue ?? domain.min,
  dispatch: () => 'immediate',
  dispatchesCanonical: () => true,
  wheelIdleMs: 0,
  describeTarget: String,
};
export const nativeRangeContinuousScalarPolicy: Readonly<ContinuousScalarPolicy> =
  Object.freeze(nativeRangePolicy);

type AuthorityIdentity = readonly (string | number | boolean | null | undefined)[];

function authorityOf(input: Readonly<ContinuousScalarInput>): AuthorityIdentity {
  const domain = input.domain;
  const shared = [
    input.evidence, input.enabled, domain.min, domain.max, domain.step,
    domain.defaultValue, domain.fineStepDivisor, domain.keyboardStep,
  ] as const;
  if (input.evidence === 'command-feedback') {
    return Object.freeze([
      ...shared, input.command, input.feedback.sessionEpoch, input.feedback.availability,
      input.feedback.scope.control, input.feedback.scope.receiver, input.feedback.scope.slot,
    ]);
  }
  return Object.freeze([...shared, input.ownerKey, input.reading.status]);
}

function sameAuthority(left: AuthorityIdentity | null, right: AuthorityIdentity): boolean {
  return left !== null && left.length === right.length
    && left.every((value, index) => Object.is(value, right[index]));
}

function canonicalOf(input: Readonly<ContinuousScalarInput>): number | null {
  if (input.evidence === 'reading') {
    return input.reading.status === 'known' && Number.isFinite(input.reading.value)
      ? input.reading.value : null;
  }
  const value = input.feedback.confirmed;
  return input.feedback.availability === 'available' && value !== null && Number.isFinite(value)
    ? value : null;
}

function validCommandFeedback(feedback: Readonly<CommandScalarFeedback>): boolean {
  return finite(feedback.target) && finite(feedback.requestedTarget)
    && Number.isFinite(feedback.sessionEpoch)
    && (feedback.scope.receiver === 0 || feedback.scope.receiver === 1);
}

function editable(input: Readonly<ContinuousScalarInput>): boolean {
  if (!input.enabled || !validDomain(input.domain) || canonicalOf(input) === null) return false;
  return input.evidence === 'reading' || validCommandFeedback(input.feedback);
}

function snapshotDomain(domain: ScalarDomain): Readonly<ScalarDomain> {
  const common = {
    min: domain.min,
    max: domain.max,
    step: domain.step,
    defaultValue: domain.defaultValue,
    fineStepDivisor: domain.fineStepDivisor,
  };
  return Object.freeze(domain.keyboardStep === undefined
    ? common
    : { ...common, keyboardStep: domain.keyboardStep });
}

const TERMINAL_FAILURES: ReadonlySet<ScalarPhase> = new Set([
  'failed', 'timed-out', 'cancelled', 'superseded',
]);

function terminalIdentity(input: Readonly<ContinuousScalarInput>): AuthorityIdentity | null {
  if (input.evidence === 'reading') return null;
  const outcomePhase = input.feedback.outcome?.phase;
  const phase = outcomePhase !== undefined && TERMINAL_FAILURES.has(outcomePhase)
    ? outcomePhase
    : TERMINAL_FAILURES.has(input.feedback.phase) ? input.feedback.phase : null;
  return phase === null ? null : Object.freeze([
    ...authorityOf(input), input.feedback.lifecycleId ?? input.feedback.transitionId, phase,
  ]);
}

export function createContinuousScalar(
  read: () => Readonly<ContinuousScalarInput>,
  policy: Readonly<ContinuousScalarPolicy>,
): ContinuousScalarBinding {
  let draft = $state<number | null>(null);
  let interaction = $state<'idle' | ScalarSource>('idle');
  let draftCanonical: number | null = null;
  let lastAuthority: AuthorityIdentity | null = null;
  let handledTerminal: AuthorityIdentity | null = null;
  let activeRenderer: number | null = null;
  let rendererSequence = 0;
  let gestureSequence = 0;
  let activeGesture: { renderer: number; token: number } | null = null;
  let debounceTimer: ReturnType<typeof setTimeout> | null = null;
  let wheelTimer: ReturnType<typeof setTimeout> | null = null;
  let invalidationGeneration = 0;
  let presentationState: Readonly<ControlFeedbackPresentationState> = {
    announcedTransitionIds: [],
  };
  let destroyed = false;

  function clearTimers(): void {
    if (debounceTimer !== null) clearTimeout(debounceTimer);
    if (wheelTimer !== null) clearTimeout(wheelTimer);
    debounceTimer = null;
    wheelTimer = null;
  }

  function clearTransient(): void {
    invalidationGeneration += 1;
    clearTimers();
    draft = null;
    draftCanonical = null;
    interaction = 'idle';
    activeGesture = null;
  }

  function reconcile(input: Readonly<ContinuousScalarInput>): void {
    const authority = authorityOf(input);
    if (lastAuthority !== null && !sameAuthority(lastAuthority, authority)) {
      clearTransient();
      handledTerminal = null;
      presentationState = { announcedTransitionIds: [] };
    }
    lastAuthority = authority;
    const terminal = terminalIdentity(input);
    if (terminal !== null && !sameAuthority(handledTerminal, terminal)) {
      clearTransient();
      handledTerminal = terminal;
    } else if (input.evidence === 'command-feedback') {
      if (draft !== null && Object.is(canonicalOf(input), draft)) {
        draft = null;
        draftCanonical = null;
        if (interaction !== 'pointer' && interaction !== 'wheel') interaction = 'idle';
      }
    } else if (draft !== null && interaction !== 'pointer' && interaction !== 'wheel'
      && !Object.is(canonicalOf(input), draftCanonical)) {
      draft = null;
      draftCanonical = null;
      interaction = 'idle';
    }
  }

  function current(): Readonly<ContinuousScalarInput> {
    const source = read();
    const keyboardStep = policy.resolveKeyboardStep === undefined
      ? source.domain.keyboardStep
      : policy.resolveKeyboardStep(source.domain);
    const domain = Object.is(keyboardStep, source.domain.keyboardStep)
      ? source.domain
      : { ...source.domain, keyboardStep };
    const input = domain === source.domain ? source : { ...source, domain };
    reconcile(input);
    return input;
  }

  function dispatch(candidate: number, authority: AuthorityIdentity, generation: number): void {
    const input = current();
    if (!destroyed && generation === invalidationGeneration
      && sameAuthority(authority, authorityOf(input)) && editable(input)) {
      input.request(candidate);
    }
  }

  function applyCandidate(
    renderer: number,
    source: ScalarSource,
    candidate: number | null,
  ): boolean {
    const input = current();
    if (destroyed || renderer !== activeRenderer || !editable(input) || candidate === null) return false;
    const normalized = policy.normalize(candidate, input.domain);
    if (normalized === null || !Number.isFinite(normalized)
      || normalized < input.domain.min || normalized > input.domain.max) return false;
    const authority = authorityOf(input);
    const generation = invalidationGeneration;
    const canonical = canonicalOf(input);
    const base = interactionBase(input);
    if (canonical === null || base === null) return false;
    draft = normalized;
    draftCanonical = canonical;
    interaction = source;
    if (!policy.dispatchesCanonical(source, {
      canonical,
      interactionBase: base,
    }, input.domain) && Object.is(normalized, draftCanonical)) {
      draft = null;
      draftCanonical = null;
      if (source !== 'pointer') interaction = 'idle';
      return true;
    }
    const mode = policy.dispatch(source);
    if (mode === 'immediate') {
      dispatch(normalized, authority, generation);
    } else {
      if (debounceTimer !== null) clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        debounceTimer = null;
        if (renderer === activeRenderer) dispatch(normalized, authority, generation);
      }, mode.debounceMs);
    }
    return true;
  }

  function interactionBase(input: Readonly<ContinuousScalarInput>): number | null {
    return policy.preview === 'optimistic' && draft !== null ? draft : canonicalOf(input);
  }

  function viewOf(input: Readonly<ContinuousScalarInput>): Readonly<ContinuousScalarView> {
    const canonical = canonicalOf(input);
    const displayed = policy.preview === 'optimistic' && draft !== null ? draft : canonical;
    const common = {
      domain: snapshotDomain(input.domain),
      domainValid: validDomain(input.domain),
      canonical,
      draft,
      displayed,
      interactionBase: interactionBase(input),
      editable: editable(input),
      busy: input.evidence === 'command-feedback' ? input.feedback.busy : false,
      interaction,
    } as const;
    if (input.evidence === 'reading') {
      return Object.freeze({
        ...common,
        evidence: 'reading' as const,
        reading: input.reading,
        confirmed: null,
        target: null,
        requested: null,
        phase: null,
        error: null,
        presentation: null,
        announcement: null,
      });
    }
    const presentation = projectControlFeedbackPresentation(
      input.feedback, presentationState, policy.describeTarget,
    );
    presentationState = presentation.state;
    return Object.freeze({
      ...common,
      evidence: 'command-feedback' as const,
      feedback: input.feedback,
      confirmed: canonical,
      target: input.feedback.target,
      requested: input.feedback.requestedTarget,
      phase: input.feedback.phase,
      error: input.feedback.outcome?.error ?? null,
      presentation,
      announcement: presentation.politeAnnouncement?.message ?? null,
    });
  }

  function makeLease(renderer: number): ContinuousScalarRendererLease {
    return {
      get view() { return viewOf(current()); },
      beginPointer(): number | null {
        const input = current();
        if (destroyed || renderer !== activeRenderer || !editable(input)) return null;
        const token = ++gestureSequence;
        activeGesture = { renderer, token };
        interaction = 'pointer';
        return token;
      },
      pointer(token: number, candidate: number): void {
        current();
        if (activeGesture?.renderer === renderer && activeGesture.token === token) {
          applyCandidate(renderer, 'pointer', candidate);
        }
      },
      endPointer(token: number): void {
        current();
        if (activeGesture?.renderer !== renderer || activeGesture.token !== token) return;
        activeGesture = null;
        interaction = 'idle';
        current();
      },
      cancelPointer(token: number): void {
        current();
        if (activeGesture?.renderer === renderer && activeGesture.token === token) clearTransient();
      },
      nativeInput(candidate: number): void {
        applyCandidate(renderer, 'native-input', candidate);
      },
      wheel(event: ScalarStepInput): void {
        const input = current();
        if (destroyed || renderer !== activeRenderer || !editable(input)) return;
        const base = interactionBase(input);
        if (base === null || !applyCandidate(renderer, 'wheel', policy.wheel(base, event, input.domain))) return;
        if (policy.wheelIdleMs === 300) {
          if (wheelTimer !== null) clearTimeout(wheelTimer);
          const authority = authorityOf(input);
          wheelTimer = setTimeout(() => {
            wheelTimer = null;
            const latest = current();
            if (renderer === activeRenderer && sameAuthority(authority, authorityOf(latest))) {
              draft = null;
              draftCanonical = null;
              interaction = 'idle';
            }
          }, policy.wheelIdleMs);
        }
      },
      key(event: ScalarKeyInput): boolean {
        const input = current();
        if (destroyed || renderer !== activeRenderer || !editable(input)) return false;
        const base = interactionBase(input);
        if (base === null) return false;
        const candidate = policy.key(base, event, input.domain);
        if (candidate === null) return false;
        return applyCandidate(renderer, 'keyboard', candidate);
      },
      reset(): void {
        const input = current();
        if (!destroyed && renderer === activeRenderer && editable(input))
          applyCandidate(renderer, 'reset', policy.reset(input.domain));
      },
      dispose(): void {
        if (renderer !== activeRenderer) return;
        clearTransient();
        activeRenderer = null;
      },
    };
  }

  return {
    get view() { return viewOf(current()); },
    attachRenderer(): ContinuousScalarRendererLease {
      if (!destroyed && activeRenderer !== null) clearTransient();
      const renderer = ++rendererSequence;
      if (!destroyed) activeRenderer = renderer;
      return makeLease(renderer);
    },
    cancel(): void { clearTransient(); },
    destroy(): void {
      if (destroyed) return;
      destroyed = true;
      clearTransient();
      activeRenderer = null;
    },
  };
}
