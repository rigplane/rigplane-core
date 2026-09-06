import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import type { ControlFeedbackPresentationInput } from '../../control-feedback/control-feedback-presentation';
import {
  createCommittedScalar,
  type CommittedScalarInput,
  type CommittedScalarPolicy,
} from '../committed-scalar.svelte';

type ProviderFeedback = ControlFeedbackPresentationInput<number> & {
  readonly providerGeneration?: number | null;
};

const feedback = (
  phase: ControlFeedbackPresentationInput<number>['phase'] = 'idle',
  over: Partial<ProviderFeedback> = {},
): Readonly<ProviderFeedback> => ({
  confirmed: 64, target: null, requestedTarget: null, phase,
  transitionId: null, outcome: null, providerGeneration: 3, ...over,
});

const rejectPolicy: Readonly<CommittedScalarPolicy> = {
  accepts: (value) => Number.isSafeInteger(value) && value >= 0 && value <= 255,
  normalize: (value) => Math.min(255, Math.max(0, Math.round(value))),
  draftPolicy: 'reject-invalid',
  describeTarget: String,
};
const normalizePolicy: Readonly<CommittedScalarPolicy> = {
  ...rejectPolicy,
  draftPolicy: 'normalize',
};

function setup(
  policy = rejectPolicy,
  initial: Readonly<ControlFeedbackPresentationInput<number>> = feedback(),
) {
  let current: CommittedScalarInput = { feedback: initial, editable: true, contextKey: 'receiver:0' };
  const request = vi.fn();
  const scalar = createCommittedScalar(() => current, policy, request);
  return { scalar, request, update: (over: Partial<CommittedScalarInput>) => {
    current = { ...current, ...over };
  } };
}

describe('createCommittedScalar', () => {
  it('owns no timer or upper-layer import', () => {
    const source = readFileSync('src/primitives/scalar/committed-scalar.svelte.ts', 'utf8');
    expect(source).not.toMatch(/setTimeout|setInterval|semantic\/|components-v2\/|runtime\//);
  });

  it('keeps input local and dispatches one normalized commit', () => {
    const { scalar, request } = setup(normalizePolicy);
    scalar.input(111.4);
    expect(request).not.toHaveBeenCalled();
    expect(scalar.view).toMatchObject({ confirmed: 64, draft: 111, displayed: 111, editing: true });
    expect(scalar.commit(111.4)).toBeNull();
    expect(request).toHaveBeenCalledExactlyOnceWith(111);
    expect(scalar.view).toMatchObject({ confirmed: 64, draft: null, displayed: 64, editing: false });
  });

  it('preserves the full requested-versus-confirmed feedback envelope', () => {
    const pending = feedback('awaiting-confirmation', {
      target: 111, requestedTarget: 111, transitionId: 'pending-1',
    });
    const { scalar, request } = setup(rejectPolicy, pending);
    expect(scalar.view).toMatchObject({
      feedback: pending, confirmed: 64, draft: null, displayed: 111, editable: true,
      presentation: { attributes: { 'aria-busy': 'true' } },
    });
    scalar.input(112);
    scalar.commit(112);
    expect(request).toHaveBeenCalledExactlyOnceWith(112);
  });

  it.each(['failed', 'cancelled', 'timed-out'] as const)(
    'keeps %s outcome and error inspectable', (phase) => {
      const terminal = feedback(phase, {
        requestedTarget: 111, transitionId: `${phase}-1`, outcome: { phase, error: 'radio rejected' },
      });
      const { scalar } = setup(rejectPolicy, terminal);
      expect(scalar.view.feedback).toBe(terminal);
      expect(scalar.view.feedback.outcome).toEqual({ phase, error: 'radio rejected' });
      expect(scalar.view.announcement).toContain('111');
    },
  );

  it('keeps unavailable truth unknown instead of inventing zero', () => {
    const unknown = feedback('unavailable', { confirmed: null });
    const { scalar, request } = setup(rejectPolicy, unknown);
    expect(scalar.view).toMatchObject({ confirmed: null, draft: null, displayed: null, editable: false });
    expect(scalar.commit(0)).toBeNull();
    expect(request).not.toHaveBeenCalled();
  });

  it('rejects invalid drafts under the reject policy', () => {
    const { scalar } = setup();
    scalar.input(64.5); scalar.input(-1);
    expect(scalar.view.draft).toBeNull();
  });

  it('cancels without dispatch and suppresses the trailing native change', () => {
    const { scalar, request } = setup();
    scalar.input(111);
    expect(scalar.cancel()).toBe(64);
    expect(scalar.commit(111)).toBe(64);
    expect(request).not.toHaveBeenCalled();
    scalar.input(99);
    scalar.commit(99);
    expect(request).toHaveBeenCalledExactlyOnceWith(99);
  });

  it.each([
    ['context', { contextKey: 'receiver:1' }],
    ['authority', { feedback: feedback('failed', {
      confirmed: 72, requestedTarget: 111, transitionId: 'failed-1', outcome: { phase: 'failed' },
    }) }],
    ['editability', { editable: false }],
  ] as const)('invalidates a draft on %s change and rejects its stale release', (_name, change) => {
    const { scalar, request, update } = setup();
    scalar.input(111);
    update(change);
    expect(scalar.view.draft).toBeNull();
    expect(scalar.commit(111)).not.toBeNull();
    expect(request).not.toHaveBeenCalled();
  });

  it('preserves a draft across an equivalent feedback clone', () => {
    const current = feedback('awaiting-confirmation', {
      target: 80, requestedTarget: 80, transitionId: 'pending-1',
    });
    const { scalar, request, update } = setup(rejectPolicy, current);
    scalar.input(111);
    update({ feedback: { ...current } });
    expect(scalar.view.draft).toBe(111);
    scalar.commit(111);
    expect(request).toHaveBeenCalledExactlyOnceWith(111);
  });

  it('clears draft and announcement memory when provider changes without an intermediate null read', () => {
    const terminal = feedback('failed', {
      requestedTarget: 111, transitionId: 'old-provider-failed',
      outcome: { phase: 'failed' },
    });
    const { scalar, request, update } = setup(rejectPolicy, terminal);
    expect(scalar.view.announcement).toBe('Failed: 111');
    scalar.input(90);
    expect(scalar.view.draft).toBe(90);

    update({ feedback: feedback('idle', { providerGeneration: 4 }) });
    expect(scalar.view).toMatchObject({ draft: null, announcement: null });
    expect(scalar.commit(90)).toBe(64);
    expect(request).not.toHaveBeenCalled();
  });

  it('supports no-input changes and repeated accepted requests while busy', () => {
    const { scalar, request } = setup(rejectPolicy, feedback('submitted', {
      target: 80, requestedTarget: 80, transitionId: 'submitted-1',
    }));
    scalar.commit(90);
    scalar.input(91);
    scalar.commit(91);
    expect(request.mock.calls).toEqual([[90], [91]]);
  });

  it('keeps draft and announcement memory per instance', () => {
    const current = feedback('failed', {
      requestedTarget: 111, transitionId: 'shared-transition', outcome: { phase: 'failed' },
    });
    const first = setup(rejectPolicy, current).scalar;
    const second = setup(rejectPolicy, current).scalar;
    first.input(90);
    expect(first.view.draft).toBe(90);
    expect(second.view.draft).toBeNull();
    expect(first.view.announcement).not.toBeNull();
    expect(second.view.announcement).not.toBeNull();
  });
});
