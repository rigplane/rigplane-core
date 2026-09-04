import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
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
    expect(target.querySelector('[data-testid="managed-tot-current"]')?.textContent).toContain('---');
    expect(target.querySelector('[data-testid="managed-tot-current"]')?.textContent).not.toContain('OFF');
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
