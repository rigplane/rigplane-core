import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { flushSync, mount, unmount } from 'svelte';
import { ManagedAppTxHarness } from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';
import type { ManagedAppTxController } from '$lib/runtime/tx-controller/managed-app-host';

const txHost = { current: undefined as unknown as ManagedAppTxController };

vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({
  getManagedAppTxController: () => txHost.current,
}));

import ManagedTotStatusControl from '../ManagedTotStatusControl.svelte';

let tx: ManagedAppTxHarness;
let component: ReturnType<typeof mount> | null;

function mountControl(): HTMLElement {
  const target = document.createElement('div');
  document.body.appendChild(target);
  component = mount(ManagedTotStatusControl, { target });
  flushSync();
  return target;
}

beforeEach(() => {
  tx = new ManagedAppTxHarness({ configuredSeconds: 180, remainingMs: null });
  txHost.current = tx.controller;
  component = null;
});

afterEach(() => {
  if (component) unmount(component);
  expect(tx.listenerCount()).toBe(0);
  document.body.innerHTML = '';
});

describe('managed TOT status control', () => {
  it('is one compact facade-backed status consumer until its editor is opened', () => {
    const target = mountControl();
    expect(target.querySelector('[data-testid="managed-tot-trigger"]')?.textContent).toContain('180s');
    expect(target.querySelector('[data-testid="managed-tot-control"]')).toBeNull();
    expect(tx.listenerCount()).toBe(1);

    target.querySelector<HTMLButtonElement>('[data-testid="managed-tot-trigger"]')!.click();
    flushSync();
    expect(target.querySelector('[data-testid="managed-tot-popover"]')).not.toBeNull();
    expect(target.querySelector('[data-testid="managed-tot-control"]')).not.toBeNull();
    expect(tx.listenerCount()).toBe(2);
  });

  it('updates its readout from facade snapshots and delegates edits to the full control', async () => {
    const target = mountControl();
    tx.emitServerSnapshot({ configuredSeconds: 1.5, remainingMs: null });
    flushSync();
    expect(target.querySelector('[data-testid="managed-tot-trigger"]')?.textContent).toContain('1.5s');

    target.querySelector<HTMLButtonElement>('[data-testid="managed-tot-trigger"]')!.click();
    flushSync();
    const input = target.querySelector<HTMLInputElement>('[data-testid="managed-tot-draft"]')!;
    input.value = '';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    target.querySelector<HTMLButtonElement>('[data-testid="managed-tot-save"]')!.click();
    await Promise.resolve();
    expect(tx.trace()).toEqual([{ transport: 'http', operation: 'set_tot', configuredSeconds: null }]);

    tx.emitServerSnapshot({ configuredSeconds: null, remainingMs: null });
    flushSync();
    expect(target.querySelector('[data-testid="managed-tot-trigger"]')?.textContent).toContain('OFF');
    tx.emitStale();
    flushSync();
    // MOR-2674 (owner rule, 2026-09-26): stale is unread — unlit, never a
    // dash run on the status bar.
    const trigger = target.querySelector<HTMLElement>('[data-testid="managed-tot-trigger"]')!;
    expect(trigger.textContent).not.toContain('-');
    expect(trigger.querySelector('.tot-live')!.textContent!.trim()).toBe('TOT');
  });

  it('renders an unread status unlit in a reserved slot — no dash run, the reservation derived from the formatter, and a wider known value grows the box', () => {
    const target = mountControl();
    tx.emitStale();
    flushSync();
    const trigger = target.querySelector<HTMLElement>('[data-testid="managed-tot-trigger"]')!;
    expect(trigger.textContent).not.toContain('-');
    // The label stays; the value is empty (unlit LCD segment).
    expect(trigger.querySelector('.tot-live')!.textContent!.trim()).toBe('TOT');
    // The reservation is derived from the formatter, not a hand number:
    // the widest of 'OFF' and an integer seconds value of up to 4 digits.
    const format = (seconds: number | null) => (seconds === null ? 'TOT OFF' : `TOT ${seconds}s`);
    const widest = Math.max(format(null).length, format(9999).length);
    // Known OFF renders through the same slot.
    tx.emitServerSnapshot({ configuredSeconds: null, remainingMs: null });
    flushSync();
    const offLive = trigger.querySelector('.tot-live')!.textContent!.trim();
    expect(offLive).toBe('TOT OFF');
    // A known seconds text also stays inside the reservation.
    tx.emitServerSnapshot({ configuredSeconds: 180, remainingMs: null });
    flushSync();
    const knownLive = trigger.querySelector('.tot-live')!.textContent!.trim();
    expect(knownLive).toBe('TOT 180s');
    expect(knownLive.length).toBeLessThanOrEqual(widest);
    // A wider legal value (TOT accepts any positive finite number) renders
    // in full: the live text is an ordinary inline sibling, never an
    // absolute overlay that would overlap the status-bar neighbours.
    tx.emitServerSnapshot({ configuredSeconds: 12345, remainingMs: null });
    flushSync();
    const wideLive = trigger.querySelector('.tot-live')!.textContent!.trim();
    expect(wideLive).toBe('TOT 12345s');
    expect(wideLive.length).toBeGreaterThan(widest);
    // Static pins: the reservation is a minimum (min-inline-size) on the
    // live element itself, exactly the derived width; no absolute
    // positioning anywhere on it. (jsdom never applies scoped styles —
    // pattern follows StandardFrequencyReadout.)
    const source = readFileSync('src/components-v2/controls/ManagedTotStatusControl.svelte', 'utf8');
    const css = (source.match(/<style>([\s\S]*?)<\/style>/)?.[1] ?? '').replace(/\/\*[\s\S]*?\*\//g, '');
    const reservation = css.match(/\.tot-live\s*\{[^}]*min-inline-size\s*:\s*(\d+)ch/);
    expect(reservation, '.tot-live must reserve a minimum inline size').not.toBeNull();
    expect(Number(reservation![1])).toBe(widest);
    expect(css).not.toMatch(/\.tot-live\s*\{[^}]*position\s*:\s*absolute/);
  });
});
