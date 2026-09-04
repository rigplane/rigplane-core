import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
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
    expect(target.querySelector('[data-testid="managed-tot-trigger"]')?.textContent).toContain('---');
  });
});
