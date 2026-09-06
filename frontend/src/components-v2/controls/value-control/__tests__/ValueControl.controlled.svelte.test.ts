import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { SvelteMap } from 'svelte/reactivity';
import ValueControl from '../ValueControl.svelte';
import HBarRenderer from '../HBarRenderer.svelte';
import DiscreteRenderer from '../DiscreteRenderer.svelte';
import { professionalSkin } from '../skins';
import {
  createBipolarContinuousScalarPolicy,
  createContinuousScalar,
  createDiscreteContinuousScalarPolicy,
  createHBarContinuousScalarPolicy,
  createKnobContinuousScalarPolicy,
  type CommandScalarFeedback,
  type ContinuousScalarBinding,
  type ContinuousScalarInput,
} from '../../../../primitives/scalar/continuous-scalar.svelte';

let components: ReturnType<typeof mount>[] = [];
let roots: HTMLElement[] = [];

function mountReactive(props: Record<string, unknown>) {
  const state = $state(props);
  const target = document.createElement('div');
  document.body.appendChild(target);
  roots.push(target);
  // The reactive prop object is the parent canonical source for this mounted witness.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const component = mount(ValueControl as any, { target, props: state });
  components.push(component);
  flushSync();
  return { component, state, target };
}

function slider(target: HTMLElement): HTMLElement {
  return target.querySelector('[role="slider"]') as HTMLElement;
}

function visibleValue(target: HTMLElement): string | null {
  return target.querySelector('.vc-value')?.textContent ?? null;
}

function fill(target: HTMLElement): string | null {
  return target.querySelector('.vc-hbar')?.getAttribute('style') ?? null;
}

function pointer(target: HTMLElement, x: number) {
  const control = slider(target) as HTMLElement & { setPointerCapture?: (pointerId: number) => void };
  control.setPointerCapture = vi.fn();
  const event = new PointerEvent('pointerdown', { bubbles: true, clientX: x, pointerId: 1 });
  control.dispatchEvent(event);
}

beforeEach(() => {
  components = [];
  roots = [];
});

afterEach(() => {
  components.forEach((component) => { void unmount(component); });
  roots.forEach((root) => root.remove());
});

const baseProps = {
  value: 20,
  min: 0,
  max: 100,
  step: 10,
  label: 'Controlled',
  renderer: 'hbar' as const,
  debounceMs: 0,
};

const commandFeedback = (
  over: Partial<CommandScalarFeedback> = {},
): Readonly<CommandScalarFeedback> => ({
  confirmed: 20,
  target: 30,
  requestedTarget: 30,
  phase: 'awaiting-confirmation',
  busy: true,
  availability: 'available',
  outcome: null,
  lifecycleId: 'life-1',
  transitionId: 'transition-1',
  sessionEpoch: 7,
  scope: { control: 'filter-width', receiver: 0 },
  repeatPolicy: 'latest-target-wins',
  ...over,
});

function commandBinding(
  request: (value: number) => void,
  over: Partial<CommandScalarFeedback> = {},
): ContinuousScalarBinding {
  const feedback = commandFeedback(over);
  const input = (): ContinuousScalarInput => ({
    evidence: 'command-feedback',
    command: 'set_filter_width',
    domain: { min: 0, max: 100, step: 10, defaultValue: 50, fineStepDivisor: 10 },
    enabled: true,
    request,
    feedback,
  });
  return createContinuousScalar(
    input,
    createHBarContinuousScalarPolicy({
      preview: 'confirmed', debounceMs: 0, describeTarget: (value) => `${value} Hz`,
    }),
  );
}

function readingBinding(value: number, request: (value: number) => void): ContinuousScalarBinding {
  return createContinuousScalar(
    () => ({
      evidence: 'reading',
      reading: { status: 'known', value },
      ownerKey: `external-${value}`,
      domain: { min: 0, max: 100, step: 10, defaultValue: 50, fineStepDivisor: 10 },
      enabled: true,
      request,
    }),
    createHBarContinuousScalarPolicy({ preview: 'optimistic', debounceMs: 0 }),
  );
}

function bipolarBinding(value: number, request: (value: number) => void): ContinuousScalarBinding {
  return createContinuousScalar(
    () => ({
      evidence: 'reading',
      reading: { status: 'known', value },
      ownerKey: `external-bipolar-${value}`,
      domain: { min: -100, max: 100, step: 5, defaultValue: 0, fineStepDivisor: 10, keyboardStep: 50 },
      enabled: true,
      request,
    }),
    createBipolarContinuousScalarPolicy({ debounceMs: 0 }),
  );
}

function discreteBinding(value: number, request: (value: number) => void): ContinuousScalarBinding {
  return createContinuousScalar(
    () => ({
      evidence: 'reading',
      reading: { status: 'known', value },
      ownerKey: `external-discrete-${value}`,
      domain: { min: 0, max: 10, step: 1, defaultValue: null, fineStepDivisor: 10 },
      enabled: true,
      request,
    }),
    createDiscreteContinuousScalarPolicy({ debounceMs: 0 }),
  );
}

function knobCommandBinding(
  request: (value: number) => void,
  over: Partial<CommandScalarFeedback> = {},
): ContinuousScalarBinding {
  const feedback = commandFeedback(over);
  return createContinuousScalar(
    () => ({
      evidence: 'command-feedback',
      command: 'set_filter_width',
      domain: { min: 0, max: 100, step: 10, defaultValue: 50, fineStepDivisor: 10 },
      enabled: true,
      request,
      feedback,
    }),
    createKnobContinuousScalarPolicy({
      debounceMs: 0,
      describeTarget: (value) => `${value} Hz`,
    }),
  );
}

describe('ValueControl controlled HBar rendering', () => {
  it.each([
    ['built-in', undefined],
    ['selected custom HBar', { name: 'test-hbar', hbar: HBarRenderer }],
  ] as const)('forwards semantic value projection through the %s route', (_route, skin) => {
    const request = vi.fn();
    const binding = createContinuousScalar(
      () => ({
        evidence: 'reading' as const,
        reading: { status: 'known' as const, value: 2100 },
        ownerKey: 'nonuniform-width',
        domain: { min: 1800, max: 3000, step: 1, defaultValue: 1800, fineStepDivisor: 1 },
        enabled: true,
        request,
      }),
      createHBarContinuousScalarPolicy({ preview: 'confirmed', debounceMs: 0 }),
    );
    const valueProjection = {
      contextKey: 'USB:[1800,2100,3000]',
      positionOf: (value: number) => value === 2100 ? 0.5 : null,
      valueAt: (position: number) => position < 0.75 ? 2100 : 3000,
    };
    const { target } = mountReactive({
      binding, label: 'Projected width', renderer: 'hbar', skin,
      valueProjection, displayFn: (value: number) => `${value} Hz`,
    });

    expect(fill(target)).toContain('--vc-fill-percent: 50%');
    expect(slider(target).getAttribute('aria-valuenow')).toBe('2100');
    expect(slider(target).getAttribute('aria-valuetext')).toBe('2100 Hz');
    binding.destroy();
  });

  it.each([
    ['built-in', undefined],
    ['selected custom HBar', { name: 'status-hbar', hbar: HBarRenderer }],
  ] as const)('delivers one whole owner-issued status through the %s route', (_route, skin) => {
    const feedback = new SvelteMap([['value', commandFeedback({
      confirmed: 2100, target: null, requestedTarget: 2501, phase: 'failed', busy: false,
      outcome: { phase: 'failed', error: 'radio rejected' }, transitionId: 'width-failed',
    })]]);
    const binding = createContinuousScalar(
      () => ({
        evidence: 'command-feedback' as const,
        command: 'set_filter_width',
        domain: { min: 1800, max: 3000, step: 1, defaultValue: 1800, fineStepDivisor: 1 },
        enabled: true,
        request: vi.fn(),
        feedback: feedback.get('value')!,
      }),
      createHBarContinuousScalarPolicy({ preview: 'confirmed', debounceMs: 0 }),
    );
    const retained = $state({ text: null as string | null });
    let locale = 'en';
    const format = vi.fn(({ view }) =>
      `${locale}:${view.feedback.requestedTarget}/${view.feedback.confirmed}:radio rejected`);
    const accept = vi.fn((text: string | null) => { retained.text = text; });
    const issuedStatusPresentation = {
      get text() { return retained.text; }, format, accept,
    };
    const { state, target } = mountReactive({
      binding, label: 'Projected width', renderer: 'hbar', skin,
      valueProjection: {
        contextKey: 'USB:[1800,2100,3000]',
        positionOf: () => 0.5,
        valueAt: () => 2100,
      },
      issuedStatusPresentation,
    });

    expect(format).toHaveBeenCalledOnce();
    expect(accept).toHaveBeenCalledExactlyOnceWith('en:2501/2100:radio rejected');
    expect(target.querySelectorAll('[data-control-feedback-status]')).toHaveLength(1);
    expect(target.querySelector('[data-control-feedback-status]')?.textContent)
      .toBe('en:2501/2100:radio rejected');

    locale = 'ru';
    state.valueProjection = {
      contextKey: 'AM:[1800,2200,3000]',
      positionOf: () => 0.5,
      valueAt: () => 2200,
    };
    feedback.set('value', commandFeedback({
      confirmed: 2200, target: null, requestedTarget: 2501, phase: 'failed', busy: false,
      outcome: { phase: 'failed', error: 'radio rejected' }, transitionId: 'width-failed',
    }));
    flushSync();

    expect(format).toHaveBeenCalledOnce();
    expect(target.querySelector('[data-control-feedback-status]')?.textContent)
      .toBe('en:2501/2100:radio rejected');
    binding.destroy();
  });

  it('cancels deferred work and releases capture when projection context changes', () => {
    vi.useFakeTimers();
    const request = vi.fn();
    const binding = createContinuousScalar(
      () => ({
        evidence: 'reading' as const,
        reading: { status: 'known' as const, value: 20 },
        ownerKey: 'stable-owner',
        domain: { min: 0, max: 100, step: 10, defaultValue: 0, fineStepDivisor: 10 },
        enabled: true,
        request,
      }),
      createHBarContinuousScalarPolicy({ preview: 'confirmed', debounceMs: 50 }),
    );
    const projection = (contextKey: string) => ({
      contextKey,
      positionOf: (value: number) => value / 100,
      valueAt: (position: number) => position * 100,
    });
    const { state, target } = mountReactive({
      binding, label: 'Contextual', renderer: 'hbar', valueProjection: projection('USB:a'),
    });
    const control = slider(target) as HTMLElement & {
      setPointerCapture: (id: number) => void;
      hasPointerCapture: (id: number) => boolean;
      releasePointerCapture: (id: number) => void;
    };
    control.setPointerCapture = vi.fn();
    control.hasPointerCapture = vi.fn(() => true);
    control.releasePointerCapture = vi.fn();
    vi.spyOn(target.querySelector('.vc-hbar') as HTMLElement, 'getBoundingClientRect')
      .mockReturnValue({ left: 0, width: 100 } as DOMRect);

    control.dispatchEvent(new PointerEvent('pointerdown', {
      bubbles: true, clientX: 40, pointerId: 7,
    }));
    expect(request).toHaveBeenCalledExactlyOnceWith(40);
    request.mockClear();
    state.valueProjection = projection('USB:b');
    flushSync();
    expect(control.releasePointerCapture).toHaveBeenCalledExactlyOnceWith(7);
    control.dispatchEvent(new PointerEvent('pointermove', {
      bubbles: true, clientX: 90, pointerId: 7,
    }));
    expect(request).not.toHaveBeenCalled();

    control.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    state.valueProjection = projection('AM:b');
    flushSync();
    vi.advanceTimersByTime(60);
    expect(request).not.toHaveBeenCalled();
    control.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    vi.advanceTimersByTime(60);
    expect(request).toHaveBeenCalledExactlyOnceWith(30);
    binding.destroy();
    vi.useRealTimers();
  });

  it('renders external command-owner feedback and domain without legacy decoration', () => {
    const pendingBinding = commandBinding(vi.fn());
    const failedBinding = commandBinding(vi.fn(), {
      phase: 'failed',
      busy: false,
      outcome: { phase: 'failed', error: 'radio rejected' },
      transitionId: 'transition-2',
    });
    const { state, target } = mountReactive({
      binding: pendingBinding,
      label: 'External command',
      renderer: 'hbar',
      feedbackPhase: 'legacy-phase',
      feedbackBusy: true,
      feedbackDescription: 'legacy description',
      feedbackStatus: 'legacy status',
    });
    const control = slider(target);

    expect(control.getAttribute('aria-valuemin')).toBe('0');
    expect(control.getAttribute('aria-valuemax')).toBe('100');
    expect(control.getAttribute('aria-valuenow')).toBe('20');
    expect(control.getAttribute('data-command-phase')).toBe('awaiting-confirmation');
    expect(control.getAttribute('aria-busy')).toBe('true');
    const descriptionId = control.getAttribute('aria-describedby');
    expect(target.querySelector(`#${descriptionId}`)?.textContent).toBe('30 Hz');
    expect(target.querySelector('[data-control-feedback-status]')?.textContent)
      .toBe('Awaiting confirmation: 30 Hz');
    expect(target.textContent).not.toContain('legacy description');
    expect(target.textContent).not.toContain('legacy status');

    state.binding = failedBinding;
    flushSync();
    expect(control.getAttribute('data-command-phase')).toBe('failed');
    expect(control.getAttribute('aria-busy')).toBe('false');
    expect(target.querySelector('[data-control-feedback-status]')?.textContent)
      .toBe('Failed: 30 Hz: radio rejected');
    pendingBinding.destroy();
    failedBinding.destroy();
  });

  it('keeps invalid-domain known text while omitting geometry and numeric ARIA', () => {
    const { state, target } = mountReactive({
      ...baseProps,
      max: Number.NaN,
      displayFn: (value: number) => `KNOWN:${value}`,
      onChange: vi.fn(),
    });

    expect(visibleValue(target)).toBe('KNOWN:20');
    expect(fill(target)).toContain('--vc-fill-percent: 0%');
    expect(slider(target).getAttribute('aria-valuemin')).toBeNull();
    expect(slider(target).getAttribute('aria-valuemax')).toBeNull();
    expect(slider(target).getAttribute('aria-valuenow')).toBeNull();
    expect(slider(target).getAttribute('aria-disabled')).toBe('true');

    state.max = 100;
    flushSync();
    expect(fill(target)).toContain('--vc-fill-percent: 20%');
    expect(slider(target).getAttribute('aria-valuenow')).toBe('20');
  });

  it('keeps feedback presentation inert by default on the actual slider', () => {
    const { target } = mountReactive({ ...baseProps, onChange: vi.fn() });
    const control = slider(target);

    expect(control.hasAttribute('data-command-phase')).toBe(false);
    expect(control.hasAttribute('aria-busy')).toBe(false);
    expect(control.hasAttribute('aria-describedby')).toBe(false);
    expect(target.querySelector('[data-control-feedback-status]')).toBeNull();
  });

  it.each([
    {
      label: 'Filter Width',
      description: 'Target 2400 Hz; last confirmed 2300 Hz',
      status: 'Awaiting confirmation: 2400 Hz',
    },
    {
      label: 'Notch Position',
      description: 'Цель 160; последнее подтверждённое 128',
      status: 'Ожидание подтверждения: 160',
    },
  ])('projects caller-authored feedback onto the $label HBar without changing truth', ({
    label, description, status,
  }) => {
    const onChange = vi.fn();
    const { target } = mountReactive({
      ...baseProps,
      label,
      optimistic: false,
      onChange,
      feedbackPhase: 'awaiting-confirmation',
      feedbackBusy: true,
      feedbackDescription: description,
      feedbackStatus: status,
    });
    const control = slider(target);
    const descriptionId = control.getAttribute('aria-describedby');

    expect(control.getAttribute('data-command-phase')).toBe('awaiting-confirmation');
    expect(control.getAttribute('aria-busy')).toBe('true');
    expect(descriptionId).toBeTruthy();
    expect(target.querySelector(`#${descriptionId}`)?.textContent).toBe(description);
    const live = target.querySelector('[data-control-feedback-status]');
    expect(live?.getAttribute('role')).toBe('status');
    expect(live?.getAttribute('aria-live')).toBe('polite');
    expect(live?.getAttribute('aria-atomic')).toBe('true');
    expect(live?.textContent).toBe(status);
    expect(visibleValue(target)).toContain('20');
    expect(fill(target)).toContain('--vc-fill-percent: 20%');
    expect(control.getAttribute('aria-valuenow')).toBe('20');

    control.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    control.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    flushSync();
    expect(onChange).toHaveBeenNthCalledWith(1, 30);
    expect(onChange).toHaveBeenNthCalledWith(2, 30);
    expect(control.getAttribute('aria-valuenow')).toBe('20');
    expect(fill(target)).toContain('--vc-fill-percent: 20%');
  });

  it.each(['confirmed', 'failed', 'timed-out', 'cancelled', 'superseded'])(
    'clears busy for terminal phase %s without changing canonical truth', (phase) => {
      const { state, target } = mountReactive({
        ...baseProps,
        optimistic: false,
        onChange: vi.fn(),
        feedbackPhase: 'awaiting-confirmation',
        feedbackBusy: true,
        feedbackDescription: 'Target 30; last confirmed 20',
        feedbackStatus: 'Awaiting confirmation: 30',
      });

      state.feedbackPhase = phase;
      state.feedbackBusy = false;
      state.feedbackDescription = null;
      state.feedbackStatus = `Terminal: ${phase}`;
      flushSync();

      const control = slider(target);
      expect(control.getAttribute('data-command-phase')).toBe(phase);
      expect(control.getAttribute('aria-busy')).toBe('false');
      expect(control.hasAttribute('aria-describedby')).toBe(false);
      expect(target.querySelector('[data-control-feedback-status]')?.textContent)
        .toBe(`Terminal: ${phase}`);
      expect(control.getAttribute('aria-valuenow')).toBe('20');
      expect(visibleValue(target)).toContain('20');
      expect(fill(target)).toContain('--vc-fill-percent: 20%');
    },
  );

  it('emits a controlled pointer target while display, fill, and ARIA remain canonical', () => {
    const onChange = vi.fn();
    const { target } = mountReactive({ ...baseProps, optimistic: false, onChange });
    vi.spyOn(target.querySelector('.vc-hbar') as HTMLElement, 'getBoundingClientRect')
      .mockReturnValue({ left: 0, width: 100 } as DOMRect);

    pointer(target, 80);
    flushSync();

    expect(onChange).toHaveBeenCalledWith(80);
    expect(visibleValue(target)).toContain('20');
    expect(fill(target)).toContain('--vc-fill-percent: 20%');
    expect(slider(target).getAttribute('aria-valuenow')).toBe('20');
  });

  it('makes a cancelled pointer token inert before a later move event', () => {
    const onChange = vi.fn();
    const { target } = mountReactive({ ...baseProps, onChange });
    vi.spyOn(target.querySelector('.vc-hbar') as HTMLElement, 'getBoundingClientRect')
      .mockReturnValue({ left: 0, width: 100 } as DOMRect);
    const control = slider(target) as HTMLElement & { setPointerCapture?: (pointerId: number) => void };
    control.setPointerCapture = vi.fn();

    control.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, clientX: 50, pointerId: 1 }));
    control.dispatchEvent(new PointerEvent('pointercancel', { bubbles: true, pointerId: 1 }));
    flushSync();
    expect(visibleValue(target)).toContain('20');
    expect(fill(target)).toContain('--vc-fill-percent: 20%');
    expect(control.getAttribute('aria-valuenow')).toBe('20');
    control.dispatchEvent(new PointerEvent('pointermove', { bubbles: true, clientX: 80, pointerId: 1 }));

    expect(onChange.mock.calls).toEqual([[50]]);
  });

  it.each([
    [42, '42%', '42', '42%'],
    [Number.POSITIVE_INFINITY, '+INF', null, '0%'],
    [Number.NEGATIVE_INFINITY, '-INF', null, '0%'],
    [Number.NaN, 'NAN', null, '0%'],
  ] as const)('projects a %s reading synchronously through the caller formatter', (
    value, expectedText, expectedAria, expectedFill,
  ) => {
    const target = document.createElement('div');
    document.body.appendChild(target);
    roots.push(target);
    const component = mount(ValueControl, { target, props: {
      ...baseProps, value, max: 100, onChange: vi.fn(),
      displayFn: (candidate: number) => Number.isNaN(candidate) ? 'NAN'
        : candidate === Number.POSITIVE_INFINITY ? '+INF'
          : candidate === Number.NEGATIVE_INFINITY ? '-INF' : `${candidate}%`,
    } });
    components.push(component);

    expect(visibleValue(target)).toBe(expectedText);
    expect(slider(target).getAttribute('aria-valuenow')).toBe(expectedAria);
    expect(fill(target)).toContain(`--vc-fill-percent: ${expectedFill}`);
    expect(slider(target).getAttribute('aria-disabled')).toBe(Number.isFinite(value) ? 'false' : 'true');
  });

  it('replaces a nonfinite formatter without changing unknown numeric evidence', () => {
    const { state, target } = mountReactive({
      ...baseProps,
      value: Number.POSITIVE_INFINITY,
      displayFn: (value: number) => value === Number.POSITIVE_INFINITY ? 'POSITIVE' : 'other',
      onChange: vi.fn(),
    });

    expect(visibleValue(target)).toBe('POSITIVE');
    state.displayFn = (value: number) => value === Number.POSITIVE_INFINITY ? '+INF' : 'other';
    flushSync();

    expect(visibleValue(target)).toBe('+INF');
    expect(slider(target).getAttribute('aria-valuenow')).toBeNull();
    expect(slider(target).getAttribute('aria-disabled')).toBe('true');
    expect(fill(target)).toContain('--vc-fill-percent: 0%');
  });

  it('replaces raw and external modes while preserving caller-owned lifetime', async () => {
    const rawRequest = vi.fn();
    const externalRequest = vi.fn();
    const external = readingBinding(70, externalRequest);
    const { component, state, target } = mountReactive({ ...baseProps, onChange: rawRequest });
    const rawSlider = slider(target);

    state.binding = external;
    flushSync();
    expect(visibleValue(target)).toContain('70');
    rawSlider.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    expect(rawRequest).not.toHaveBeenCalled();

    state.binding = undefined;
    state.value = 40;
    flushSync();
    expect(visibleValue(target)).toContain('40');
    const externalLease = external.attachRenderer();
    expect(externalLease.key({ key: 'ArrowRight', fine: false })).toBe(true);
    expect(externalRequest).toHaveBeenCalledWith(80);
    externalLease.dispose();

    components = components.filter((candidate) => candidate !== component);
    await unmount(component);
    const retainedLease = external.attachRenderer();
    expect(retainedLease.beginPointer()).not.toBeNull();
    retainedLease.dispose();
    external.destroy();
  });

  it('detaches a replaced external binding and renders its successor synchronously', () => {
    const firstRequest = vi.fn();
    const secondRequest = vi.fn();
    const first = readingBinding(20, firstRequest);
    const second = readingBinding(80, secondRequest);
    const { state, target } = mountReactive({ binding: first, label: 'Bound', renderer: 'hbar' });
    const retainedSlider = slider(target);

    state.binding = second;
    flushSync();
    expect(visibleValue(target)).toContain('80');
    retainedSlider.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    expect(firstRequest).not.toHaveBeenCalled();
    expect(secondRequest).toHaveBeenCalledWith(90);

    const firstLease = first.attachRenderer();
    expect(firstLease.beginPointer()).not.toBeNull();
    firstLease.dispose();
    first.destroy();
    second.destroy();
  });

  it('uses canonical value for controlled keyboard arithmetic until the parent accepts', () => {
    const onChange = vi.fn();
    const { target } = mountReactive({ ...baseProps, optimistic: false, onChange });

    slider(target).dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    slider(target).dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    flushSync();

    expect(onChange).toHaveBeenNthCalledWith(1, 30);
    expect(onChange).toHaveBeenNthCalledWith(2, 30);
    expect(visibleValue(target)).toContain('20');
  });

  it('keeps a same-value parent update authoritative after a rejected controlled request', () => {
    const onChange = vi.fn();
    const { state, target } = mountReactive({ ...baseProps, optimistic: false, onChange });

    slider(target).dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    state.value = 20;
    flushSync();

    expect(onChange).toHaveBeenCalledWith(30);
    expect(visibleValue(target)).toContain('20');
    expect(fill(target)).toContain('--vc-fill-percent: 20%');
  });

  it('adopts an accepted parent value immediately and uses it for the next keyboard step', () => {
    const onChange = vi.fn();
    const { state, target } = mountReactive({ ...baseProps, optimistic: false, onChange });

    slider(target).dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    state.value = 30;
    flushSync();

    expect(visibleValue(target)).toContain('30');
    expect(fill(target)).toContain('--vc-fill-percent: 30%');
    expect(slider(target).getAttribute('aria-valuenow')).toBe('30');
    slider(target).dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    expect(onChange).toHaveBeenLastCalledWith(40);
  });

  it('preserves immediate optimistic HBar rendering by default', () => {
    const onChange = vi.fn();
    const { target } = mountReactive({ ...baseProps, onChange });

    slider(target).dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    flushSync();

    expect(onChange).toHaveBeenCalledWith(30);
    expect(visibleValue(target)).toContain('30');
    expect(fill(target)).toContain('--vc-fill-percent: 30%');
  });

  it('revokes a live disabled HBar binding even when a key event is forced through', () => {
    const onChange = vi.fn();
    const { state, target } = mountReactive({ ...baseProps, onChange });
    state.disabled = true;
    flushSync();

    slider(target).dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));

    expect(slider(target).getAttribute('aria-disabled')).toBe('true');
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe('ValueControl controlled Bipolar rendering', () => {
  it('cancels a Bipolar pointer draft and makes its retained move inert', () => {
    const onChange = vi.fn();
    const { target } = mountReactive({
      value: 0,
      min: -100,
      max: 100,
      step: 5,
      label: 'Pointer',
      renderer: 'bipolar',
      debounceMs: 0,
      onChange,
    });
    const control = slider(target) as HTMLElement & {
      setPointerCapture?: (pointerId: number) => void;
      releasePointerCapture?: (pointerId: number) => void;
    };
    control.setPointerCapture = vi.fn();
    control.releasePointerCapture = vi.fn();
    vi.spyOn(target.querySelector('.vc-bipolar') as HTMLElement, 'getBoundingClientRect')
      .mockReturnValue({ left: 0, width: 100 } as DOMRect);

    control.dispatchEvent(new PointerEvent('pointerdown', {
      bubbles: true, clientX: 75, pointerId: 1,
    }));
    control.dispatchEvent(new PointerEvent('pointercancel', { bubbles: true, pointerId: 1 }));
    control.dispatchEvent(new PointerEvent('pointermove', {
      bubbles: true, clientX: 100, pointerId: 1,
    }));
    flushSync();

    expect(onChange.mock.calls).toEqual([[50]]);
    expect(visibleValue(target)).toBe('0');
  });

  it('reconciles a rejected raw wheel draft when the shared wheel lease expires', () => {
    vi.useFakeTimers();
    const onChange = vi.fn();
    const { target } = mountReactive({
      value: 0,
      min: -100,
      max: 100,
      step: 5,
      label: 'Rejected',
      renderer: 'bipolar',
      debounceMs: 0,
      onChange,
    });
    const control = slider(target);

    control.dispatchEvent(new WheelEvent('wheel', { deltaY: -1, bubbles: true, cancelable: true }));
    flushSync();
    expect(onChange).toHaveBeenCalledExactlyOnceWith(5);
    expect(visibleValue(target)).toBe('+5');

    vi.advanceTimersByTime(300);
    flushSync();
    expect(visibleValue(target)).toBe('0');
    expect(control.getAttribute('aria-valuenow')).toBe('0');
    vi.useRealTimers();
  });

  it('replaces HBar with Bipolar on the same external owner and revokes retained callbacks', () => {
    const request = vi.fn();
    const binding = bipolarBinding(0, request);
    const { state, target } = mountReactive({ binding, label: 'Replaceable', renderer: 'hbar' });
    const retainedHBar = slider(target);

    state.renderer = 'bipolar';
    flushSync();
    expect(target.querySelector('.vc-bipolar')).not.toBeNull();
    expect(visibleValue(target)).toBe('0');

    retainedHBar.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    expect(request).not.toHaveBeenCalled();
    slider(target).dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    expect(request).toHaveBeenCalledExactlyOnceWith(50);

    const retainedLease = binding.attachRenderer();
    expect(retainedLease.beginPointer()).not.toBeNull();
    retainedLease.dispose();
    binding.destroy();
  });

  it('preserves command feedback and announcement identity across Bipolar appearance replacement', () => {
    const binding = commandBinding(vi.fn(), {
      phase: 'failed',
      busy: false,
      outcome: { phase: 'failed', error: 'radio rejected' },
      transitionId: 'failed-bipolar',
    });
    const { state, target } = mountReactive({ binding, label: 'Feedback', renderer: 'hbar' });
    const first = slider(target);
    expect(first.getAttribute('data-command-phase')).toBe('failed');
    expect(target.querySelector('[data-control-feedback-status]')?.textContent)
      .toBe('Failed: 30 Hz: radio rejected');

    state.renderer = 'bipolar';
    flushSync();
    const replacement = slider(target);
    expect(replacement.getAttribute('data-command-phase')).toBe('failed');
    expect(replacement.getAttribute('aria-busy')).toBe('false');
    const descriptionId = replacement.getAttribute('aria-describedby');
    expect(target.querySelector(`#${descriptionId}`)?.textContent).toBe('30 Hz');
    expect(target.querySelector('[data-control-feedback-status]')).toBeNull();
    binding.destroy();
  });

  it.each([
    [Number.POSITIVE_INFINITY, '+INF'],
    [Number.NEGATIVE_INFINITY, '-INF'],
    [Number.NaN, 'NAN'],
  ] as const)('keeps exact legacy formatting for nonfinite Bipolar input %s', (value, expected) => {
    const { target } = mountReactive({
      value,
      min: -100,
      max: 100,
      step: 5,
      label: 'Nonfinite',
      renderer: 'bipolar',
      onChange: vi.fn(),
      displayFn: (candidate: number) => Number.isNaN(candidate) ? 'NAN'
        : candidate === Number.POSITIVE_INFINITY ? '+INF' : '-INF',
    });

    expect(visibleValue(target)).toBe(expected);
    expect(slider(target).getAttribute('aria-valuenow')).toBeNull();
    expect(slider(target).getAttribute('aria-disabled')).toBe('true');
  });

  it('distinguishes a known invalid-domain Bipolar reading from an unknown reading', () => {
    const { state, target } = mountReactive({
      value: 25,
      min: -100,
      max: Number.NaN,
      step: 5,
      label: 'Domain',
      renderer: 'bipolar',
      onChange: vi.fn(),
      displayFn: (candidate: number) => Number.isNaN(candidate) ? 'UNKNOWN' : `KNOWN:${candidate}`,
    });

    expect(visibleValue(target)).toBe('KNOWN:25');
    expect(slider(target).getAttribute('aria-valuenow')).toBeNull();
    state.value = Number.NaN;
    flushSync();
    expect(visibleValue(target)).toBe('UNKNOWN');
    expect(slider(target).getAttribute('aria-disabled')).toBe('true');
  });
});

describe('ValueControl controlled Discrete rendering', () => {
  it('keeps a raw unknown reading undimmed but noninteractive without numeric value ARIA', () => {
    const onChange = vi.fn();
    const { target } = mountReactive({
      value: Number.NaN,
      min: 0,
      max: 100,
      step: 10,
      disabled: false,
      label: 'Unknown raw Discrete',
      renderer: 'discrete',
      onChange,
    });
    const wrapper = target.querySelector('.vc-discrete')!;
    const control = slider(target);

    expect(wrapper.classList.contains('dimmed')).toBe(false);
    expect(wrapper.classList.contains('disabled')).toBe(false);
    expect(wrapper.classList.contains('interaction-disabled')).toBe(true);
    expect(control.getAttribute('aria-valuenow')).toBeNull();
    expect(control.getAttribute('aria-disabled')).toBe('true');
    expect(control.getAttribute('tabindex')).toBe('-1');
    control.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    control.dispatchEvent(new WheelEvent('wheel', { deltaY: -1, bubbles: true }));
    expect(onChange).not.toHaveBeenCalled();
  });

  it('dims an explicitly disabled raw Discrete without changing owner editability', () => {
    const { target } = mountReactive({
      value: 20,
      min: 0,
      max: 100,
      step: 10,
      disabled: true,
      label: 'Disabled raw Discrete',
      renderer: 'discrete',
      onChange: vi.fn(),
    });

    expect(target.querySelector('.vc-discrete')?.classList.contains('dimmed')).toBe(true);
    expect(target.querySelector('.vc-discrete')?.classList.contains('interaction-disabled')).toBe(true);
    expect(slider(target).getAttribute('aria-disabled')).toBe('true');
    expect(slider(target).getAttribute('tabindex')).toBe('-1');
  });

  it('retains default noneditable dimming for a direct external binding', () => {
    const binding = createContinuousScalar(
      () => ({
        evidence: 'reading',
        reading: { status: 'unknown' },
        ownerKey: 'external-unknown-discrete',
        domain: { min: 0, max: 100, step: 10, defaultValue: null, fineStepDivisor: 10 },
        enabled: true,
        request: vi.fn(),
      }),
      createDiscreteContinuousScalarPolicy({ debounceMs: 0 }),
    );
    const { target } = mountReactive({
      binding,
      label: 'External unknown Discrete',
      renderer: 'discrete',
    });

    expect(target.querySelector('.vc-discrete')?.classList.contains('dimmed')).toBe(true);
    expect(target.querySelector('.vc-discrete')?.classList.contains('interaction-disabled')).toBe(true);
    expect(slider(target).getAttribute('aria-disabled')).toBe('true');
    binding.destroy();
  });

  it('keeps pending owner work intact when direct renderer dimming changes', () => {
    vi.useFakeTimers();
    const request = vi.fn();
    const binding = createContinuousScalar(
      () => ({
        evidence: 'reading',
        reading: { status: 'known', value: 20 },
        ownerKey: 'direct-cosmetic-discrete',
        domain: { min: 0, max: 100, step: 10, defaultValue: null, fineStepDivisor: 10 },
        enabled: true,
        request,
      }),
      createDiscreteContinuousScalarPolicy({ debounceMs: 50 }),
    );
    const state = $state({
      binding,
      label: 'Direct cosmetic Discrete',
      dimmed: false,
    });
    const target = document.createElement('div');
    document.body.appendChild(target);
    roots.push(target);
    const component = mount(DiscreteRenderer, { target, props: state });
    components.push(component);
    flushSync();

    try {
      slider(target).dispatchEvent(
        new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }),
      );
      state.dimmed = true;
      flushSync();
      expect(target.querySelector('.vc-discrete')?.classList.contains('dimmed')).toBe(true);
      expect(target.querySelector('.vc-discrete')?.classList.contains('interaction-disabled')).toBe(false);
      expect(slider(target).getAttribute('aria-disabled')).toBe('false');
      expect(slider(target).getAttribute('tabindex')).toBe('0');
      expect(visibleValue(target)).toBe('30');

      vi.advanceTimersByTime(50);
      expect(request).toHaveBeenCalledExactlyOnceWith(30);
    } finally {
      binding.destroy();
      vi.useRealTimers();
    }
  });

  it.each([
    ['optimistic', false],
    ['keyboardStep', 5],
  ] as const)('keeps a pending Discrete request when ignored raw %s changes', (prop, next) => {
    vi.useFakeTimers();
    try {
      const onChange = vi.fn();
      const { state, target } = mountReactive({
        value: 20,
        min: 0,
        max: 100,
        step: 10,
        keyboardStep: 40,
        optimistic: true,
        label: 'Ignored authority input',
        renderer: 'discrete',
        debounceMs: 50,
        onChange,
      });

      slider(target).dispatchEvent(
        new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }),
      );
      state[prop] = next;
      flushSync();
      vi.advanceTimersByTime(50);

      expect(onChange).toHaveBeenCalledExactlyOnceWith(30);
    } finally {
      vi.useRealTimers();
    }
  });

  it('cancels a Discrete pointer draft and reconciles rejected wheel state at expiry', () => {
    vi.useFakeTimers();
    const onChange = vi.fn();
    const { target } = mountReactive({
      value: 4,
      min: 0,
      max: 10,
      step: 1,
      label: 'Discrete lifecycle',
      renderer: 'discrete',
      debounceMs: 0,
      onChange,
    });
    const control = slider(target) as HTMLElement & {
      setPointerCapture?: (pointerId: number) => void;
      releasePointerCapture?: (pointerId: number) => void;
    };
    control.setPointerCapture = vi.fn();
    control.releasePointerCapture = vi.fn();
    vi.spyOn(target.querySelector('.vc-discrete') as HTMLElement, 'getBoundingClientRect')
      .mockReturnValue({ left: 0, width: 100 } as DOMRect);

    control.dispatchEvent(new PointerEvent('pointerdown', {
      bubbles: true, clientX: 80, pointerId: 1,
    }));
    control.dispatchEvent(new PointerEvent('pointercancel', { bubbles: true, pointerId: 1 }));
    control.dispatchEvent(new PointerEvent('pointermove', {
      bubbles: true, clientX: 100, pointerId: 1,
    }));
    flushSync();
    expect(onChange.mock.calls).toEqual([[8]]);
    expect(visibleValue(target)).toBe('4');

    control.dispatchEvent(new WheelEvent('wheel', { deltaY: -1, bubbles: true, cancelable: true }));
    flushSync();
    expect(onChange).toHaveBeenLastCalledWith(5);
    expect(visibleValue(target)).toBe('5');
    vi.advanceTimersByTime(300);
    flushSync();
    expect(visibleValue(target)).toBe('4');
    vi.useRealTimers();
  });

  it('replaces HBar with Discrete on the same external owner and revokes retained callbacks', () => {
    const request = vi.fn();
    const binding = discreteBinding(4, request);
    const { state, target } = mountReactive({ binding, label: 'Replaceable', renderer: 'hbar' });
    const retainedHBar = slider(target);

    state.renderer = 'discrete';
    flushSync();
    expect(target.querySelector('.vc-discrete')).not.toBeNull();
    expect(visibleValue(target)).toBe('4');

    retainedHBar.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    expect(request).not.toHaveBeenCalled();
    slider(target).dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    expect(request).toHaveBeenCalledExactlyOnceWith(5);

    const retainedLease = binding.attachRenderer();
    expect(retainedLease.beginPointer()).not.toBeNull();
    retainedLease.dispose();
    binding.destroy();
  });

  it('preserves full command feedback and one-shot announcement identity in Discrete', () => {
    const binding = commandBinding(vi.fn(), {
      phase: 'failed',
      busy: false,
      outcome: { phase: 'failed', error: 'radio rejected' },
      transitionId: 'failed-discrete',
    });
    const { state, target } = mountReactive({ binding, label: 'Feedback', renderer: 'hbar' });
    expect(target.querySelector('[data-control-feedback-status]')?.textContent)
      .toBe('Failed: 30 Hz: radio rejected');

    state.renderer = 'discrete';
    flushSync();
    const replacement = slider(target);
    expect(replacement.getAttribute('data-command-phase')).toBe('failed');
    expect(replacement.getAttribute('aria-busy')).toBe('false');
    const descriptionId = replacement.getAttribute('aria-describedby');
    expect(target.querySelector(`#${descriptionId}`)?.textContent).toBe('30 Hz');
    expect(target.querySelector('[data-control-feedback-status]')).toBeNull();
    binding.destroy();
  });

  it.each([
    [Number.POSITIVE_INFINITY, '+INF'],
    [Number.NEGATIVE_INFINITY, '-INF'],
    [Number.NaN, 'NAN'],
  ] as const)('keeps exact legacy formatting for nonfinite Discrete input %s', (value, expected) => {
    const { target } = mountReactive({
      value,
      min: 0,
      max: 10,
      step: 1,
      label: 'Nonfinite',
      renderer: 'discrete',
      onChange: vi.fn(),
      displayFn: (candidate: number) => Number.isNaN(candidate) ? 'NAN'
        : candidate === Number.POSITIVE_INFINITY ? '+INF' : '-INF',
    });

    expect(visibleValue(target)).toBe(expected);
    expect(slider(target).getAttribute('aria-valuenow')).toBeNull();
    expect(slider(target).getAttribute('aria-disabled')).toBe('true');
  });

  it('distinguishes known invalid-domain Discrete evidence from an unknown reading', () => {
    const { state, target } = mountReactive({
      value: 4,
      min: 0,
      max: Number.NaN,
      step: 1,
      label: 'Domain',
      renderer: 'discrete',
      onChange: vi.fn(),
      displayFn: (candidate: number) => Number.isNaN(candidate) ? 'UNKNOWN' : `KNOWN:${candidate}`,
    });

    expect(visibleValue(target)).toBe('KNOWN:4');
    expect(slider(target).getAttribute('aria-valuenow')).toBeNull();
    state.value = Number.NaN;
    flushSync();
    expect(visibleValue(target)).toBe('UNKNOWN');
    expect(slider(target).getAttribute('aria-disabled')).toBe('true');
  });
});

describe('ValueControl controlled Knob skins', () => {
  it.each([
    ['standard', undefined],
    ['professional', professionalSkin],
  ] as const)('preserves relative coarse and Shift-fine pointer geometry in %s', (_name, skin) => {
    const onChange = vi.fn();
    const { target } = mountReactive({
      value: 50,
      min: 0,
      max: 100,
      step: 10,
      fineStepDivisor: 10,
      label: 'Relative pointer',
      renderer: 'knob',
      skin,
      onChange,
    });
    const control = slider(target) as HTMLElement & {
      setPointerCapture?: (pointerId: number) => void;
      releasePointerCapture?: (pointerId: number) => void;
    };
    control.setPointerCapture = vi.fn();
    control.releasePointerCapture = vi.fn();

    control.dispatchEvent(new PointerEvent('pointerdown', {
      bubbles: true, clientY: 100, pointerId: 1,
    }));
    control.dispatchEvent(new PointerEvent('pointermove', {
      bubbles: true, clientY: 99, pointerId: 1,
    }));
    control.dispatchEvent(new PointerEvent('pointermove', {
      bubbles: true, clientY: 88, pointerId: 1, shiftKey: true,
    }));

    expect(onChange.mock.calls).toEqual([[70], [51]]);
  });

  it('keeps equivalent wheel traces across appearance and token changes', () => {
    const onChange = vi.fn();
    const { state, target } = mountReactive({
      value: 50,
      min: 0,
      max: 100,
      step: 10,
      label: 'Appearance-only',
      renderer: 'knob',
      onChange,
    });

    slider(target).dispatchEvent(new WheelEvent('wheel', {
      deltaY: -1, bubbles: true, cancelable: true,
    }));
    state.skin = professionalSkin;
    state.arcAngle = 180;
    state.tickCount = 5;
    state.accentColor = '#ff0000';
    flushSync();
    slider(target).dispatchEvent(new WheelEvent('wheel', {
      deltaY: -1, bubbles: true, cancelable: true,
    }));

    expect(onChange.mock.calls).toEqual([[80], [80]]);
  });

  it.each([
    ['standard', undefined],
    ['professional', professionalSkin],
  ] as const)('shows pending command feedback in %s', (_name, skin) => {
    const binding = knobCommandBinding(vi.fn(), {
      phase: 'awaiting-confirmation',
      busy: true,
      transitionId: 'pending-knob',
    });
    const { target } = mountReactive({
      binding,
      label: 'Pending knob',
      renderer: 'knob',
      skin,
    });
    const control = slider(target);

    expect(control.getAttribute('data-command-phase')).toBe('awaiting-confirmation');
    expect(control.getAttribute('aria-busy')).toBe('true');
    expect(target.querySelector('[data-control-feedback-status]')?.textContent)
      .toBe('Awaiting confirmation: 30 Hz');
    binding.destroy();
  });

  it.each([
    ['standard', undefined],
    ['professional', professionalSkin],
  ] as const)('preserves unavailable command evidence in %s', (_name, skin) => {
    const binding = knobCommandBinding(vi.fn(), {
      confirmed: null,
      target: null,
      requestedTarget: null,
      phase: 'unavailable',
      busy: false,
      availability: 'unavailable',
    });
    const { target } = mountReactive({
      binding,
      label: 'Unavailable knob',
      renderer: 'knob',
      skin,
    });

    expect(slider(target).getAttribute('aria-valuenow')).toBeNull();
    expect(slider(target).getAttribute('aria-disabled')).toBe('true');
    binding.destroy();
  });

  it('replaces Standard with Professional while preserving feedback and revoking the old lease', () => {
    const request = vi.fn();
    const binding = knobCommandBinding(request, {
      phase: 'failed',
      busy: false,
      outcome: { phase: 'failed', error: 'radio rejected' },
      transitionId: 'failed-knob',
    });
    const { state, target } = mountReactive({
      binding,
      label: 'Skinned command',
      renderer: 'knob',
    });
    const standard = slider(target);

    expect(target.querySelector('.vc-knob')).not.toBeNull();
    expect(standard.getAttribute('aria-valuenow')).toBe('20');
    expect(standard.getAttribute('data-command-phase')).toBe('failed');
    expect(target.querySelector('[data-control-feedback-status]')?.textContent)
      .toBe('Failed: 30 Hz: radio rejected');

    state.skin = professionalSkin;
    flushSync();
    const professional = slider(target);
    expect(target.querySelector('.pro-knob')).not.toBeNull();
    expect(professional.getAttribute('aria-valuenow')).toBe('20');
    expect(professional.getAttribute('data-command-phase')).toBe('failed');
    expect(professional.getAttribute('aria-busy')).toBe('false');
    const descriptionId = professional.getAttribute('aria-describedby');
    expect(target.querySelector(`#${descriptionId}`)?.textContent).toBe('30 Hz');
    expect(target.querySelector('[data-control-feedback-status]')).toBeNull();

    standard.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    expect(request).not.toHaveBeenCalled();
    professional.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    expect(request).toHaveBeenCalledExactlyOnceWith(30);
    binding.destroy();
  });

  it.each([
    [Number.POSITIVE_INFINITY, '+INF'],
    [Number.NEGATIVE_INFINITY, '-INF'],
    [Number.NaN, 'NAN'],
  ] as const)('keeps exact raw Knob formatting for nonfinite input %s', (value, expected) => {
    const { target } = mountReactive({
      value,
      min: 0,
      max: 100,
      step: 10,
      label: 'Nonfinite Knob',
      renderer: 'knob',
      onChange: vi.fn(),
      displayFn: (candidate: number) => Number.isNaN(candidate) ? 'NAN'
        : candidate === Number.POSITIVE_INFINITY ? '+INF' : '-INF',
    });

    expect(target.querySelector('.vc-knob-value')?.textContent).toBe(expected);
    expect(slider(target).getAttribute('aria-valuenow')).toBeNull();
    expect(slider(target).getAttribute('aria-disabled')).toBe('true');
  });
});

describe('ValueControl raw effective behavior authority', () => {
  it.each([
    ['optimistic', false],
    ['keyboardStep', 25],
  ] as const)('keeps a pending Knob request when ignored raw %s changes', (prop, next) => {
    vi.useFakeTimers();
    try {
      const onChange = vi.fn();
      const { state, target } = mountReactive({
        value: 50,
        min: 0,
        max: 100,
        step: 10,
        optimistic: true,
        keyboardStep: 40,
        label: 'Ignored Knob input',
        renderer: 'knob',
        debounceMs: 50,
        onChange,
      });

      slider(target).dispatchEvent(
        new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }),
      );
      state[prop] = next;
      flushSync();
      vi.advanceTimersByTime(50);

      expect(onChange).toHaveBeenCalledExactlyOnceWith(60);
      expect(target.querySelector('.vc-knob-value')?.textContent).toBe('50');
    } finally {
      vi.useRealTimers();
    }
  });

  it('keeps a pending Bipolar request when ignored raw optimistic changes', () => {
    vi.useFakeTimers();
    try {
      const onChange = vi.fn();
      const { state, target } = mountReactive({
        value: 0,
        min: -100,
        max: 100,
        step: 5,
        optimistic: true,
        label: 'Ignored Bipolar preview',
        renderer: 'bipolar',
        debounceMs: 50,
        onChange,
      });

      slider(target).dispatchEvent(
        new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }),
      );
      state.optimistic = false;
      flushSync();
      vi.advanceTimersByTime(50);

      expect(onChange).toHaveBeenCalledExactlyOnceWith(5);
    } finally {
      vi.useRealTimers();
    }
  });

  it('cancels a pending HBar request when raw optimistic changes effective preview', () => {
    vi.useFakeTimers();
    try {
      const onChange = vi.fn();
      const { state, target } = mountReactive({
        ...baseProps,
        optimistic: true,
        debounceMs: 50,
        onChange,
      });

      slider(target).dispatchEvent(
        new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }),
      );
      state.optimistic = false;
      flushSync();
      vi.advanceTimersByTime(50);

      expect(onChange).not.toHaveBeenCalled();
      expect(visibleValue(target)).toContain('20');
    } finally {
      vi.useRealTimers();
    }
  });
});
