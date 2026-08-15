import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import ValueControl from '../ValueControl.svelte';

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
  return { state, target };
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

describe('ValueControl controlled HBar rendering', () => {
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
});
