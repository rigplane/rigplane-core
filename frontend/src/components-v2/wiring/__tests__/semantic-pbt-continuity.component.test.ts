/**
 * MOR-1692 — PBT continuity over the real frontend state -> semantic adapter
 * -> mounted FilterSurface path.  State and capabilities use their real
 * generation-gated stores; only the network boundary and unrelated TX host
 * are replaced.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { Capabilities } from '$lib/types/capabilities';
import type { FieldStatus, ServerState } from '$lib/types/state';

const { sendCommand } = vi.hoisted(() => ({ sendCommand: vi.fn(() => true) }));
vi.mock('$lib/transport/ws-client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('$lib/transport/ws-client')>()),
  sendCommand,
}));
vi.mock('$lib/runtime/tx-controller/app-host', () => ({
  getAppTxController: () => ({
    snapshot: () => ({
      phase: 'idle', intent: null, guard: null, radioTx: 'off', txRisk: 'none',
      mayOwnKey: false, fault: null,
    }),
    subscribe: () => () => {}, start: vi.fn(), setIntent: vi.fn(),
    release: vi.fn(), resetFault: vi.fn(),
  }),
}));
vi.mock('$lib/runtime/adapters/mod-input-tx-guard.svelte', () => ({
  deriveModInputTxGuardProps: () => ({ visible: false, sourceLabel: null }),
  getModInputTxGuardHandlers: () => ({ onSetLan: vi.fn(), onDismiss: vi.fn() }),
}));

import SemanticRadioSurfaces from '../SemanticRadioSurfaces.svelte';
import { clearCapabilities, setCapabilities } from '$lib/stores/capabilities.svelte';
import { resetRadioState, setRadioState } from '$lib/stores/radio.svelte';
import { resetCommandLifecycle } from '$lib/stores/commands.svelte';

const status = (
  path: string, at: number, kind: 'fresh' | 'stale' | 'missing' = 'fresh',
): FieldStatus => ({
  storePath: path,
  observed: kind !== 'missing',
  freshness: kind === 'fresh' ? 'fresh' : kind === 'stale' ? 'stale' : 'unknown',
  availability: kind === 'fresh' ? 'available' : kind,
  ...(kind === 'missing' ? {} : { lastObservedMonotonic: at, maxAge: 10 }),
});

function caps(generation = 0, receivers = 1, pbt = true): Capabilities {
  return {
    model: 'fixture', scope: false, audio: false, tx: false,
    capabilities: pbt ? ['pbt'] : [], receivers, vfoScheme: receivers === 1 ? 'single' : 'main_sub',
    freqRanges: [], modes: [], filters: [], txBands: null,
    controls: pbt ? {
      pbt_inner: {
        raw_min: 0, raw_max: 255, raw_center: 128,
        display_min: -1200, display_max: 1200, display_unit: 'Hz',
      },
    } : {},
    stateContractVersion: 1, providerGeneration: generation,
  } as unknown as Capabilities;
}

interface StateOptions {
  generation?: number;
  revision?: number;
  observationSeq?: number;
  active?: 'MAIN' | 'SUB';
  inner?: number;
  outer?: number;
  innerKind?: 'fresh' | 'stale' | 'missing';
  outerKind?: 'fresh' | 'stale' | 'missing';
  innerAt?: number;
  outerAt?: number;
}

function state(options: StateOptions = {}): ServerState {
  const {
    generation = 0, revision = 1, observationSeq = revision, active = 'MAIN',
    inner = 160, outer = 96, innerKind = 'fresh', outerKind = 'fresh',
    innerAt = observationSeq, outerAt = observationSeq,
  } = options;
  const receiver = (pbtInner: number | undefined, pbtOuter: number | undefined) => ({
    freqHz: 14_200_000, mode: 'USB', filter: 1, dataMode: 0, sMeter: 20,
    att: 0, preamp: 0, nb: false, nr: false, afLevel: 100, rfGain: 255, squelch: 0,
    ...(pbtInner === undefined ? {} : { pbtInner }),
    ...(pbtOuter === undefined ? {} : { pbtOuter }),
  });
  return {
    stateContractVersion: 1, providerGeneration: generation,
    revision, stateRevision: revision, freshnessRevision: revision, observationSeq,
    updatedAt: '2026-08-15T00:00:00Z', active, ptt: false, split: false,
    dualWatch: false, tunerStatus: 0,
    txTarget: { status: 'unknown', reason: 'not-observed' },
    main: receiver(innerKind === 'missing' ? undefined : inner, outerKind === 'missing' ? undefined : outer),
    sub: receiver(innerKind === 'missing' ? undefined : inner, outerKind === 'missing' ? undefined : outer),
    connection: { rigConnected: true, radioReady: true, controlConnected: true },
    fieldStatus: {
      active: status('active', observationSeq),
      [`${active === 'SUB' ? 'sub' : 'main'}.pbtInner`]: status('pbtInner', innerAt, innerKind),
      [`${active === 'SUB' ? 'sub' : 'main'}.pbtOuter`]: status('pbtOuter', outerAt, outerKind),
    },
  } as unknown as ServerState;
}

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;

const row = (field: 'pbtInner' | 'pbtOuter') =>
  target.querySelector<HTMLElement>(`[data-testid="filter-${field}"]`)!;
const input = (field: 'pbtInner' | 'pbtOuter') => row(field).querySelector<HTMLInputElement>('input')!;
const output = (field: 'pbtInner' | 'pbtOuter') => row(field).querySelector('output')!.textContent;

function render(): void {
  target = document.createElement('div');
  document.body.appendChild(target);
  component = mount(SemanticRadioSurfaces, { target });
  flushSync();
}

beforeEach(() => {
  vi.useFakeTimers();
  expect(setCapabilities(caps())).toBe(true);
});

afterEach(() => {
  if (component) unmount(component);
  component = null;
  document.body.innerHTML = '';
  resetRadioState();
  clearCapabilities();
  resetCommandLifecycle();
  sendCommand.mockClear();
  vi.useRealTimers();
});

describe('mounted PBT presentation continuity (MOR-1692)', () => {
  it('keeps independent last-confirmed values through stale/missing gaps and recovers without an endpoint jump', () => {
    expect(setRadioState(state())).toBe(true);
    render();
    expect([output('pbtInner'), output('pbtOuter')]).toEqual(['300', '-300']);

    expect(setRadioState(state({
      revision: 2, observationSeq: 2, innerKind: 'stale', outerKind: 'missing',
    }))).toBe(true);
    flushSync();
    expect([output('pbtInner'), output('pbtOuter')]).toEqual(['300', '-300']);
    expect([row('pbtInner').dataset.presentation, row('pbtOuter').dataset.presentation])
      .toEqual(['retained', 'retained']);
    expect(input('pbtInner').disabled).toBe(true);
    expect(input('pbtOuter').disabled).toBe(true);
    expect([input('pbtInner').value, input('pbtOuter').value]).toEqual(['300', '-300']);

    expect(setRadioState(state({
      revision: 3, observationSeq: 3, inner: 176, outer: 80, innerAt: 3, outerAt: 3,
    }))).toBe(true);
    flushSync();
    expect([output('pbtInner'), output('pbtOuter')]).toEqual(['450', '-450']);
    expect([row('pbtInner').dataset.presentation, row('pbtOuter').dataset.presentation])
      .toEqual(['confirmed', 'confirmed']);
    expect(sendCommand).not.toHaveBeenCalled();
  });

  it('shows no false thumb when no confirmed value exists', () => {
    expect(setRadioState(state({ innerKind: 'missing', outerKind: 'missing' }))).toBe(true);
    render();
    expect([output('pbtInner'), output('pbtOuter')]).toEqual(['?', '?']);
    expect([row('pbtInner').dataset.presentation, row('pbtOuter').dataset.presentation])
      .toEqual(['unknown', 'unknown']);
    expect(input('pbtInner').dataset.thumb).toBe('hidden');
    expect(input('pbtOuter').dataset.thumb).toBe('hidden');
  });

  it('rejects a delayed older readback, then accepts a newer confirmation', () => {
    expect(setRadioState(state({ innerAt: 20, outerAt: 20 }))).toBe(true);
    render();

    expect(setRadioState(state({
      revision: 2, observationSeq: 2, inner: 64, outer: 192, innerAt: 10, outerAt: 10,
    }))).toBe(true);
    flushSync();
    expect([output('pbtInner'), output('pbtOuter')]).toEqual(['300', '-300']);
    expect(row('pbtInner').dataset.presentation).toBe('retained');

    expect(setRadioState(state({
      revision: 3, observationSeq: 3, inner: 176, outer: 80, innerAt: 30, outerAt: 30,
    }))).toBe(true);
    flushSync();
    expect([output('pbtInner'), output('pbtOuter')]).toEqual(['450', '-450']);
  });

  it('clears continuity on provider generation and receiver identity boundaries', () => {
    expect(setRadioState(state())).toBe(true);
    render();
    expect(output('pbtInner')).toBe('300');

    expect(setCapabilities(caps(1))).toBe(true);
    expect(setRadioState(state({
      generation: 1, revision: 1, observationSeq: 1,
      innerKind: 'missing', outerKind: 'missing',
    }))).toBe(true);
    flushSync();
    expect(row('pbtInner').dataset.presentation).toBe('unknown');

    expect(setCapabilities(caps(2, 2))).toBe(true);
    expect(setRadioState(state({ generation: 2, revision: 1, observationSeq: 1 }))).toBe(true);
    flushSync();
    expect(output('pbtInner')).toBe('300');
    expect(setRadioState(state({
      generation: 2, revision: 2, observationSeq: 2, active: 'SUB',
      innerKind: 'missing', outerKind: 'missing',
    }))).toBe(true);
    flushSync();
    expect(row('pbtInner').dataset.presentation).toBe('unknown');
  });

  it('does not restore retained values after an explicit unsupported interval', () => {
    expect(setRadioState(state())).toBe(true);
    render();
    expect(output('pbtInner')).toBe('300');
    expect(setCapabilities(caps(0, 1, false))).toBe(true);
    flushSync();
    expect(target.querySelector('[data-testid="filter-pbtInner"]')).toBeNull();
    expect(setCapabilities(caps())).toBe(true);
    expect(setRadioState(state({ revision: 2, observationSeq: 2, innerKind: 'missing' }))).toBe(true);
    flushSync();
    expect(row('pbtInner').dataset.presentation).toBe('unknown');
  });

  it('keeps Inner/Outer command gestures independent and recovery emits no extra command', () => {
    expect(setRadioState(state())).toBe(true);
    render();
    input('pbtInner').value = '375';
    input('pbtInner').dispatchEvent(new Event('input', { bubbles: true }));
    input('pbtOuter').value = '-375';
    input('pbtOuter').dispatchEvent(new Event('input', { bubbles: true }));
    vi.advanceTimersByTime(60);
    expect(sendCommand).toHaveBeenCalledTimes(2);

    expect(setRadioState(state({
      revision: 2, observationSeq: 2, innerKind: 'stale', outerKind: 'stale',
    }))).toBe(true);
    flushSync();
    expect(setRadioState(state({
      revision: 3, observationSeq: 3, inner: 168, outer: 88, innerAt: 3, outerAt: 3,
    }))).toBe(true);
    flushSync();
    expect(sendCommand).toHaveBeenCalledTimes(2);
  });
});
