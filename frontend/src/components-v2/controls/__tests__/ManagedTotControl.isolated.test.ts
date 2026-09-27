import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { flushSync, mount, unmount } from 'svelte';
import { ManagedAppTxHarness } from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';
import type { ManagedAppTxController } from '$lib/runtime/tx-controller/managed-app-host';

const txHost = { current: undefined as unknown as ManagedAppTxController };

vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({
  getManagedAppTxController: () => txHost.current,
}));

import ManagedTotControl from '../ManagedTotControl.svelte';

let tx: ManagedAppTxHarness;
let component: ReturnType<typeof mount> | null;

function mountControl(): HTMLElement {
  const target = document.createElement('div');
  document.body.appendChild(target);
  component = mount(ManagedTotControl, { target });
  flushSync();
  return target;
}

beforeEach(() => {
  tx = new ManagedAppTxHarness({ configuredSeconds: 180, remainingMs: 42_100 });
  txHost.current = tx.controller;
  component = null;
});

afterEach(() => {
  if (component) unmount(component);
  expect(tx.listenerCount()).toBe(0);
  document.body.innerHTML = '';
});

describe('managed TOT control', () => {
  it('separates canonical configuration, live countdown, and local draft', () => {
    const target = mountControl();
    expect(target.querySelector('[data-testid="managed-tot-current"]')?.textContent).toContain('180s');
    expect(target.querySelector('[data-testid="managed-tot-countdown"]')?.textContent).toContain('43s');

    const input = target.querySelector<HTMLInputElement>('[data-testid="managed-tot-draft"]')!;
    input.value = '240';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();

    expect(target.querySelector('[data-testid="managed-tot-current"]')?.textContent).toContain('180s');
    expect(input.value).toBe('240');
    expect(tx.trace()).toEqual([]);
  });

  it('submits through the facade without optimistically changing server truth', async () => {
    const target = mountControl();
    const input = target.querySelector<HTMLInputElement>('[data-testid="managed-tot-draft"]')!;
    input.value = '240';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    target.querySelector<HTMLButtonElement>('[data-testid="managed-tot-save"]')!.click();
    await Promise.resolve();
    flushSync();

    expect(tx.trace()).toEqual([{ transport: 'http', operation: 'set_tot', configuredSeconds: 240 }]);
    expect(target.querySelector('[data-testid="managed-tot-current"]')?.textContent).toContain('180s');

    tx.emitServerSnapshot({ configuredSeconds: 240, remainingMs: 60_000 });
    flushSync();
    expect(target.querySelector('[data-testid="managed-tot-current"]')?.textContent).toContain('240s');
  });

  it('submits blank as disabled and never renders stale null as OFF', async () => {
    const target = mountControl();
    const input = target.querySelector<HTMLInputElement>('[data-testid="managed-tot-draft"]')!;
    input.value = '';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    target.querySelector<HTMLButtonElement>('[data-testid="managed-tot-save"]')!.click();
    await Promise.resolve();
    expect(tx.trace()).toEqual([{ transport: 'http', operation: 'set_tot', configuredSeconds: null }]);
    expect(target.querySelector('[data-testid="managed-tot-current"]')?.textContent).toContain('180s');

    tx.emitServerSnapshot({ configuredSeconds: null, remainingMs: null });
    flushSync();
    expect(target.querySelector('[data-testid="managed-tot-current"]')?.textContent).toContain('LIMIT OFF');

    tx.emitStale();
    flushSync();
    // MOR-2674 (owner rule, 2026-09-26): stale is unread — unlit, never a
    // dash run, and never the stale 'OFF' painted back.
    const current = target.querySelector<HTMLElement>('[data-testid="managed-tot-current"]')!;
    expect(current.textContent).not.toContain('-');
    const live = current.querySelector('.tot-live')!;
    expect(live.textContent!.trim()).toBe('LIMIT');
  });

  it('renders an unread limit unlit in a reserved slot — no dash run, the reservation derived from the formatter, and a wider known value grows the box', () => {
    const target = mountControl();
    tx.emitStale();
    flushSync();
    const current = target.querySelector<HTMLElement>('[data-testid="managed-tot-current"]')!;
    expect(current.textContent).not.toContain('-');
    // The label stays; the value is empty (unlit LCD segment).
    expect(current.querySelector('.tot-live')!.textContent!.trim()).toBe('LIMIT');
    // The reservation is derived from the formatter, not a hand number:
    // the widest of 'OFF' and an integer seconds value of up to 4 digits.
    const format = (seconds: number | null) => (seconds === null ? 'LIMIT OFF' : `LIMIT ${seconds}s`);
    const widest = Math.max(format(null).length, format(9999).length);
    // Known OFF is real and renders through the same slot.
    tx.emitServerSnapshot({ configuredSeconds: null, remainingMs: null });
    flushSync();
    const offLive = current.querySelector('.tot-live')!.textContent!.trim();
    expect(offLive).toBe('LIMIT OFF');
    // A known seconds text also stays inside the reservation.
    tx.emitServerSnapshot({ configuredSeconds: 180, remainingMs: 42_100 });
    flushSync();
    const knownLive = current.querySelector('.tot-live')!.textContent!.trim();
    expect(knownLive).toBe('LIMIT 180s');
    expect(knownLive.length).toBeLessThanOrEqual(widest);
    // A wider legal value (TOT accepts any positive finite number) renders
    // in full next to the REMAINING span: the live text is an ordinary
    // inline sibling, never an absolute overlay that would overlap it.
    tx.emitServerSnapshot({ configuredSeconds: 12345, remainingMs: 42_100 });
    flushSync();
    const wideLive = current.querySelector('.tot-live')!.textContent!.trim();
    expect(wideLive).toBe('LIMIT 12345s');
    expect(wideLive.length).toBeGreaterThan(widest);
    expect(current.querySelector('[data-testid="managed-tot-countdown"]')?.textContent).toContain('43s');
    // Static pins: the reservation is a minimum (min-inline-size) on the
    // live element itself, exactly the derived width; no absolute
    // positioning anywhere on it. (jsdom never applies scoped styles —
    // pattern follows StandardFrequencyReadout.)
    const source = readFileSync('src/components-v2/controls/ManagedTotControl.svelte', 'utf8');
    const css = (source.match(/<style>([\s\S]*?)<\/style>/)?.[1] ?? '').replace(/\/\*[\s\S]*?\*\//g, '');
    const reservation = css.match(/\.tot-live\s*\{[^}]*min-inline-size\s*:\s*(\d+)ch/);
    expect(reservation, '.tot-live must reserve a minimum inline size').not.toBeNull();
    expect(Number(reservation![1])).toBe(widest);
    expect(css).not.toMatch(/\.tot-live\s*\{[^}]*position\s*:\s*absolute/);
  });

  it('accepts positive fractional drafts through the facade', async () => {
    const target = mountControl();
    const input = target.querySelector<HTMLInputElement>('[data-testid="managed-tot-draft"]')!;
    input.value = '1.5';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    target.querySelector<HTMLButtonElement>('[data-testid="managed-tot-save"]')!.click();
    await Promise.resolve();
    flushSync();
    expect(tx.trace()).toEqual([{ transport: 'http', operation: 'set_tot', configuredSeconds: 1.5 }]);

  });

  it('rejects non-positive drafts before facade submission', () => {
    const target = mountControl();
    const input = target.querySelector<HTMLInputElement>('[data-testid="managed-tot-draft"]')!;
    // A native number input normalizes textual NaN/Infinity to blank before
    // Svelte sees it; the production finite guard remains deliberate for
    // programmatic/edge values. Exercise the two invalid values a user can
    // actually submit through this control.
    for (const value of ['0', '-1']) {
      input.value = value;
      input.dispatchEvent(new Event('input', { bubbles: true }));
      flushSync();
      target.querySelector<HTMLButtonElement>('[data-testid="managed-tot-save"]')!.click();
      flushSync();
      expect(target.querySelector('[data-testid="managed-tot-error"]')).not.toBeNull();
    }
    expect(tx.trace()).toEqual([]);
  });
});
