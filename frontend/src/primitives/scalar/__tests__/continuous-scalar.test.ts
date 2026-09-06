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
  it('does not abort a live drag for changing same-authority pending feedback', () => {
    const { scalar, request, update, read } = commandSetup();
    const lease = scalar.attachRenderer();
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
    expect(request.mock.calls).toEqual([[2_700], [2_900]]);
    expect(scalar.view.draft).toBe(2_900);
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
