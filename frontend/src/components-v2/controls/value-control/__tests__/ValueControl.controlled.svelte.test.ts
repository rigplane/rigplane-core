import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import ValueControl from '../ValueControl.svelte';
import {
  createContinuousScalar,
  createHBarContinuousScalarPolicy,
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

describe('ValueControl controlled HBar rendering', () => {
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
