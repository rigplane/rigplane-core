import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { DisplayValue } from '../../../semantic/radio-display-model';
import { meterFill } from '../lcd-display-helpers';

const smoothing = vi.hoisted(() => {
  const instances: Array<{
    update: ReturnType<typeof vi.fn>;
    start: ReturnType<typeof vi.fn>;
    stop: ReturnType<typeof vi.fn>;
  }> = [];
  const createSmoother = vi.fn((_attack: number, _release: number) => {
    let value = 0.75;
    const instance = {
      update: vi.fn((next: number) => { value = next; }),
      start: vi.fn(),
      stop: vi.fn(),
    };
    instances.push(instance);
    return {
      get value() { return value; },
      update: instance.update,
      start: instance.start,
      stop: instance.stop,
    };
  });
  return { createSmoother, instances };
});

vi.mock('$lib/utils/smoothing.svelte', () => ({
  createSmoother: smoothing.createSmoother,
}));

import LcdLinearSMeter from '../LcdLinearSMeter.svelte';

let component: ReturnType<typeof mount> | null = null;

afterEach(() => {
  if (component) unmount(component);
  component = null;
  document.body.innerHTML = '';
  smoothing.createSmoother.mockClear();
  smoothing.instances.length = 0;
});

function render(field: DisplayValue<number>): HTMLElement {
  const target = document.createElement('div');
  document.body.appendChild(target);
  component = mount(LcdLinearSMeter, { target, props: { field } });
  flushSync();
  return target;
}

describe('LcdLinearSMeter ballistics wiring', () => {
  it('uses Standard/SDR attack and release constants and starts the shared smoother', () => {
    const field = { state: 'known', value: 10 } as const;    render(field);

    expect(smoothing.createSmoother).toHaveBeenCalledWith(0.06, 0.1);
    expect(smoothing.instances[0]?.update).toHaveBeenCalledWith(meterFill(field));
    expect(smoothing.instances[0]?.start).toHaveBeenCalledOnce();
  });

  it.each([
    { state: 'unknown' } as const,
    { state: 'unsupported' } as const,
  ])('keeps $state meter facts at a truthful zero target', (field) => {
    const target = render(field);

    expect(target.querySelector('.s-meter')?.getAttribute('data-state')).toBe(field.state);
    expect(target.querySelector('.meter-fill')?.getAttribute('style')).toContain('width: 0%');
    expect(smoothing.instances[0]?.update).toHaveBeenCalledWith(0);
  });

  it('stops the smoother on unmount so no presentation callback survives', () => {
    render({ state: 'known', value: -18 });
    const instance = smoothing.instances[0];

    expect(instance?.start).toHaveBeenCalledOnce();
    unmount(component!);
    component = null;
    expect(instance?.stop).toHaveBeenCalledOnce();
  });
});
