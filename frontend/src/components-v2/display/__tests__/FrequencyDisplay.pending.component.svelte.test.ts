import { afterEach, describe, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import FrequencyDisplayPendingHarness from './FrequencyDisplayPendingHarness.svelte';

/**
 * MOR-2911 (MOR-2215 audit F5) — the phone's passive frequency readout shows
 * the in-flight tune target. The facade must carry `pendingDisplayHz` into
 * the same projection the desktop's seat path uses, so while a `set_freq` is
 * in flight the digits show where the burst is heading, and fall back to the
 * confirmed digits the moment the pending target clears (radio confirms or
 * refuses — both drop `getPendingFrequencyHz` to null). Phone rules (owner):
 * minimal UI, no new chrome — the passive readout gains no interactive
 * attributes while doing so.
 */
const mounted: ReturnType<typeof mount>[] = [];
let setFrequency!: (freq: number, pending: number | null) => void;

function mountHarness(): HTMLElement {
  const target = document.createElement('div');
  document.body.appendChild(target);
  const component = mount(FrequencyDisplayPendingHarness, { target });
  mounted.push(component);
  setFrequency = component.setFrequency;
  flushSync();
  return target;
}

function readoutText(target: HTMLElement): string {
  return target.querySelector<HTMLElement>('.freq')!.textContent?.replace(/\s/g, '') ?? '';
}

afterEach(() => {
  mounted.forEach((component) => unmount(component));
  mounted.length = 0;
  document.body.innerHTML = '';
});

describe('phone frequency readout shows the tune target in flight (MOR-2911)', () => {
  it('renders the pending target digits instead of the confirmed frequency while a tune is in flight', () => {
    const target = mountHarness();
    setFrequency(14_235_000, 14_260_100);
    flushSync();

    expect(readoutText(target)).toBe('14.260.100');
  });

  it('returns to the confirmed digits when the pending target clears (radio confirms or refuses)', () => {
    const target = mountHarness();
    setFrequency(14_235_000, 14_260_100);
    flushSync();
    expect(readoutText(target)).toBe('14.260.100');

    setFrequency(14_235_000, null);
    flushSync();
    expect(readoutText(target)).toBe('14.235.000');
  });

  it('keeps the pending display chrome-free: passive, compact, no interactive attributes', () => {
    const target = mountHarness();
    setFrequency(14_235_000, 14_260_100);
    flushSync();
    const root = target.querySelector<HTMLElement>('.freq')!;

    expect(root.classList.contains('compact')).toBe(true);
    expect(root.classList.contains('interactive')).toBe(false);
    expect(root.hasAttribute('tabindex')).toBe(false);
    expect(root.hasAttribute('role')).toBe(false);
    expect(root.hasAttribute('aria-disabled')).toBe(false);
    expect(root.hasAttribute('data-vfo-freq')).toBe(false);
  });
});
