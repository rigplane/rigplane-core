import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { ComponentProps } from 'svelte';

import AttenuatorControl from '../AttenuatorControl.svelte';

let components: ReturnType<typeof mount>[] = [];

function mountControl(props: ComponentProps<typeof AttenuatorControl>) {
  const target = document.createElement('div');
  document.body.appendChild(target);
  const component = mount(AttenuatorControl, { target, props });
  flushSync();
  components.push(component);
  return target;
}

beforeEach(() => {
  components = [];
});

afterEach(() => {
  components.forEach((component) => unmount(component));
  document.body.innerHTML = '';
});

describe('AttenuatorControl', () => {
  it('renders all available values directly when the attenuator only has two positions', () => {
    const target = mountControl({
      values: [0, 20],
      selected: 0,
      onchange: vi.fn(),
    });

    const segments = Array.from(target.querySelectorAll('.button-grid .v2-control-button')).map((button) => button.textContent?.trim());
    expect(segments).toEqual(['OFF', '20dB']);
    // No overflow wrapper div should exist
    expect(target.querySelector('.att-control > div:not(.button-grid)')).toBeNull();
  });

  it('renders quick OFF/6/12/18 picks for dense attenuator ladders', () => {
    const target = mountControl({
      values: [0, 3, 6, 9, 12, 15, 18],
      selected: 0,
      onchange: vi.fn(),
    });

    const segments = Array.from(target.querySelectorAll('.button-grid .v2-control-button')).map((button) => button.textContent?.trim());
    expect(segments).toEqual(['OFF', '6dB', '12dB', '18dB']);
    const radios = [...target.querySelectorAll('.button-grid [role="radio"]')];
    expect(radios.map((radio) => radio.getAttribute('aria-checked')))
      .toEqual(['true', 'false', 'false', 'false']);
    expect(target.querySelector('.button-grid')?.getAttribute('role')).toBe('radiogroup');
    const moreButton = target.querySelector<HTMLButtonElement>('.att-control > div:not(.button-grid) .v2-control-button');
    expect(moreButton?.textContent?.trim()).toBe('MORE');
    expect(moreButton?.getAttribute('aria-expanded')).toBe('false');
  });

  it('shows the selected overflow value on the more button when ATT is on a non-quick step', () => {
    const target = mountControl({
      values: [0, 3, 6, 9, 12, 15, 18],
      selected: 15,
      onchange: vi.fn(),
    });

    const moreButton = target.querySelector<HTMLButtonElement>('.att-control > div:not(.button-grid) .v2-control-button');
    expect(moreButton?.textContent?.trim()).toBe('15dB');
    expect(moreButton?.dataset.active).toBe('true');
  });

  it('opens the overflow menu and selects a non-quick value', () => {
    const onchange = vi.fn();
    const target = mountControl({
      values: [0, 3, 6, 9, 12, 15, 18],
      selected: 0,
      onchange,
    });

    (target.querySelector('.att-control > div:not(.button-grid) .v2-control-button') as HTMLButtonElement).click();
    flushSync();

    const menuItems = Array.from(document.querySelectorAll('.menu-grid .v2-control-button'));
    expect(menuItems.map((item) => item.textContent?.trim())).toEqual(['3dB', '9dB', '15dB']);

    (menuItems[2] as HTMLButtonElement).click();
    flushSync();

    expect(onchange).toHaveBeenCalledWith(15);
    expect(document.querySelector('.menu')).toBeNull();
  });
});
