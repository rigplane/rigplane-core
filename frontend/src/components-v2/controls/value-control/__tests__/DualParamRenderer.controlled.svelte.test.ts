import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import DualParamRenderer from '../DualParamRenderer.svelte';
import {
  type ContinuousPairBinding,
  type ContinuousPairInput,
  type ContinuousPairLaneView,
  type ContinuousPairRendererLease,
  type ContinuousPairView,
} from '../../../../primitives/scalar/continuous-pair.svelte';
import type { CommandScalarFeedback } from '../../../../primitives/scalar/continuous-scalar.svelte';

let mounted: ReturnType<typeof mount>[] = [];

afterEach(async () => {
  await Promise.all(mounted.map(component => unmount(component)));
  mounted = [];
  document.body.innerHTML = '';
});

function readingView(): ContinuousPairView {
  const lane = (value: number) => ({
    evidence: 'reading' as const,
    reading: { status: 'known' as const, value },
    availability: 'available' as const,
    canonical: value,
    localRequested: null,
    feedback: null,
    phase: null,
    error: null,
    presentation: null,
    announcement: null,
  });
  return {
    evidence: 'reading',
    domain: { min: 0, max: 1, step: 0.01, defaultValue: null, fineStepDivisor: 10 },
    domainValid: true,
    canonical: { rf: 1, sql: 0 },
    position: 0.5,
    axisDraft: null,
    draft: null,
    displayedPosition: 0.5,
    editable: true,
    busy: false,
    interaction: 'idle',
    lanes: { rf: lane(1), sql: lane(0) },
  };
}

function fakeBinding(token: number, readView: () => ContinuousPairView = readingView) {
  const lease: ContinuousPairRendererLease = {
    get view() { return readView(); },
    beginPointer: vi.fn(() => token),
    pointer: vi.fn(),
    endPointer: vi.fn(),
    cancelPointer: vi.fn(),
    nativeInput: vi.fn(),
    wheel: vi.fn(),
    key: vi.fn(() => true),
    reset: vi.fn(),
    dispose: vi.fn(),
  };
  const binding: ContinuousPairBinding = {
    get view() { return readView(); },
    attachRenderer: vi.fn(() => lease),
    cancel: vi.fn(),
    destroy: vi.fn(),
  };
  return { binding, lease };
}

function mountReactive(binding: ContinuousPairBinding) {
  const state = $state({ binding });
  const target = document.createElement('div');
  document.body.appendChild(target);
  const component = mount(DualParamRenderer, { target, props: state });
  mounted.push(component);
  flushSync();
  return { state, target, component };
}

function slider(target: HTMLElement): HTMLElement {
  return target.querySelector('[role="slider"]') as HTMLElement;
}

describe('binding-only DualParamRenderer', () => {
  it('owns one lease, forwards every gesture, and disposes leases without destroying owners', async () => {
    const first = fakeBinding(11);
    const second = fakeBinding(22);
    const { state, target, component } = mountReactive(first.binding);
    const control = slider(target) as HTMLElement & {
      setPointerCapture(id: number): void;
      hasPointerCapture(id: number): boolean;
      releasePointerCapture(id: number): void;
    };
    control.setPointerCapture = vi.fn();
    control.hasPointerCapture = vi.fn(() => true);
    control.releasePointerCapture = vi.fn();
    vi.spyOn(target.querySelector('.vc-dual') as HTMLElement, 'getBoundingClientRect')
      .mockReturnValue({ left: 0, width: 100 } as DOMRect);

    control.dispatchEvent(new PointerEvent('pointerdown', {
      bubbles: true, cancelable: true, pointerId: 1, clientX: 0,
    }));
    control.dispatchEvent(new PointerEvent('pointermove', {
      bubbles: true, pointerId: 1, clientX: 100,
    }));
    control.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, pointerId: 1 }));
    expect(first.lease.pointer).toHaveBeenNthCalledWith(1, 11, 0);
    expect(first.lease.pointer).toHaveBeenNthCalledWith(2, 11, 1);
    expect(first.lease.endPointer).toHaveBeenCalledExactlyOnceWith(11);

    control.dispatchEvent(new PointerEvent('pointerdown', {
      bubbles: true, cancelable: true, pointerId: 2, clientX: 50,
    }));
    control.dispatchEvent(new PointerEvent('pointercancel', { bubbles: true, pointerId: 2 }));
    expect(first.lease.cancelPointer).toHaveBeenCalledExactlyOnceWith(11);
    control.dispatchEvent(new WheelEvent('wheel', { bubbles: true, cancelable: true, deltaY: -1 }));
    control.dispatchEvent(new WheelEvent('wheel', {
      bubbles: true, cancelable: true, deltaY: 1, shiftKey: true,
    }));
    expect(first.lease.wheel).toHaveBeenNthCalledWith(1, { direction: 1, fine: false });
    expect(first.lease.wheel).toHaveBeenNthCalledWith(2, { direction: -1, fine: true });
    control.dispatchEvent(new KeyboardEvent('keydown', {
      bubbles: true, cancelable: true, key: 'ArrowRight', shiftKey: true,
    }));
    control.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }));
    expect(first.lease.key).toHaveBeenCalledExactlyOnceWith({ key: 'ArrowRight', fine: true });
    expect(first.lease.reset).toHaveBeenCalledOnce();

    control.dispatchEvent(new PointerEvent('pointerdown', {
      bubbles: true, cancelable: true, pointerId: 3, clientX: 25,
    }));
    state.binding = second.binding;
    flushSync();
    expect(first.lease.dispose).toHaveBeenCalledOnce();
    expect(control.releasePointerCapture).toHaveBeenCalledWith(3);
    control.dispatchEvent(new PointerEvent('pointermove', {
      bubbles: true, pointerId: 3, clientX: 75,
    }));
    expect(second.lease.pointer).not.toHaveBeenCalled();
    control.dispatchEvent(new KeyboardEvent('keydown', { bubbles: true, key: 'ArrowLeft' }));
    expect(second.lease.key).toHaveBeenCalledExactlyOnceWith({ key: 'ArrowLeft', fine: false });

    mounted = mounted.filter(item => item !== component);
    await unmount(component);
    expect(second.lease.dispose).toHaveBeenCalledOnce();
    expect(first.binding.destroy).not.toHaveBeenCalled();
    expect(second.binding.destroy).not.toHaveBeenCalled();
  });

  it('renders distinct lane truth and one status for each supplied lane announcement', () => {
    const feedback = (
      control: string,
      confirmed: number,
      over: Partial<CommandScalarFeedback>,
    ): CommandScalarFeedback => ({
      confirmed, target: null, requestedTarget: null, phase: 'idle', busy: false,
      availability: 'available', outcome: null, lifecycleId: null, transitionId: null,
      providerGeneration: 3, sessionEpoch: 7, scope: { control, receiver: 0 },
      repeatPolicy: 'latest-target-wins', ...over,
    });
    const input: ContinuousPairInput = {
      evidence: 'command-feedback', enabled: true,
      domain: { min: 0, max: 1, step: 0.01, defaultValue: null, fineStepDivisor: 10 },
      requestRf: vi.fn(), requestSql: vi.fn(),
      rf: { command: 'set_rf_gain', feedback: feedback('rf-gain', 0.5, {
        target: 0.55, requestedTarget: 0.6, phase: 'submitted', busy: true,
        lifecycleId: 'rf-1', transitionId: 'rf-submitted',
      }) },
      sql: { command: 'set_squelch', feedback: feedback('squelch', 0.2, {
        requestedTarget: 0.3, phase: 'failed',
        outcome: { phase: 'failed', error: 'radio rejected' },
        lifecycleId: 'sql-1', transitionId: 'sql-failed',
      }) },
    };
    const lane = (
      command: string,
      supplied: CommandScalarFeedback,
      announcement: string,
    ): ContinuousPairLaneView => ({
      evidence: 'command-feedback', command, feedback: supplied,
      availability: supplied.availability, canonical: supplied.confirmed,
      localRequested: null, phase: supplied.phase,
      error: supplied.outcome?.error ?? null,
      presentation: {
        attributes: {
          'data-command-phase': supplied.phase,
          'aria-busy': supplied.busy ? 'true' : 'false',
        },
        targetDescription: null,
        politeAnnouncement: supplied.transitionId === null ? null : {
          politeness: 'polite', transitionId: supplied.transitionId,
          phase: supplied.phase, targetDescription: null, message: announcement,
        },
        state: { announcedTransitionIds: supplied.transitionId === null ? [] : [supplied.transitionId] },
      },
      announcement,
    });
    const rf = lane('set_rf_gain', input.rf.feedback, 'Submitting RF gain');
    const sql = lane('set_squelch', input.sql.feedback, 'Failed squelch');
    const view: ContinuousPairView = {
      evidence: 'command-feedback', domain: input.domain, domainValid: true,
      canonical: { rf: 0.5, sql: 0.2 }, position: 0.65,
      axisDraft: null, draft: null, displayedPosition: 0.65,
      editable: true, busy: true, interaction: 'idle', lanes: { rf, sql },
    };
    const binding = fakeBinding(31, () => view).binding;
    const target = document.createElement('div');
    document.body.appendChild(target);
    mounted.push(mount(DualParamRenderer, { target, props: { binding } }));
    flushSync();
    const control = slider(target);

    expect(control.dataset).toMatchObject({
      rfCommandPhase: 'submitted', sqlCommandPhase: 'failed',
      rfConfirmed: '0.5', rfTarget: '0.55', rfRequested: '0.6',
      sqlConfirmed: '0.2', sqlTarget: '', sqlRequested: '0.3',
      sqlError: 'radio rejected',
    });
    expect(control.getAttribute('aria-busy')).toBe('true');
    expect(target.querySelectorAll('[data-control-feedback-status]')).toHaveLength(2);
  });
});
