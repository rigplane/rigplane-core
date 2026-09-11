import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import DualParamRenderer from '../DualParamRenderer.svelte';
import type { DualParamIssuedStatusPresentation } from '../dual-param-issued-status';
import {
  createContinuousPair,
  createLegacyContinuousPairPolicy,
  createRenderedNativeRangeContinuousPairPolicy,
  nativeRangeContinuousPairPolicy,
  type CommandFeedbackContinuousPairInput,
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

it('leaves unarmed RF/SQL wheel input available for page scrolling', () => {
  const { binding, lease } = fakeBinding(1);
  const { target } = mountReactive(binding);
  const event = new WheelEvent('wheel', { deltaY: -1, bubbles: true, cancelable: true });
  slider(target).dispatchEvent(event);
  expect(event.defaultPrevented).toBe(false);
  expect(lease.wheel).not.toHaveBeenCalled();
});

it.each(['legacy', 'native'] as const)('keeps %s RF/SQL geometry and authority through accelerated wheel input', (kind) => {
  const requestRf = vi.fn();
  const requestSql = vi.fn();
  const source = $state({
    evidence: 'reading' as const, ownerKey: 'receiver-a', enabled: true,
    domain: { min: 0, max: 100, step: 2, defaultValue: null, fineStepDivisor: 10 },
    rf: { reading: { status: 'known' as const, value: 100 }, availability: 'available' as const },
    sql: { reading: { status: 'known' as const, value: 0 }, availability: 'available' as const },
    requestRf, requestSql,
  });
  const binding = createContinuousPair(() => source, kind === 'legacy'
    ? createLegacyContinuousPairPolicy({ keyboardDebounceMs: 50 })
    : createRenderedNativeRangeContinuousPairPolicy());
  const { target } = mountReactive(binding);
  const control = slider(target);
  control.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  const wheel = (time: number, deltaY = -120) => {
    const event = new WheelEvent('wheel', { deltaY, bubbles: true, cancelable: true });
    Object.defineProperty(event, 'timeStamp', { value: time });
    control.dispatchEvent(event);
    flushSync();
    return event;
  };
  wheel(1000);
  if (kind === 'legacy') expect(requestSql).toHaveBeenCalledExactlyOnceWith(2);
  else expect(requestSql).not.toHaveBeenCalled();
  wheel(1010);
  expect(requestSql).toHaveBeenLastCalledWith(kind === 'legacy' ? 18 : 26);
  expect(requestRf).not.toHaveBeenCalled();
  expect(binding.view.canonical).toEqual({ rf: 100, sql: 0 });
  for (let time = 1020; time < 1200; time += 10) {
    source.sql.reading.value = requestSql.mock.lastCall?.[0] ?? 0;
    flushSync();
    wheel(time);
  }
  expect(requestSql).toHaveBeenLastCalledWith(100);
  expect(requestSql.mock.calls.every(([value]) => value >= 0 && value <= 100 && value % 2 === 0)).toBe(true);
  source.ownerKey = 'receiver-b';
  flushSync();
  expect(control.dataset.wheelArmed).toBe('false');
  requestSql.mockClear();
  expect(wheel(1300).defaultPrevented).toBe(false);
  expect(requestSql).not.toHaveBeenCalled();
  binding.destroy();
});

function commandFeedback(
  control: string,
  confirmed: number,
  over: Partial<CommandScalarFeedback>,
): CommandScalarFeedback {
  return {
    confirmed, target: null, requestedTarget: null, phase: 'idle', busy: false,
    availability: 'available', outcome: null, lifecycleId: null, transitionId: null,
    providerGeneration: 3, sessionEpoch: 7, scope: { control, receiver: 0 },
    repeatPolicy: 'latest-target-wins', ...over,
  };
}

function commandPairInput(
  rf: Partial<CommandScalarFeedback>,
  sql: Partial<CommandScalarFeedback>,
): CommandFeedbackContinuousPairInput {
  return {
    evidence: 'command-feedback', enabled: true,
    domain: { min: 0, max: 1, step: 0.01, defaultValue: null, fineStepDivisor: 10 },
    requestRf: vi.fn(), requestSql: vi.fn(),
    rf: { command: 'set_rf_gain', feedback: commandFeedback('rf-gain', 0.5, rf) },
    sql: { command: 'set_squelch', feedback: commandFeedback('squelch', 0.2, sql) },
  };
}

describe('binding-only DualParamRenderer', () => {
  it('issues actual pair transitions once through independent external lane presentations', () => {
    const state = $state({ input: commandPairInput({
      target: 0.55, phase: 'submitted', busy: true,
      lifecycleId: 'rf-1', transitionId: 'rf-submitted',
    }, {
      requestedTarget: 0.3, phase: 'failed',
      outcome: { phase: 'failed', error: 'radio rejected' },
      lifecycleId: 'sql-1', transitionId: 'sql-failed',
    }) });
    const pair = createContinuousPair(() => state.input, nativeRangeContinuousPairPolicy);
    let ownerViewReads = 0;
    const binding: ContinuousPairBinding = {
      get view() { ownerViewReads += 1; return pair.view; },
      attachRenderer: () => pair.attachRenderer(),
      cancel: reason => pair.cancel(reason),
      destroy: () => pair.destroy(),
    };
    const acceptRf = vi.fn();
    const acceptSql = vi.fn();
    const presentation = {
      rf: {
        text: null,
        format: vi.fn(({ lane, laneView, announcement }) =>
          `${lane}:${laneView.phase}:${announcement.message}`),
        accept: acceptRf,
      },
      sql: {
        text: null,
        format: vi.fn(({ lane, laneView, announcement }) =>
          `${lane}:${laneView.phase}:${announcement.message}`),
        accept: acceptSql,
      },
    } satisfies DualParamIssuedStatusPresentation;
    const target = document.createElement('div');
    document.body.appendChild(target);
    mounted.push(mount(DualParamRenderer, {
      target, props: { binding, issuedStatusPresentation: presentation },
    }));
    flushSync();

    expect(ownerViewReads).toBe(0);
    expect(presentation.rf.format).toHaveBeenCalledOnce();
    expect(presentation.sql.format).toHaveBeenCalledOnce();
    expect(acceptRf).toHaveBeenCalledExactlyOnceWith(
      'rf:submitted:Submitting: 0.55',
    );
    expect(acceptSql).toHaveBeenCalledExactlyOnceWith(
      'sql:failed:Failed: 0.3',
    );
    expect(target.querySelectorAll('[data-control-feedback-status]')).toHaveLength(0);

    state.input = commandPairInput({}, {});
    flushSync();
    expect(acceptRf).toHaveBeenLastCalledWith(null);
    expect(acceptSql).toHaveBeenLastCalledWith(null);
    expect(presentation.rf.format).toHaveBeenCalledOnce();
    expect(presentation.sql.format).toHaveBeenCalledOnce();
  });

  it('preserves default local output for actual stateful pair announcements', () => {
    const input = commandPairInput({
      target: 0.55, phase: 'submitted', busy: true,
      lifecycleId: 'rf-1', transitionId: 'rf-submitted',
    }, {
      requestedTarget: 0.3, phase: 'failed',
      outcome: { phase: 'failed', error: 'radio rejected' },
      lifecycleId: 'sql-1', transitionId: 'sql-failed',
    });
    const pair = createContinuousPair(() => input, nativeRangeContinuousPairPolicy);
    const target = document.createElement('div');
    document.body.appendChild(target);
    mounted.push(mount(DualParamRenderer, { target, props: { binding: pair } }));
    flushSync();

    expect([...target.querySelectorAll('[data-control-feedback-status]')]
      .map(status => status.textContent)).toEqual(['Submitting: 0.55', 'Failed: 0.3']);
  });

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
    control.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    control.dispatchEvent(new WheelEvent('wheel', { bubbles: true, cancelable: true, deltaY: -1 }));
    control.dispatchEvent(new WheelEvent('wheel', {
      bubbles: true, cancelable: true, deltaY: 1, shiftKey: true,
    }));
    expect(first.lease.wheel).toHaveBeenCalledExactlyOnceWith({ direction: 1, fine: false, steps: 1 });
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
        currentStatus: supplied.phase === 'idle' ? null : announcement,
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
