/**
 * MOR-2978 — ControlButton's tri-state `pressed` seam.
 *
 * `pressed` true / false / undefined renders `aria-pressed` "true" / "false" /
 * absent (Svelte omits the attribute for `undefined`) — the same omit-never-
 * "false" rule `pressedOf` pins for semantic surfaces. `active` alone never
 * produces `aria-pressed`: it collapses an unread reading to false, so
 * deriving `pressed` from it would announce an unconfirmed off-state.
 * HardwareButton passes `pressed` through to the native button.
 */
import { describe, it, expect, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import ControlButton from '../ControlButton.svelte';
import HardwareButton from '../HardwareButton.svelte';

const mounted: ReturnType<typeof mount>[] = [];

afterEach(() => {
  while (mounted.length) unmount(mounted.pop()!);
  document.body.innerHTML = '';
});

function render(
  Component: Parameters<typeof mount>[0],
  props: Record<string, unknown>,
): HTMLButtonElement {
  const target = document.createElement('div');
  document.body.appendChild(target);
  mounted.push(mount(Component, { target, props }));
  flushSync();
  return target.querySelector('button') as HTMLButtonElement;
}

describe('ControlButton pressed (MOR-2978)', () => {
  it.each([
    [true, 'true'],
    [false, 'false'],
  ] as const)('renders aria-pressed "%s" for pressed=%s', (pressed, expected) => {
    expect(render(ControlButton, { pressed }).getAttribute('aria-pressed')).toBe(expected);
  });

  it('omits aria-pressed when pressed is undefined', () => {
    expect(render(ControlButton, {}).hasAttribute('aria-pressed')).toBe(false);
  });

  it('never renders aria-pressed from active alone', () => {
    for (const active of [true, false]) {
      expect(render(ControlButton, { active }).hasAttribute('aria-pressed')).toBe(false);
    }
  });

  it('renders pressed independently of active', () => {
    const el = render(ControlButton, { active: false, pressed: true });
    expect(el.getAttribute('aria-pressed')).toBe('true');
    expect(el.dataset.active).toBe('false');
  });
});

describe('HardwareButton pressed passthrough (MOR-2978)', () => {
  it.each([
    [true, 'true'],
    [false, 'false'],
  ] as const)('renders aria-pressed "%s" for pressed=%s', (pressed, expected) => {
    expect(render(HardwareButton, { pressed }).getAttribute('aria-pressed')).toBe(expected);
  });

  it('omits aria-pressed when pressed is undefined', () => {
    expect(render(HardwareButton, { active: true }).hasAttribute('aria-pressed')).toBe(false);
  });
});
