import { describe, expect, it, vi } from 'vitest';
import type { ControlFeedback } from '$lib/runtime/adapters/panel-adapters';
import {
  createBipolarContinuousScalarPolicy,
  createContinuousScalar,
  createDiscreteContinuousScalarPolicy,
  createHBarContinuousScalarPolicy,
  createKnobContinuousScalarPolicy,
  nativeRangeContinuousScalarPolicy,
  type CommandScalarFeedback,
  type ContinuousScalarInput,
  type ReadingScalarInput,
  type ScalarDomain,
} from '../continuous-scalar.svelte';

const ACTUAL_CONTRACT_FITS: ControlFeedback<number> extends CommandScalarFeedback ? true : false = true;

const DOMAIN: ScalarDomain = {
  min: 0,
  max: 5_000,
  step: 100,
  defaultValue: 2_400,
  fineStepDivisor: 10,
};
const NORMALIZED_DOMAIN: ScalarDomain = {
  min: 0,
  max: 1,
  step: 0.01,
  defaultValue: null,
  fineStepDivisor: 10,
};

const feedback = (
  phase: CommandScalarFeedback['phase'] = 'idle',
  over: Partial<CommandScalarFeedback> = {},
): Readonly<CommandScalarFeedback> => ({
  confirmed: 2_400,
  target: null,
  requestedTarget: null,
  phase,
  busy: ['submitted', 'queued', 'dispatched', 'awaiting-confirmation'].includes(phase),
  availability: phase === 'unavailable' ? 'unavailable' : 'available',
  outcome: null,
  lifecycleId: null,
  transitionId: null,
  providerGeneration: 3,
  sessionEpoch: 7,
  scope: { control: 'filter-width', receiver: 0, slot: 'main' },
  repeatPolicy: 'latest-target-wins',
  ...over,
});

const commandInput = (
  request: (value: number) => void,
  over: Partial<Omit<Extract<ContinuousScalarInput, { evidence: 'command-feedback' }>, 'evidence'>> = {},
): Extract<ContinuousScalarInput, { evidence: 'command-feedback' }> => ({
  evidence: 'command-feedback',
  domain: DOMAIN,
  enabled: true,
  request,
  feedback: feedback(),
  command: 'set_filter_width',
  ...over,
});

const readingInput = (
  request: (value: number) => void,
  over: Partial<Omit<ReadingScalarInput, 'evidence'>> = {},
): ReadingScalarInput => ({
  evidence: 'reading',
  domain: DOMAIN,
  enabled: true,
  request,
  reading: { status: 'known', value: 2_400 },
  ownerKey: 'filter-width:main',
  ...over,
});

function commandSetup(
  policy = createHBarContinuousScalarPolicy({ preview: 'optimistic', debounceMs: 50 }),
  initial?: Partial<Omit<Extract<ContinuousScalarInput, { evidence: 'command-feedback' }>, 'evidence'>>,
) {
  const request = vi.fn<(value: number) => void>();
  let current = commandInput(request, initial);
  const scalar = createContinuousScalar(() => current, policy);
  return {
    scalar,
    request,
    read: () => current,
    update: (
      over: Partial<Omit<Extract<ContinuousScalarInput, { evidence: 'command-feedback' }>, 'evidence'>>,
    ) => { current = { ...current, ...over }; },
  };
}

function readingSetup(
  policy = createHBarContinuousScalarPolicy({ preview: 'optimistic', debounceMs: 50 }),
  initial?: Partial<Omit<ReadingScalarInput, 'evidence'>>,
) {
  const request = vi.fn<(value: number) => void>();
  let current = readingInput(request, initial);
  const scalar = createContinuousScalar(() => current, policy);
  return {
    scalar,
    request,
    read: () => current,
    update: (over: Partial<Omit<ReadingScalarInput, 'evidence'>>) => {
      current = { ...current, ...over };
    },
  };
}

describe('continuous scalar contract', () => {
  it('publishes a frozen exact domain snapshot without erasing known invalid-domain truth', () => {
    const invalidDomain: ScalarDomain = {
      min: 0,
      max: Number.NaN,
      step: 100,
      defaultValue: 2_400,
      fineStepDivisor: 10,
      keyboardStep: 50,
    };
    const { scalar, request } = readingSetup(undefined, { domain: invalidDomain });
    const view = scalar.view;

    expect(view).toMatchObject({
      canonical: 2_400,
      displayed: 2_400,
      editable: false,
      domainValid: false,
      domain: invalidDomain,
    });
    expect(view.domain).not.toBe(invalidDomain);
    expect(Object.isFrozen(view.domain)).toBe(true);
    scalar.attachRenderer().nativeInput(2_500);
    expect(request).not.toHaveBeenCalled();
  });

  it('keeps command truth and presentation while an invalid domain disables behavior', () => {
    const failed = feedback('failed', {
      target: 2_700,
      requestedTarget: 2_700,
      transitionId: 'failed-invalid-domain',
      outcome: { phase: 'failed', error: 'radio rejected' },
    });
    const { scalar } = commandSetup(undefined, {
      domain: { ...DOMAIN, step: 0 },
      feedback: failed,
    });

    expect(scalar.view).toMatchObject({
      canonical: 2_400,
      displayed: 2_400,
      domainValid: false,
      editable: false,
      phase: 'failed',
      error: 'radio rejected',
      presentation: { targetDescription: '2700' },
    });
  });

  it('keeps reading evidence honest for known, unknown, and non-finite values', () => {
    const { scalar, request, update } = readingSetup();
    const lease = scalar.attachRenderer();
    expect(scalar.view).toMatchObject({
      evidence: 'reading',
      canonical: 2_400,
      displayed: 2_400,
      confirmed: null,
      target: null,
      requested: null,
      phase: null,
      presentation: null,
      announcement: null,
      editable: true,
    });

    update({ reading: { status: 'unknown' } });
    expect(scalar.view).toMatchObject({ canonical: null, displayed: null, editable: false });
    lease.nativeInput(1_500);
    expect(request).not.toHaveBeenCalled();

    update({ reading: { status: 'known', value: Number.NaN } });
    expect(scalar.view).toMatchObject({ canonical: null, displayed: null, editable: false });
    update({ enabled: false, reading: { status: 'known', value: 2_400 } });
    lease.nativeInput(1_600);
    expect(request).not.toHaveBeenCalled();
  });

  it('preserves the full command envelope while deriving fail-closed scalar fields', () => {
    expect(ACTUAL_CONTRACT_FITS).toBe(true);
    const pending = feedback('awaiting-confirmation', {
      confirmed: 2_400,
      target: 2_800,
      requestedTarget: 2_800,
      lifecycleId: 'life-1',
      transitionId: 'transition-1',
    });
    const { scalar, update } = commandSetup(undefined, { feedback: pending });
    expect(scalar.view).toMatchObject({
      evidence: 'command-feedback',
      feedback: pending,
      canonical: 2_400,
      confirmed: 2_400,
      target: 2_800,
      requested: 2_800,
      phase: 'awaiting-confirmation',
      busy: true,
      presentation: { attributes: { 'aria-busy': 'true' } },
      announcement: 'Awaiting confirmation: 2800',
    });
    expect(scalar.view.announcement).toBeNull();

    const malformed = feedback('submitted', {
      confirmed: Number.POSITIVE_INFINITY,
      target: 2_900,
      requestedTarget: 2_900,
      transitionId: 'transition-2',
    });
    update({ feedback: malformed });
    const malformedView = scalar.view;
    expect(malformedView.evidence).toBe('command-feedback');
    if (malformedView.evidence === 'command-feedback') expect(malformedView.feedback).toBe(malformed);
    expect(malformedView).toMatchObject({ canonical: null, displayed: null, editable: false });
  });
});

describe('continuous scalar source policies', () => {
  it('keeps Discrete ordinary and fine wheel increments distinct from HBar', () => {
    const policy = createDiscreteContinuousScalarPolicy({ debounceMs: 0 });
    const domain = { min: 0, max: 10, step: 2, defaultValue: null, fineStepDivisor: 4 };

    expect(policy.wheel(4, { direction: 1, fine: false }, domain)).toBe(6);
    expect(policy.wheel(4, { direction: -1, fine: true }, domain)).toBe(3.5);
    expect(createHBarContinuousScalarPolicy({ preview: 'optimistic', debounceMs: 0 })
      .wheel(4, { direction: 1, fine: false }, domain)).toBe(10);
  });

  it('ignores Discrete keyboardStep while preserving declared-step keyboard behavior', () => {
    const policy = createDiscreteContinuousScalarPolicy({ debounceMs: 0 });
    const { scalar, request } = readingSetup(policy, {
      domain: { min: 0, max: 10, step: 2, defaultValue: null, fineStepDivisor: 4, keyboardStep: Infinity },
      reading: { status: 'known', value: 4 },
    });

    expect(scalar.view.domainValid).toBe(true);
    expect('keyboardStep' in scalar.view.domain).toBe(false);
    scalar.attachRenderer().key({ key: 'ArrowRight', fine: false });
    expect(request).toHaveBeenCalledExactlyOnceWith(6);
  });

  it('keeps absent Discrete reset inert while HBar resets to min', () => {
    const domain = { min: 0, max: 10, step: 1, defaultValue: null, fineStepDivisor: 10 };
    const discrete = readingSetup(createDiscreteContinuousScalarPolicy({ debounceMs: 0 }), {
      domain, reading: { status: 'known', value: 5 },
    });
    const hbar = readingSetup(createHBarContinuousScalarPolicy({ preview: 'optimistic', debounceMs: 0 }), {
      domain, reading: { status: 'known', value: 5 },
    });

    discrete.scalar.attachRenderer().reset();
    hbar.scalar.attachRenderer().reset();
    expect(discrete.request).not.toHaveBeenCalled();
    expect(hbar.request).toHaveBeenCalledExactlyOnceWith(0);
  });

  it('dispatches a Discrete wheel at the canonical boundary', () => {
    const policy = createDiscreteContinuousScalarPolicy({ debounceMs: 0 });
    const { scalar, request } = readingSetup(policy, {
      domain: { min: 0, max: 10, step: 1, defaultValue: null, fineStepDivisor: 10 },
      reading: { status: 'known', value: 10 },
    });

    scalar.attachRenderer().wheel({ direction: 1, fine: false });
    expect(request).toHaveBeenCalledExactlyOnceWith(10);
  });

  it('keeps Knob input anchored to confirmed value and ignores keyboardStep', () => {
    const policy = createKnobContinuousScalarPolicy({ debounceMs: 0 });
    const { scalar, request } = readingSetup(policy, {
      domain: {
        min: 0, max: 100, step: 10, defaultValue: null,
        fineStepDivisor: 10, keyboardStep: Number.POSITIVE_INFINITY,
      },
      reading: { status: 'known', value: 50 },
    });
    const lease = scalar.attachRenderer();

    expect(scalar.view).toMatchObject({ domainValid: true, displayed: 50 });
    expect('keyboardStep' in scalar.view.domain).toBe(false);
    lease.wheel({ direction: 1, fine: false });
    lease.wheel({ direction: 1, fine: false });
    lease.key({ key: 'ArrowRight', fine: false });
    lease.key({ key: 'ArrowRight', fine: false });

    expect(request.mock.calls).toEqual([[80], [80], [60], [60]]);
    expect(scalar.view).toMatchObject({ canonical: 50, displayed: 50 });
  });

  it('clears zero-idle Knob wheel state when the incoming value confirms the request', () => {
    const policy = createKnobContinuousScalarPolicy({ debounceMs: 0 });
    const { scalar, request, update } = readingSetup(policy, {
      domain: { min: 0, max: 100, step: 10, defaultValue: null, fineStepDivisor: 10 },
      reading: { status: 'known', value: 50 },
    });
    const lease = scalar.attachRenderer();

    lease.wheel({ direction: 1, fine: false });
    expect(request).toHaveBeenCalledExactlyOnceWith(80);
    update({ reading: { status: 'known', value: 80 } });

    expect(scalar.view).toMatchObject({
      canonical: 80,
      displayed: 80,
      draft: null,
      interaction: 'idle',
    });
  });

  it('keeps Knob dispatch timing, fine increments, reset, and canonical no-ops explicit', () => {
    vi.useFakeTimers();
    const policy = createKnobContinuousScalarPolicy({ debounceMs: 50 });
    const { scalar, request } = readingSetup(policy, {
      domain: { min: 0, max: 100, step: 10, defaultValue: null, fineStepDivisor: 10 },
      reading: { status: 'known', value: 50 },
    });
    const lease = scalar.attachRenderer();
    const token = lease.beginPointer()!;

    lease.pointer(token, 51);
    lease.wheel({ direction: 1, fine: true });
    expect(request.mock.calls).toEqual([[51], [51]]);
    lease.pointer(token, 50);
    expect(request).toHaveBeenCalledTimes(2);

    lease.key({ key: 'ArrowRight', fine: false });
    vi.advanceTimersByTime(49);
    expect(request).toHaveBeenCalledTimes(2);
    vi.advanceTimersByTime(1);
    expect(request).toHaveBeenLastCalledWith(60);

    lease.reset();
    vi.advanceTimersByTime(50);
    expect(request).toHaveBeenLastCalledWith(0);
    expect(request).toHaveBeenCalledTimes(4);

    const explicit = readingSetup(policy, {
      domain: { min: 0, max: 100, step: 10, defaultValue: 80, fineStepDivisor: 10 },
      reading: { status: 'known', value: 50 },
    });
    explicit.scalar.attachRenderer().reset();
    vi.advanceTimersByTime(50);
    expect(explicit.request).toHaveBeenCalledExactlyOnceWith(80);
    vi.useRealTimers();
  });

  it.each([0, -50, Number.NaN, 2.5, Number.POSITIVE_INFINITY])(
    'adapts invalid Bipolar keyboard hint %s without weakening the radio domain',
    (keyboardStep) => {
      const policy = createBipolarContinuousScalarPolicy({ debounceMs: 0 });
      const { scalar, request } = readingSetup(policy, {
        domain: {
          min: -100,
          max: 100,
          step: 5,
          defaultValue: 0,
          fineStepDivisor: 10,
          keyboardStep,
        },
        reading: { status: 'known', value: 0 },
      });

      expect(scalar.view).toMatchObject({
        domainValid: true,
        editable: true,
        domain: { min: -100, max: 100, step: 5, keyboardStep: 5 },
      });
      scalar.attachRenderer().key({ key: 'ArrowRight', fine: false });
      expect(request).toHaveBeenCalledExactlyOnceWith(5);
    },
  );

  it('dispatches a center-anchored draft return but suppresses an untouched boundary', () => {
    vi.useFakeTimers();
    const policy = createBipolarContinuousScalarPolicy({ debounceMs: 50 });
    const { scalar, request } = readingSetup(policy, {
      domain: {
        min: -100,
        max: 100,
        step: 5,
        defaultValue: 0,
        fineStepDivisor: 10,
        keyboardStep: 50,
      },
      reading: { status: 'known', value: 0 },
    });
    const lease = scalar.attachRenderer();

    lease.key({ key: 'ArrowRight', fine: false });
    vi.advanceTimersByTime(50);
    lease.key({ key: 'ArrowLeft', fine: false });
    vi.advanceTimersByTime(50);
    expect(request.mock.calls).toEqual([[50], [0]]);

    const atBoundary = readingSetup(policy, {
      domain: {
        min: -100,
        max: 100,
        step: 5,
        defaultValue: 0,
        fineStepDivisor: 10,
        keyboardStep: 50,
      },
      reading: { status: 'known', value: 100 },
    });
    expect(atBoundary.scalar.attachRenderer().key({ key: 'ArrowRight', fine: false })).toBe(true);
    vi.advanceTimersByTime(50);
    expect(atBoundary.request).not.toHaveBeenCalled();
    vi.useRealTimers();
  });

  it('keeps Bipolar wheel scaling and fine stepping explicit', () => {
    const policy = createBipolarContinuousScalarPolicy({ debounceMs: 0 });
    const compact = { min: -100, max: 100, step: 5, defaultValue: 0, fineStepDivisor: 10 };
    const large = { min: -9_999, max: 9_999, step: 1, defaultValue: 0, fineStepDivisor: 10 };

    expect(policy.wheel(0, { direction: 1, fine: false }, compact)).toBe(5);
    expect(policy.wheel(0, { direction: 1, fine: false }, large)).toBe(44);
    expect(policy.wheel(0, { direction: 1, fine: true }, compact)).toBe(0.5);
  });

  it('makes native input immediate and tokenless without wheel or key reinterpretation', () => {
    const { scalar, request } = readingSetup(nativeRangeContinuousScalarPolicy, {
      reading: { status: 'known', value: 2_450 },
    });
    const lease = scalar.attachRenderer();
    lease.nativeInput(2_561);
    expect(request).toHaveBeenCalledExactlyOnceWith(2_600);
    expect(scalar.view).toMatchObject({
      interaction: 'native-input', canonical: 2_450, draft: 2_600, displayed: 2_600,
    });
    expect(lease.key({ key: 'ArrowRight', fine: false })).toBe(false);
    lease.wheel({ direction: 1, fine: false });
    expect(request).toHaveBeenCalledTimes(1);

    const ambiguous = readingSetup(nativeRangeContinuousScalarPolicy, {
      domain: {
        min: 1_000_000_000_000, max: 1_000_000_000_001, step: 0.0005,
        defaultValue: null, fineStepDivisor: 10,
      },
      reading: { status: 'known', value: 1_000_000_000_000 },
    });
    ambiguous.scalar.attachRenderer().nativeInput(1_000_000_000_000.0004);
    expect(ambiguous.request).toHaveBeenCalledExactlyOnceWith(1_000_000_000_000.0005);
  });

  it.each([
    ['exact decimal lattice point', NORMALIZED_DOMAIN, 0.7, 0.7],
    ['shifted-minimum lattice point', { ...NORMALIZED_DOMAIN, min: 0.2 }, 0.3, 0.3],
    ['negative lattice point', { ...NORMALIZED_DOMAIN, min: -1 }, -0.3, -0.3],
    ['positive half-step tie', NORMALIZED_DOMAIN, 0.705, 0.71],
    ['negative half-step tie', { ...DOMAIN, min: -1_000, max: 1_000 }, -150, -100],
    ['lower bound clamp', NORMALIZED_DOMAIN, -0.1, 0],
    ['upper bound clamp', NORMALIZED_DOMAIN, 1.1, 1],
    ['non-finite rejection', NORMALIZED_DOMAIN, Number.NaN, null],
  ] as const)('normalizes native input with stable %s behavior', (_name, domain, candidate, expected) => {
    const { scalar, request } = readingSetup(nativeRangeContinuousScalarPolicy, {
      domain,
      reading: { status: 'known', value: domain.min },
    });
    scalar.attachRenderer().nativeInput(candidate);
    if (expected === null) expect(request).not.toHaveBeenCalled();
    else expect(request).toHaveBeenCalledExactlyOnceWith(expected);
  });

  it('keeps optimistic and confirmed HBar display/base policies explicit', () => {
    const optimisticPolicy = createHBarContinuousScalarPolicy({ preview: 'optimistic', debounceMs: 0 });
    expect(optimisticPolicy.wheel(1_000, { direction: 1, fine: false }, DOMAIN)).toBe(5_000);
    const optimistic = readingSetup(optimisticPolicy);
    const confirmed = readingSetup(
      createHBarContinuousScalarPolicy({ preview: 'confirmed', debounceMs: 0 }),
    );
    const optimisticLease = optimistic.scalar.attachRenderer();
    const confirmedLease = confirmed.scalar.attachRenderer();

    optimisticLease.wheel({ direction: 1, fine: true });
    confirmedLease.wheel({ direction: 1, fine: true });
    expect(optimistic.request).toHaveBeenCalledWith(2_410);
    expect(confirmed.request).toHaveBeenCalledWith(2_410);
    expect(optimistic.scalar.view.displayed).toBe(2_410);
    expect(optimistic.scalar.view.interactionBase).toBe(2_410);
    expect(confirmed.scalar.view.displayed).toBe(2_400);
    expect(confirmed.scalar.view.interactionBase).toBe(2_400);

    optimisticLease.key({ key: 'ArrowRight', fine: true });
    confirmedLease.key({ key: 'ArrowRight', fine: true });
    expect(optimistic.request).toHaveBeenLastCalledWith(2_420);
    expect(confirmed.request).toHaveBeenLastCalledWith(2_410);
  });

  it('suppresses HBar pointer, key, and reset at canonical but still sends boundary wheel', () => {
    const policy = createHBarContinuousScalarPolicy({ preview: 'optimistic', debounceMs: 0 });
    const atMax = readingSetup(policy, {
      domain: { ...DOMAIN, defaultValue: 5_000 },
      reading: { status: 'known', value: 5_000 },
    });
    const lease = atMax.scalar.attachRenderer();
    const token = lease.beginPointer()!;
    lease.pointer(token, 5_000);
    expect(lease.key({ key: 'ArrowRight', fine: false })).toBe(true);
    lease.reset();
    expect(atMax.request).not.toHaveBeenCalled();
    lease.wheel({ direction: 1, fine: false });
    expect(atMax.request).toHaveBeenCalledExactlyOnceWith(5_000);
    lease.dispose();

    const pending = commandSetup(policy, { feedback: feedback('submitted', {
      target: 2_700, requestedTarget: 2_700, lifecycleId: 'pending-target',
    }) });
    const pendingLease = pending.scalar.attachRenderer();
    const pendingToken = pendingLease.beginPointer()!;
    pendingLease.pointer(pendingToken, 2_700);
    pendingLease.pointer(pendingToken, 2_700);
    expect(pending.request.mock.calls).toEqual([[2_700], [2_700]]);
    pendingLease.dispose();
  });

  it('uses caller debounce for HBar keys and reset but not pointer or wheel', () => {
    vi.useFakeTimers();
    const { scalar, request } = commandSetup();
    const lease = scalar.attachRenderer();
    const token = lease.beginPointer();
    expect(token).not.toBeNull();
    lease.pointer(token!, 2_700);
    lease.wheel({ direction: 1, fine: true });
    expect(request.mock.calls).toEqual([[2_700], [2_710]]);

    expect(lease.key({ key: 'ArrowRight', fine: false })).toBe(true);
    expect(lease.key({ key: 'PageDown', fine: false })).toBe(false);
    lease.reset();
    expect(request).toHaveBeenCalledTimes(2);
    vi.advanceTimersByTime(49);
    expect(request).toHaveBeenCalledTimes(2);
    vi.advanceTimersByTime(1);
    expect(request).toHaveBeenLastCalledWith(2_800);
    vi.useRealTimers();
  });
});

describe('continuous scalar authority and feedback reconciliation', () => {
  it('does not let an earlier native request abort a live drag on new pending feedback', () => {
    const { scalar, request, update, read } = commandSetup();
    const lease = scalar.attachRenderer();
    lease.nativeInput(2_500);
    const token = lease.beginPointer()!;
    lease.pointer(token, 2_700);

    update({ feedback: feedback('submitted', {
      target: 2_700, requestedTarget: 2_700, lifecycleId: 'life-1', transitionId: 'submitted-1',
    }) });
    expect(scalar.view.draft).toBe(2_700);
    update({ feedback: {
      ...read().feedback,
      phase: 'awaiting-confirmation',
      transitionId: 'awaiting-1',
    } });
    lease.pointer(token, 2_900);
    expect(request.mock.calls).toEqual([[2_500], [2_700], [2_900]]);
    expect(scalar.view.draft).toBe(2_900);
  });

  it('hands an ended optimistic HBar pointer draft to newly represented command evidence', () => {
    const initial = feedback('idle', {
      confirmed: 0.5,
      scope: { control: 'rf-gain', receiver: 0 },
    });
    const { scalar, request, update } = commandSetup(undefined, {
      domain: NORMALIZED_DOMAIN,
      command: 'set_rf_gain',
      feedback: initial,
    });
    const lease = scalar.attachRenderer();
    const token = lease.beginPointer()!;
    lease.pointer(token, 0.7);
    lease.endPointer(token);

    expect(request).toHaveBeenCalledOnce();
    expect(Math.round(request.mock.calls[0][0] * 255)).toBe(179);
    expect(scalar.view).toMatchObject({ interaction: 'idle', canonical: 0.5 });
    expect(scalar.view.draft).not.toBeNull();

    update({ feedback: feedback('awaiting-confirmation', {
      confirmed: 0.5,
      target: 179 / 255,
      requestedTarget: 179 / 255,
      lifecycleId: 'rf-179',
      transitionId: 'rf-awaiting-179',
      scope: { control: 'rf-gain', receiver: 0 },
    }) });
    expect(scalar.view).toMatchObject({
      draft: null,
      displayed: 0.5,
      target: 179 / 255,
      phase: 'awaiting-confirmation',
    });

    update({ feedback: feedback('idle', {
      confirmed: 0.8,
      scope: { control: 'rf-gain', receiver: 0 },
    }) });
    expect(scalar.view).toMatchObject({ draft: null, displayed: 0.8, canonical: 0.8 });
  });

  it('retains a live HBar drag through synchronous feedback and hands off its latest request on release', () => {
    let current!: Extract<ContinuousScalarInput, { evidence: 'command-feedback' }>;
    const request = vi.fn<(value: number) => void>((value) => {
      const raw = Math.round(value * 255);
      current = commandInput(request, {
        domain: NORMALIZED_DOMAIN,
        command: 'set_rf_gain',
        feedback: feedback('submitted', {
          confirmed: 0.5,
          target: raw / 255,
          requestedTarget: raw / 255,
          lifecycleId: `rf-${raw}`,
          transitionId: `rf-submitted-${raw}`,
          scope: { control: 'rf-gain', receiver: 0 },
        }),
      });
    });
    current = commandInput(request, {
      domain: NORMALIZED_DOMAIN,
      command: 'set_rf_gain',
      feedback: feedback('idle', {
        confirmed: 0.5,
        scope: { control: 'rf-gain', receiver: 0 },
      }),
    });
    const scalar = createContinuousScalar(
      () => current,
      createHBarContinuousScalarPolicy({ preview: 'optimistic', debounceMs: 0 }),
    );
    const lease = scalar.attachRenderer();
    const token = lease.beginPointer()!;

    lease.pointer(token, 0.7);
    expect(scalar.view).toMatchObject({ interaction: 'pointer', target: 179 / 255 });
    expect(scalar.view.draft).not.toBeNull();
    lease.pointer(token, 0.8);
    expect(scalar.view).toMatchObject({ interaction: 'pointer', draft: 0.8, target: 0.8 });

    lease.endPointer(token);
    expect(request).toHaveBeenCalledTimes(2);
    expect(scalar.view).toMatchObject({
      interaction: 'idle', draft: null, displayed: 0.5, target: 0.8,
    });
  });

  it.each([0, 50])('hands a %dms HBar key request to quantized command evidence after dispatch', (debounceMs) => {
    vi.useFakeTimers();
    const initial = feedback('idle', {
      confirmed: 0.5,
      scope: { control: 'rf-gain', receiver: 0 },
    });
    const policy = createHBarContinuousScalarPolicy({ preview: 'optimistic', debounceMs });
    const { scalar, request, update } = commandSetup(policy, {
      domain: NORMALIZED_DOMAIN,
      command: 'set_rf_gain',
      feedback: initial,
    });
    const lease = scalar.attachRenderer();

    expect(lease.key({ key: 'ArrowRight', fine: false })).toBe(true);
    expect(scalar.view.draft).toBe(0.51);
    update({ feedback: { ...initial, scope: { ...initial.scope } } });
    expect(scalar.view.draft).toBe(0.51);
    vi.advanceTimersByTime(debounceMs);
    expect(request).toHaveBeenCalledExactlyOnceWith(0.51);

    update({ feedback: feedback('awaiting-confirmation', {
      confirmed: 0.5,
      target: 130 / 255,
      requestedTarget: 130 / 255,
      lifecycleId: `rf-key-${debounceMs}`,
      transitionId: `rf-key-awaiting-${debounceMs}`,
      scope: { control: 'rf-gain', receiver: 0 },
    }) });
    expect(scalar.view).toMatchObject({ draft: null, displayed: 0.5, target: 130 / 255 });

    update({ feedback: feedback('idle', {
      confirmed: 0.8,
      scope: { control: 'rf-gain', receiver: 0 },
    }) });
    expect(scalar.view).toMatchObject({ displayed: 0.8, canonical: 0.8 });
    vi.useRealTimers();
  });

  it.each([
    ['immediate exact zero', 0, 0, 0],
    ['debounced exact zero', 50, 0, 0],
    ['immediate nonzero quantized', 0, 0.7, 179 / 255],
    ['debounced nonzero quantized', 50, 0.7, 179 / 255],
  ] as const)('hands a %s HBar reset to represented command evidence', (
    _label, debounceMs, defaultValue, target,
  ) => {
    vi.useFakeTimers();
    const initial = feedback('idle', {
      confirmed: 0.5,
      scope: { control: 'rf-gain', receiver: 0 },
    });
    const policy = createHBarContinuousScalarPolicy({ preview: 'optimistic', debounceMs });
    const { scalar, request, update } = commandSetup(policy, {
      domain: { ...NORMALIZED_DOMAIN, defaultValue },
      command: 'set_rf_gain',
      feedback: initial,
    });
    const lease = scalar.attachRenderer();

    lease.reset();
    expect(scalar.view.draft).not.toBeNull();
    vi.advanceTimersByTime(debounceMs);
    expect(request).toHaveBeenCalledOnce();
    expect(Math.round(request.mock.calls[0][0] * 255)).toBe(Math.round(defaultValue * 255));
    update({ feedback: feedback('submitted', {
      confirmed: 0.5,
      target,
      requestedTarget: target,
      lifecycleId: `rf-reset-${defaultValue}`,
      transitionId: `rf-reset-submitted-${defaultValue}`,
      scope: { control: 'rf-gain', receiver: 0 },
    }) });
    expect(scalar.view).toMatchObject({ draft: null, displayed: 0.5, target });
    vi.useRealTimers();
  });

  it('keeps a debounced HBar B through terminal A, then hands off to represented B evidence', () => {
    vi.useFakeTimers();
    const pendingA = feedback('awaiting-confirmation', {
      confirmed: 0.5,
      target: 179 / 255,
      requestedTarget: 179 / 255,
      lifecycleId: 'rf-a',
      transitionId: 'rf-a-awaiting',
      scope: { control: 'rf-gain', receiver: 0 },
    });
    const policy = createHBarContinuousScalarPolicy({ preview: 'optimistic', debounceMs: 50 });
    const { scalar, request, update } = commandSetup(policy, {
      domain: NORMALIZED_DOMAIN,
      command: 'set_rf_gain',
      feedback: pendingA,
    });
    const lease = scalar.attachRenderer();

    expect(lease.key({ key: 'ArrowRight', fine: false })).toBe(true);
    update({ feedback: feedback('failed', {
      confirmed: 0.5,
      requestedTarget: 179 / 255,
      lifecycleId: 'rf-a',
      transitionId: 'rf-a-failed',
      outcome: { phase: 'failed', error: 'late A' },
      scope: { control: 'rf-gain', receiver: 0 },
    }) });
    expect(scalar.view).toMatchObject({ draft: 0.51, phase: 'failed' });
    vi.advanceTimersByTime(50);
    expect(request).toHaveBeenCalledExactlyOnceWith(0.51);

    update({ feedback: feedback('submitted', {
      confirmed: 0.5,
      target: 130 / 255,
      requestedTarget: 130 / 255,
      lifecycleId: 'rf-b',
      transitionId: 'rf-b-submitted',
      scope: { control: 'rf-gain', receiver: 0 },
    }) });
    expect(scalar.view).toMatchObject({ draft: null, target: 130 / 255 });
    vi.useRealTimers();
  });

  it('keeps HBar B through delayed terminal A after pointer end and retires only for B evidence', () => {
    const pendingA = feedback('awaiting-confirmation', {
      confirmed: 0.5,
      target: 153 / 255,
      requestedTarget: 153 / 255,
      lifecycleId: 'rf-a',
      transitionId: 'rf-a-awaiting',
      scope: { control: 'rf-gain', receiver: 0 },
    });
    const { scalar, update } = commandSetup(undefined, {
      domain: NORMALIZED_DOMAIN,
      command: 'set_rf_gain',
      feedback: pendingA,
    });
    const lease = scalar.attachRenderer();
    const token = lease.beginPointer()!;
    lease.pointer(token, 0.7);
    lease.endPointer(token);

    const delayedA = feedback('failed', {
      confirmed: 0.5,
      requestedTarget: 153 / 255,
      lifecycleId: 'rf-a',
      transitionId: 'rf-a-failed',
      outcome: { phase: 'failed', error: 'late A' },
      scope: { control: 'rf-gain', receiver: 0 },
    });
    update({ feedback: delayedA });
    expect(scalar.view.draft).not.toBeNull();
    update({ feedback: { ...delayedA, scope: { ...delayedA.scope } } });
    expect(scalar.view.draft).not.toBeNull();

    update({ feedback: feedback('submitted', {
      confirmed: 0.5,
      target: 179 / 255,
      requestedTarget: 179 / 255,
      lifecycleId: 'rf-b',
      transitionId: 'rf-b-submitted',
      scope: { control: 'rf-gain', receiver: 0 },
    }) });
    expect(scalar.view).toMatchObject({ draft: null, target: 179 / 255 });
  });

  it('preserves a represented command HBar wheel draft until the 300ms hold expires', () => {
    vi.useFakeTimers();
    const initial = feedback('idle', {
      confirmed: 0.5,
      scope: { control: 'rf-gain', receiver: 0 },
    });
    const policy = createHBarContinuousScalarPolicy({ preview: 'optimistic', debounceMs: 50 });
    const { scalar, request, update } = commandSetup(policy, {
      domain: NORMALIZED_DOMAIN,
      command: 'set_rf_gain',
      feedback: initial,
    });
    const lease = scalar.attachRenderer();

    lease.wheel({ direction: 1, fine: false });
    expect(request).toHaveBeenCalledExactlyOnceWith(0.56);
    update({ feedback: feedback('awaiting-confirmation', {
      confirmed: 0.5,
      target: 143 / 255,
      requestedTarget: 143 / 255,
      lifecycleId: 'rf-wheel-143',
      transitionId: 'rf-wheel-awaiting-143',
      scope: { control: 'rf-gain', receiver: 0 },
    }) });
    vi.advanceTimersByTime(299);
    expect(scalar.view).toMatchObject({ interaction: 'wheel', draft: 0.56, displayed: 0.56 });
    vi.advanceTimersByTime(1);
    expect(scalar.view).toMatchObject({ interaction: 'idle', draft: null, displayed: 0.5 });
    vi.useRealTimers();
  });

  it.each([
    ['failed', 'key'], ['timed-out', 'reset'],
    ['cancelled', 'key'], ['superseded', 'reset'],
  ] as const)(
    'cancels pre-%s %s work once and permits a fresh pointer gesture', (phase, source) => {
      vi.useFakeTimers();
      const { scalar, request, update } = commandSetup(undefined, {
        domain: { ...DOMAIN, defaultValue: 2_700 },
      });
      const lease = scalar.attachRenderer();
      const staleToken = lease.beginPointer()!;
      lease.pointer(staleToken, 2_600);
      if (source === 'key') expect(lease.key({ key: 'ArrowRight', fine: false })).toBe(true);
      else lease.reset();
      expect(scalar.view.draft).toBe(2_700);

      const terminal = feedback(phase, {
        requestedTarget: 2_700,
        lifecycleId: 'life-1',
        transitionId: `${phase}-1`,
        outcome: { phase, error: 'radio rejected' },
      });
      update({ feedback: terminal });
      vi.advanceTimersByTime(50);
      expect(request.mock.calls).toEqual([[2_600]]);
      expect(scalar.view).toMatchObject({
        feedback: terminal,
        draft: null,
        displayed: 2_400,
        error: 'radio rejected',
        phase,
      });
      lease.pointer(staleToken, 2_900);
      const freshToken = lease.beginPointer();
      expect(freshToken).not.toBeNull();
      lease.pointer(freshToken!, 2_800);
      expect(request.mock.calls).toEqual([[2_600], [2_800]]);
      vi.useRealTimers();
    },
  );

  it('keeps every intent path live when a handled terminal outcome survives a phase change', () => {
    vi.useFakeTimers();
    const terminal = feedback('failed', {
      requestedTarget: 2_500,
      lifecycleId: 'life-retained',
      transitionId: 'failed-retained',
      outcome: { phase: 'failed', error: 'retained' },
    });
    const { scalar, request, update } = commandSetup(undefined, {
      domain: { ...DOMAIN, defaultValue: 2_700 }, feedback: terminal,
    });
    expect(scalar.view.error).toBe('retained');
    const lease = scalar.attachRenderer();
    const token = lease.beginPointer()!;
    lease.pointer(token, 2_600);
    update({ feedback: {
      ...terminal, phase: 'idle', busy: false, transitionId: 'idle-after-retained',
    } });
    lease.pointer(token, 2_620);
    lease.nativeInput(2_650);
    lease.wheel({ direction: 1, fine: true });
    lease.key({ key: 'ArrowRight', fine: false });
    vi.advanceTimersByTime(50);
    lease.reset();
    vi.advanceTimersByTime(50);
    expect(request.mock.calls).toEqual([[2_600], [2_620], [2_650], [2_660], [2_800], [2_700]]);
    vi.useRealTimers();
  });

  it('retires an optimistic draft when confirmation reaches it', () => {
    const { scalar, update } = commandSetup(nativeRangeContinuousScalarPolicy);
    const lease = scalar.attachRenderer();
    lease.nativeInput(2_700);
    update({ feedback: feedback('confirmed', {
      confirmed: 2_700,
      requestedTarget: 2_700,
      lifecycleId: 'life-1',
      transitionId: 'confirmed-1',
      outcome: { phase: 'confirmed' },
    }) });
    expect(scalar.view).toMatchObject({ canonical: 2_700, draft: null, displayed: 2_700, interaction: 'idle' });
  });

  it('hands a native draft to newly represented quantized command evidence and follows later truth', () => {
    const initial = feedback('idle', {
      confirmed: 0.5,
      scope: { control: 'rf-gain', receiver: 0 },
    });
    const { scalar, update } = commandSetup(nativeRangeContinuousScalarPolicy, {
      domain: NORMALIZED_DOMAIN,
      command: 'set_rf_gain',
      feedback: initial,
    });
    const lease = scalar.attachRenderer();
    lease.nativeInput(0.7);
    const localDraft = scalar.view.draft;
    expect(localDraft).not.toBeNull();

    update({ feedback: { ...initial, scope: { ...initial.scope } } });
    expect(scalar.view.draft).toBe(localDraft);

    update({ feedback: feedback('awaiting-confirmation', {
      confirmed: 0.5,
      target: 179 / 255,
      requestedTarget: 179 / 255,
      lifecycleId: 'rf-179',
      transitionId: 'rf-awaiting-179',
      scope: { control: 'rf-gain', receiver: 0 },
    }) });
    expect(scalar.view).toMatchObject({
      draft: null,
      target: 179 / 255,
      canonical: 0.5,
      phase: 'awaiting-confirmation',
      interaction: 'idle',
    });

    update({ feedback: feedback('confirmed', {
      confirmed: 179 / 255,
      requestedTarget: 179 / 255,
      lifecycleId: 'rf-179',
      transitionId: 'rf-confirmed-179',
      outcome: { phase: 'confirmed' },
      scope: { control: 'rf-gain', receiver: 0 },
    }) });
    expect(scalar.view).toMatchObject({ canonical: 179 / 255, draft: null });
    update({ feedback: feedback('idle', {
      confirmed: 204 / 255,
      scope: { control: 'rf-gain', receiver: 0 },
    }) });
    expect(scalar.view).toMatchObject({ canonical: 0.8, draft: null, displayed: 0.8 });
  });

  it('keeps request B while terminal A is retained or cloned, then hands off only to B evidence', () => {
    const terminalA = feedback('confirmed', {
      confirmed: 0.5,
      requestedTarget: 179 / 255,
      lifecycleId: 'rf-a',
      transitionId: 'rf-a-confirmed',
      outcome: { phase: 'confirmed' },
      scope: { control: 'rf-gain', receiver: 0 },
    });
    const { scalar, update } = commandSetup(nativeRangeContinuousScalarPolicy, {
      domain: NORMALIZED_DOMAIN,
      command: 'set_rf_gain',
      feedback: terminalA,
    });
    const lease = scalar.attachRenderer();
    expect(scalar.view.phase).toBe('confirmed');
    lease.nativeInput(0.8);
    expect(scalar.view.draft).toBe(0.8);

    update({ feedback: { ...terminalA, scope: { ...terminalA.scope } } });
    expect(scalar.view).toMatchObject({ draft: 0.8, phase: 'confirmed' });

    update({ feedback: feedback('submitted', {
      confirmed: 0.5,
      target: 204 / 255,
      requestedTarget: 204 / 255,
      lifecycleId: 'rf-b',
      transitionId: 'rf-b-submitted',
      scope: { control: 'rf-gain', receiver: 0 },
    }) });
    expect(scalar.view).toMatchObject({
      draft: null,
      target: 0.8,
      phase: 'submitted',
      interaction: 'idle',
    });
  });

  it('keeps request B when delayed terminal A arrives before B feedback', () => {
    const pendingA = feedback('awaiting-confirmation', {
      confirmed: 0.5,
      target: 179 / 255,
      requestedTarget: 179 / 255,
      lifecycleId: 'rf-a',
      transitionId: 'rf-a-awaiting',
      scope: { control: 'rf-gain', receiver: 0 },
    });
    const { scalar, update } = commandSetup(nativeRangeContinuousScalarPolicy, {
      domain: NORMALIZED_DOMAIN,
      command: 'set_rf_gain',
      feedback: pendingA,
    });
    const lease = scalar.attachRenderer();
    lease.nativeInput(0.8);
    expect(scalar.view.draft).toBe(0.8);

    update({ feedback: feedback('failed', {
      confirmed: 0.5,
      requestedTarget: 179 / 255,
      lifecycleId: 'rf-a',
      transitionId: 'rf-a-failed',
      outcome: { phase: 'failed', error: 'late A' },
      scope: { control: 'rf-gain', receiver: 0 },
    }) });
    expect(scalar.view).toMatchObject({ draft: 0.8, phase: 'failed' });

    update({ feedback: feedback('submitted', {
      confirmed: 0.5,
      target: 204 / 255,
      requestedTarget: 204 / 255,
      lifecycleId: 'rf-b',
      transitionId: 'rf-b-submitted',
      scope: { control: 'rf-gain', receiver: 0 },
    }) });
    expect(scalar.view).toMatchObject({ draft: null, target: 0.8, phase: 'submitted' });
  });

  it('reconciles reading updates as canonical values, not invented authority sessions', () => {
    const { scalar, request, update } = readingSetup();
    const lease = scalar.attachRenderer();
    const token = lease.beginPointer()!;
    lease.pointer(token, 2_700);
    update({ reading: { status: 'known', value: 2_500 } });
    expect(scalar.view).toMatchObject({ canonical: 2_500, draft: 2_700 });
    lease.pointer(token, 2_800);
    expect(request.mock.calls).toEqual([[2_700], [2_800]]);
  });
});

describe('continuous scalar deferred work', () => {
  it('invalidates pending work when a valid domain becomes invalid', () => {
    vi.useFakeTimers();
    const { scalar, request, update } = readingSetup();
    const lease = scalar.attachRenderer();
    lease.key({ key: 'ArrowRight', fine: false });

    update({ domain: { ...DOMAIN, max: Number.NEGATIVE_INFINITY } });
    expect(scalar.view).toMatchObject({
      canonical: 2_400,
      displayed: 2_400,
      domainValid: false,
      editable: false,
      draft: null,
    });
    vi.advanceTimersByTime(50);
    expect(request).not.toHaveBeenCalled();
    vi.useRealTimers();
  });

  it('dispatches a debounced candidate through the current request callback', () => {
    vi.useFakeTimers();
    const original = vi.fn<(value: number) => void>();
    const replacement = vi.fn<(value: number) => void>();
    let current = readingInput(original);
    const scalar = createContinuousScalar(
      () => current,
      createHBarContinuousScalarPolicy({ preview: 'optimistic', debounceMs: 50 }),
    );
    const lease = scalar.attachRenderer();
    lease.key({ key: 'ArrowRight', fine: false });
    current = { ...current, request: replacement };
    vi.advanceTimersByTime(50);
    expect(original).not.toHaveBeenCalled();
    expect(replacement).toHaveBeenCalledExactlyOnceWith(2_500);
    vi.useRealTimers();
  });

  it.each([
    ['provider', { feedback: feedback('idle', { providerGeneration: 4 }) }],
    ['epoch', { feedback: feedback('idle', { sessionEpoch: 8 }) }],
    ['receiver', { feedback: feedback('idle', { scope: { control: 'filter-width', receiver: 1, slot: 'main' } }) }],
    ['control', { feedback: feedback('idle', { scope: { control: 'other', receiver: 0, slot: 'main' } }) }],
    ['slot', { feedback: feedback('idle', { scope: { control: 'filter-width', receiver: 0, slot: 'sub' } }) }],
    ['command', { command: 'set_other' }],
    ['domain', { domain: { ...DOMAIN, max: 4_000 } }],
    ['availability', { feedback: feedback('unavailable', { confirmed: null }) }],
  ] as const)('drops command debounce after %s replacement', (_label, replacement) => {
    vi.useFakeTimers();
    const { scalar, request, update } = commandSetup();
    const lease = scalar.attachRenderer();
    lease.key({ key: 'ArrowRight', fine: false });
    update(replacement);
    vi.advanceTimersByTime(50);
    expect(request).not.toHaveBeenCalled();
    expect(scalar.view.draft).toBeNull();
    vi.useRealTimers();
  });

  it('invalidates draft, lease, and announcement memory on provider replacement without a null read', () => {
    const terminal = feedback('failed', {
      requestedTarget: 2_500, lifecycleId: 'old-provider',
      transitionId: 'old-provider-failed', outcome: { phase: 'failed' },
    });
    const { scalar, update } = commandSetup(nativeRangeContinuousScalarPolicy, {
      feedback: terminal,
    });
    const stale = scalar.attachRenderer();
    expect(stale.view.announcement).toBe('Failed: 2500');
    const staleToken = stale.beginPointer()!;
    stale.pointer(staleToken, 2_700);
    expect(scalar.view.draft).toBe(2_700);

    update({ feedback: feedback('idle', { providerGeneration: 4 }) });
    expect(scalar.view).toMatchObject({ draft: null, announcement: null });
    stale.pointer(staleToken, 2_800);
    expect(scalar.view.draft).toBeNull();
    stale.nativeInput(2_900);
    expect(scalar.view.draft).toBe(2_900);
  });

  it.each([
    ['owner', { ownerKey: 'filter-width:sub' }],
    ['knownness', { reading: { status: 'unknown' as const } }],
    ['domain', { domain: { ...DOMAIN, step: 50 } }],
    ['enabled gate', { enabled: false }],
  ] as const)('drops reading debounce after %s replacement', (_label, replacement) => {
    vi.useFakeTimers();
    const { scalar, request, update } = readingSetup();
    const lease = scalar.attachRenderer();
    lease.key({ key: 'ArrowRight', fine: false });
    update(replacement);
    vi.advanceTimersByTime(50);
    expect(request).not.toHaveBeenCalled();
    expect(scalar.view.draft).toBeNull();
    vi.useRealTimers();
  });

  it('restarts one wheel hold and actively reconciles at expiry', () => {
    vi.useFakeTimers();
    const { scalar, request, update } = readingSetup();
    const lease = scalar.attachRenderer();
    lease.wheel({ direction: 1, fine: true });
    vi.advanceTimersByTime(200);
    lease.wheel({ direction: 1, fine: true });
    update({ reading: { status: 'known', value: 2_500 } });
    vi.advanceTimersByTime(299);
    expect(scalar.view).toMatchObject({ interaction: 'wheel', draft: 2_420, displayed: 2_420 });
    vi.advanceTimersByTime(1);
    expect(scalar.view).toMatchObject({ interaction: 'idle', draft: null, displayed: 2_500 });
    expect(request.mock.calls).toEqual([[2_410], [2_420]]);
    vi.useRealTimers();
  });

  it('reconciles a rejected Bipolar wheel draft to current canonical state at expiry', () => {
    vi.useFakeTimers();
    const policy = createBipolarContinuousScalarPolicy({ debounceMs: 50 });
    const { scalar, request } = readingSetup(policy, {
      domain: { min: -100, max: 100, step: 5, defaultValue: 0, fineStepDivisor: 10 },
      reading: { status: 'known', value: 0 },
    });
    const lease = scalar.attachRenderer();

    lease.wheel({ direction: 1, fine: false });
    expect(request).toHaveBeenCalledExactlyOnceWith(5);
    expect(scalar.view).toMatchObject({ interaction: 'wheel', draft: 5, displayed: 5 });
    vi.advanceTimersByTime(300);
    expect(scalar.view).toMatchObject({ interaction: 'idle', draft: null, displayed: 0 });
    vi.useRealTimers();
  });
});

describe('continuous scalar renderer leases and cleanup', () => {
  it('makes replaced renderer callbacks and stale gesture tokens inert', () => {
    vi.useFakeTimers();
    const { scalar, request } = readingSetup();
    const rendererA = scalar.attachRenderer();
    const tokenA = rendererA.beginPointer()!;
    rendererA.pointer(tokenA, 2_700);
    rendererA.key({ key: 'ArrowRight', fine: false });

    const rendererB = scalar.attachRenderer();
    expect(scalar.view).toMatchObject({ draft: null, interaction: 'idle' });
    rendererA.pointer(tokenA, 3_000);
    rendererA.nativeInput(3_100);
    rendererA.wheel({ direction: 1, fine: false });
    rendererA.reset();
    rendererA.dispose();
    vi.advanceTimersByTime(100);
    expect(request.mock.calls).toEqual([[2_700]]);

    const tokenB = rendererB.beginPointer()!;
    rendererB.pointer(tokenA, 3_200);
    rendererB.pointer(tokenB, 2_900);
    expect(request.mock.calls).toEqual([[2_700], [2_900]]);
    vi.useRealTimers();
  });

  it('destroy clears timers and permanently forbids future intents', () => {
    vi.useFakeTimers();
    const { scalar, request } = readingSetup();
    const lease = scalar.attachRenderer();
    lease.key({ key: 'ArrowRight', fine: false });
    lease.wheel({ direction: 1, fine: true });
    scalar.destroy();
    vi.advanceTimersByTime(1_000);
    lease.nativeInput(3_000);
    lease.wheel({ direction: 1, fine: true });
    lease.reset();
    expect(request.mock.calls).toEqual([[2_510]]);
    expect(scalar.attachRenderer().beginPointer()).toBeNull();
    vi.useRealTimers();
  });
});
