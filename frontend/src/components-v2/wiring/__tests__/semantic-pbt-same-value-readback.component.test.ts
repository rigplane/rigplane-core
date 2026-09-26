/**
 * MOR-1692 residual — a newer fresh readback that repeats the confirmed PBT
 * value must not leave the range input at a stale drag position.
 *
 * Mounts the default face (`SemanticRadioSurfaces`, single composition) on
 * the captured IC-7300 capabilities fixture, with the per-mode twin-PBT step
 * that fixture predates composed in the same way SpectrumPanel's tests do.
 * The drag uses the real `input` event the neighbouring FilterSurface tests
 * use; the command lifecycle is the real store those tests drive.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { Capabilities } from '$lib/types/capabilities';
import type { FieldStatus, ServerState } from '$lib/types/state';
import type { ManagedAppTxController } from '$lib/runtime/tx-controller/managed-app-host';
import { measuredPbtRawToHz } from '$lib/radio/filter-controls';
import { IC7300_CAPABILITIES } from '$lib/runtime/adapters/__tests__/fixtures/ic7300-profile';

const WIDTH_HZ = 2400;
const STEP_HZ = 50;
const CONFIRMED_RAW = 206;
const DRAG_HZ = 500;
const CONFIRMED_HZ = measuredPbtRawToHz(CONFIRMED_RAW, WIDTH_HZ, STEP_HZ)!;

const h = vi.hoisted(() => ({
  session: { state: 'connected' as const, epoch: 1 },
  listeners: new Set<(next: { state: string; epoch: number }) => void>(),
  commands: vi.fn(() => true),
  txController: null as ManagedAppTxController | null,
}));
vi.mock('$lib/transport/ws-client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/transport/ws-client')>();
  return {
    ...actual,
    sendCommand: h.commands,
    getControlSession: () => h.session,
    onControlSessionTransition: (listener: (next: { state: string; epoch: number }) => void) => {
      h.listeners.add(listener);
      return () => h.listeners.delete(listener);
    },
    onCommandDelivery: () => () => undefined,
  };
});
vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({
  getManagedAppTxController: () => {
    if (!h.txController) throw new Error('managed TX harness is not installed');
    return h.txController;
  },
}));
vi.mock('$lib/runtime/adapters/mod-input-tx-guard.svelte', () => ({
  deriveModInputTxGuardProps: () => ({ visible: false, sourceLabel: null }),
  getModInputTxGuardHandlers: () => ({ onSetLan: vi.fn(), onDismiss: vi.fn() }),
}));

import SemanticRadioSurfaces from '../SemanticRadioSurfaces.svelte';
import { clearCapabilities, setCapabilities } from '$lib/stores/capabilities.svelte';
import { resetRadioState, setRadioState } from '$lib/stores/radio.svelte';
import { failCommand, getCommandLifecycles, resetCommandLifecycle } from '$lib/stores/commands.svelte';
import { ManagedAppTxHarness } from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';

const fresh = (marker: number): FieldStatus => ({
  storePath: 'x', observed: true, freshness: 'fresh',
  availability: 'available', lastObservedMonotonic: marker,
});

function steppedIc7300Caps(): Capabilities {
  return {
    ...IC7300_CAPABILITIES,
    filterConfig: Object.fromEntries(
      Object.entries(IC7300_CAPABILITIES.filterConfig ?? {}).map(([mode, config]) => [
        mode,
        mode === 'FM' ? config : { ...config, pbtStepHz: mode === 'AM' ? 200 : STEP_HZ },
      ]),
    ),
  } as Capabilities;
}

function liveState(marker: number, pbtInner = CONFIRMED_RAW, pbtOuter = CONFIRMED_RAW): ServerState {
  const slot = { freqHz: 14188000, mode: 'USB', filterNum: 1, dataMode: 0 };
  const receiver = {
    ...slot, vfoA: slot, vfoB: { ...slot, freqHz: 14074000 }, activeSlot: 'A', filter: 1,
    filterWidth: WIDTH_HZ, filterShape: 0, ifShift: 0, pbtInner, pbtOuter,
    sMeter: 0, att: 0, preamp: 0, nb: false, nr: false,
    afLevel: 0.5, rfGain: 0.8, squelch: 0,
  };
  const base = {
    stateContractVersion: 1, providerGeneration: IC7300_CAPABILITIES.providerGeneration,
    revision: marker, stateRevision: marker, freshnessRevision: marker, observationSeq: marker,
    updatedAt: '2026-09-26T00:00:00Z',
    active: 'MAIN', split: false, dualWatch: false, ptt: false, tunerStatus: 0,
    connection: { rigConnected: true, radioReady: true, controlConnected: true },
    txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 14188000 },
    main: receiver,
  };
  const paths = (node: unknown, prefix = ''): string[] => {
    if (node === null || typeof node !== 'object' || Array.isArray(node)) {
      return prefix === '' ? [] : [prefix];
    }
    const own = prefix === '' ? [] : [prefix];
    return own.concat(...Object.entries(node as Record<string, unknown>)
      .map(([key, value]) => paths(value, prefix === '' ? key : `${prefix}.${key}`)));
  };
  return {
    ...base,
    fieldStatus: Object.fromEntries(paths(base).map((path) => [path, fresh(marker)])),
  } as unknown as ServerState;
}

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;
let txHarness: ManagedAppTxHarness;

function render(): void {
  target = document.createElement('div');
  document.body.append(target);
  component = mount(SemanticRadioSurfaces, { target });
  flushSync();
}

const inputOf = (field: 'pbtInner' | 'pbtOuter'): HTMLInputElement =>
  target.querySelector<HTMLInputElement>(`[data-testid="filter-${field}"] input`)!;
const readoutOf = (field: 'pbtInner' | 'pbtOuter'): string =>
  target.querySelector(`[data-testid="filter-${field}"] output`)!.textContent ?? '';

function drag(field: 'pbtInner' | 'pbtOuter', hz: number): void {
  const input = inputOf(field);
  input.value = String(hz);
  input.dispatchEvent(new Event('input', { bubbles: true }));
  flushSync();
}

beforeEach(() => {
  txHarness = new ManagedAppTxHarness();
  h.txController = txHarness.controller;
  h.session = { state: 'connected', epoch: 1 };
  h.listeners.clear();
  h.commands.mockClear();
  resetRadioState();
  clearCapabilities();
  resetCommandLifecycle();
  expect(setCapabilities(steppedIc7300Caps())).toBe(true);
  expect(setRadioState(liveState(1))).toBe(true);
});

afterEach(() => {
  if (component) unmount(component);
  component = null;
  expect(h.listeners.size).toBe(0);
  document.body.innerHTML = '';
  resetRadioState();
  clearCapabilities();
  resetCommandLifecycle();
});

describe('same-value fresh PBT readback after a drag (MOR-1692 residual)', () => {
  it.each(['pbtInner', 'pbtOuter'] as const)(
    '%s: a failed command then a newer fresh readback of the confirmed value puts the thumb back',
    (field) => {
      render();
      const intent = field === 'pbtInner' ? 'set_pbt_inner' : 'set_pbt_outer';
      expect(inputOf(field).value).toBe(String(CONFIRMED_HZ));
      expect(readoutOf(field)).toBe(String(CONFIRMED_HZ));

      drag(field, DRAG_HZ);
      expect(h.commands).toHaveBeenCalledOnce();
      expect(inputOf(field).value).toBe(String(DRAG_HZ));
      const command = getCommandLifecycles().find((candidate) => candidate.name === intent);
      expect(command?.status).toBe('pending');

      failCommand(command!.id, command!.originalEpoch, command!.originalEpoch, 'radio refused');
      flushSync();
      expect(inputOf(field).dataset.commandPhase).toBe('failed');

      h.commands.mockClear();
      expect(setRadioState(liveState(2))).toBe(true);
      flushSync();

      expect(inputOf(field).value).toBe(String(CONFIRMED_HZ));
      expect(readoutOf(field)).toBe(String(CONFIRMED_HZ));
      expect(h.commands).not.toHaveBeenCalled();
    },
  );

  // MOR-2533: while a command is still pending, a readback that is not the
  // last sent value does not move the thumb. The thumb stays on the
  // operator's draft until that command reaches a terminal outcome.
  it.each(['pbtInner', 'pbtOuter'] as const)(
    '%s: a still-pending command holds the draft when the same confirmed value is read back',
    (field) => {
      render();
      drag(field, DRAG_HZ);
      expect(h.commands).toHaveBeenCalledOnce();
      expect(inputOf(field).value).toBe(String(DRAG_HZ));
      expect(getCommandLifecycles().some((candidate) => candidate.status === 'pending')).toBe(true);

      h.commands.mockClear();
      expect(setRadioState(liveState(2))).toBe(true);
      flushSync();

      expect(inputOf(field).value).toBe(String(DRAG_HZ));
      expect(inputOf(field).dataset.commandPhase).toBe('submitted');
      expect(h.commands).not.toHaveBeenCalled();
    },
  );
});
