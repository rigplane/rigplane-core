import { afterEach, describe, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { ComponentProps } from 'svelte';
import IcomTouchNeedleMeter from '../IcomTouchNeedleMeter.svelte';

let mounted: ReturnType<typeof mount>[] = [];
let roots: HTMLElement[] = [];

function render(props: ComponentProps<typeof IcomTouchNeedleMeter>): HTMLElement {
  const target = document.createElement('div');
  document.body.appendChild(target);
  roots.push(target);
  mounted.push(mount(IcomTouchNeedleMeter, { target, props }));
  flushSync();
  return target;
}

afterEach(() => {
  mounted.forEach((component) => unmount(component));
  roots.forEach((root) => root.remove());
  mounted = [];
  roots = [];
});

describe('IcomTouchNeedleMeter state grammar', () => {
  it('renders one profile-normalized known pointer and the preformatted value verbatim', () => {
    const root = render({
      value: 0.34,
      displayValue: '34 W',
      selectedScale: 'Po',
      structural: true,
      operational: true,
      relevant: true,
    });

    const meter = root.querySelector('[data-testid="icom-touch-needle-meter"]');
    expect(meter?.getAttribute('data-state')).toBe('known');
    expect(meter?.getAttribute('data-selected-scale')).toBe('Po');
    expect(root.querySelectorAll('[data-meter-pointer]')).toHaveLength(1);
    expect(root.querySelector('[data-meter-display-value]')?.textContent).toBe('34 W');
    expect(root.textContent).toContain('SWR');
    expect(root.textContent).toContain('COMP');
  });

  it('keeps scale geometry but removes the pointer for supported-unread state', () => {
    const root = render({
      value: null,
      displayValue: null,
      selectedScale: 'Po',
      structural: true,
      operational: false,
      relevant: true,
    });

    const meter = root.querySelector('[data-testid="icom-touch-needle-meter"]');
    expect(meter?.getAttribute('data-state')).toBe('unknown');
    expect(root.querySelector('[data-meter-artwork]')).not.toBeNull();
    expect(root.querySelector('[data-meter-pointer]')).toBeNull();
    expect(root.querySelector('[data-meter-unknown]')?.textContent).toBe('?');
    expect(root.querySelector('[data-meter-display-value]')).toBeNull();
  });

  it('preserves the box but hides impossible artwork when structurally unsupported', () => {
    const root = render({
      value: null,
      displayValue: null,
      selectedScale: 'Po',
      structural: false,
      operational: false,
      relevant: false,
    });

    const meter = root.querySelector('[data-testid="icom-touch-needle-meter"]');
    expect(meter?.getAttribute('data-state')).toBe('unsupported');
    expect(meter?.getAttribute('aria-hidden')).toBe('true');
    expect(root.querySelector('[data-meter-artwork]')?.getAttribute('visibility')).toBe('hidden');
    expect(root.querySelector('[data-meter-pointer]')).toBeNull();
  });

  it('keeps a known but irrelevant reading distinct from unknown', () => {
    const root = render({
      value: 0.7,
      displayValue: '70 W',
      selectedScale: 'Po',
      structural: true,
      operational: true,
      relevant: false,
    });

    const meter = root.querySelector('[data-testid="icom-touch-needle-meter"]');
    expect(meter?.getAttribute('data-state')).toBe('known');
    expect(meter?.getAttribute('data-relevant')).toBe('false');
    expect(root.querySelector('[data-meter-pointer]')).not.toBeNull();
  });

  it('clamps out-of-domain fixture values without moving beyond the scale', () => {
    const low = render({ value: -1, displayValue: 'low', selectedScale: 'Po' });
    const high = render({ value: 2, displayValue: 'high', selectedScale: 'Po' });
    const transform = (root: HTMLElement) =>
      root.querySelector('[data-meter-pointer]')?.getAttribute('transform');

    expect(transform(low)).toBe('rotate(-160 280 224)');
    expect(transform(high)).toBe('rotate(-25 280 224)');
  });
});
