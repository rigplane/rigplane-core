import { describe, expect, it } from 'vitest';
import type { ControlFeedback } from '$lib/runtime/adapters/panel-adapters';
import {
  confirmedChecked, confirmedPressed, confirmedSelected,
  projectControlFeedbackPresentation,
  type ControlFeedbackPresentationInput, type ControlFeedbackPresentationState,
  type PresentationPhase,
} from '../primitives/control-feedback/control-feedback-presentation';

type ActualContractFits = ControlFeedback<number> extends ControlFeedbackPresentationInput<number>
  ? true : false;
const ACTUAL_CONTRACT_FITS: ActualContractFits = true;

const PHASES: readonly PresentationPhase[] = [
  'unavailable', 'idle', 'submitted', 'queued', 'dispatched',
  'awaiting-confirmation', 'confirmed', 'failed', 'timed-out', 'cancelled', 'superseded',
];
const BUSY = new Set<PresentationPhase>([
  'submitted', 'queued', 'dispatched', 'awaiting-confirmation',
]);
const EMPTY: ControlFeedbackPresentationState = { announcedTransitionIds: [] };
const feedback = <T>(phase: PresentationPhase, overrides: Partial<ControlFeedbackPresentationInput<T>> = {}) => ({
  confirmed: null, target: null, requestedTarget: null, phase,
  transitionId: phase === 'idle' || phase === 'unavailable' ? null : `transition-${phase}`,
  outcome: ['confirmed', 'failed', 'timed-out', 'cancelled', 'superseded'].includes(phase)
    ? { phase: phase as 'confirmed' | 'failed' | 'timed-out' | 'cancelled' | 'superseded' }
    : null,
  ...overrides,
} satisfies ControlFeedbackPresentationInput<T>);

describe('pure ControlFeedback presentation contract (MOR-1711)', () => {
  it('stays structurally assignable from the real ControlFeedback type', () => {
    expect(ACTUAL_CONTRACT_FITS).toBe(true);
  });

  it.each(PHASES)('maps phase %s deterministically', (phase) => {
    const result = projectControlFeedbackPresentation(
      feedback<number>(phase, { target: 2400, requestedTarget: 2400 }), EMPTY, String,
    );
    expect(result.attributes).toEqual({
      'data-command-phase': phase, 'aria-busy': BUSY.has(phase) ? 'true' : 'false',
    });
    expect(result.targetDescription).toBe('2400');
    expect(result.politeAnnouncement === null).toBe(phase === 'idle' || phase === 'unavailable');
  });

  it.each(['confirmed', 'failed', 'timed-out', 'cancelled', 'superseded'] as const)(
    'maps terminal outcome %s without a busy ARIA state', (phase) => {
      const result = projectControlFeedbackPresentation(
        feedback<number>(phase, { requestedTarget: 1800, outcome: { phase, error: 'bounded' } }),
        EMPTY, (value) => `${value} Hz`,
      );
      expect(result.attributes['aria-busy']).toBe('false');
      expect(result.politeAnnouncement).toMatchObject({
        politeness: 'polite', transitionId: `transition-${phase}`, phase,
        targetDescription: '1800 Hz',
      });
    },
  );

  it('announces one immutable transition once, then a new transition once', () => {
    const first = projectControlFeedbackPresentation(
      feedback<number>('submitted', { target: 3000, requestedTarget: 3000 }), EMPTY, String,
    );
    expect(first.politeAnnouncement?.transitionId).toBe('transition-submitted');
    const repeated = projectControlFeedbackPresentation(
      feedback<number>('submitted', { target: 3000, requestedTarget: 3000 }), first.state, String,
    );
    expect(repeated.politeAnnouncement).toBeNull();
    const next = projectControlFeedbackPresentation(
      feedback<number>('awaiting-confirmation', {
        target: 3000, requestedTarget: 3000, transitionId: 'transition-new',
      }), repeated.state, String,
    );
    expect(next.politeAnnouncement?.transitionId).toBe('transition-new');
    const delayedOld = projectControlFeedbackPresentation(
      feedback<number>('submitted', { transitionId: 'transition-submitted' }), next.state, String,
    );
    expect(delayedOld.politeAnnouncement).toBeNull();
  });

  it('derives toggle and choice ARIA only from confirmed truth', () => {
    const toggle = feedback<boolean>('submitted', { confirmed: false, target: true, requestedTarget: true });
    expect(confirmedPressed(toggle)).toBe(false);
    expect(confirmedChecked(toggle)).toBe(false);
    const choice = feedback<string>('submitted', { confirmed: 'A', target: 'B', requestedTarget: 'B' });
    expect(confirmedSelected(choice, 'A')).toBe(true);
    expect(confirmedSelected(choice, 'B')).toBe(false);
  });
});
