/**
 * MOR-1687 part 2 — the wiring pin the part-1 review flagged as missing:
 * nothing failed if `SemanticRadioSurfaces.svelte` stopped passing
 * `ifShiftFeedback`/`pbtInnerFeedback`/`pbtOuterFeedback` into
 * `FilterSurface`.
 *
 * Harness: the `semantic-pbt-continuity.component.test.ts` seam set — the
 * REAL radio/capabilities stores, the REAL runtime, the REAL
 * `panel-adapters` producers and the REAL `SemanticRadioSurfaces` ->
 * `FilterSurface` prop wiring; the `vi.mock` blocks below are the complete
 * list of stubbed seams. Pending commands come
 * from the REAL lifecycle store via `dispatchRadioIntent` — the same
 * producer `panel-commands.ts`'s handlers call — so the feedback these
 * tests observe is the exact object production code projects, not a
 * hand-built stand-in.
 *
 * The pin: a dropped feedback prop collapses the row's scalar to reading
 * evidence, its input loses `data-command-phase`, and the pending target
 * leaves the thumb. Verified by mutation — temporarily removing
 * `{pbtInnerFeedback}` from the wiring's `<FilterSurface>` mount fails
 * the set_pbt_inner case (recorded in the commit body).
 *
 * Isolated pool by name (`*.component.test.ts`), per the MOR-1272 doctrine.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { Capabilities } from '$lib/types/capabilities';
import type { FieldStatus, ServerState } from '$lib/types/state';
import type { ManagedAppTxController } from '$lib/runtime/tx-controller/managed-app-host';
import { measuredPbtRawToHz } from '$lib/radio/filter-controls';

const h = vi.hoisted(() => ({
  session: { state: 'connected', epoch: 1 },
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
import { getCommandLifecycles, resetCommandLifecycle } from '$lib/stores/commands.svelte';
import { dispatchRadioIntent } from '$lib/runtime/commands/radio-intents';
import { ManagedAppTxHarness } from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';

const PROVIDER_GENERATION = 1;
/** The observed width/step every PBT conversion below rides — the values
 *  `caps().filterConfig.USB.pbtStepHz` and `receiver().filterWidth` carry,
 *  so `measuredPbtRawToHz(raw, WIDTH_HZ, STEP_HZ)` is the same conversion
 *  `pbtFeedbackOnMeasuredLattice` performs on the pending target. */
const WIDTH_HZ = 2400;
const STEP_HZ = 50;
const fresh: FieldStatus = {
  storePath: 'x', observed: true, freshness: 'fresh',
  availability: 'available', lastObservedMonotonic: 500,
};

/** Freshness-invisibility's proven live fixture, trimmed to the filter
 *  tree: distinct PBT raws (100/40) so a crossed inner/outer prop in the
 *  wiring cannot pass by landing on the same number. */
function liveCaps(extraTags: readonly string[] = []): Capabilities {
  return {
    stateContractVersion: 1, providerGeneration: PROVIDER_GENERATION,
    model: 'fixture', scope: false, audio: true, tx: true,
    capabilities: ['pbt', 'filter_width', 'filter_shape', ...extraTags],
    receivers: 2, vfoScheme: 'main_sub',
    freqRanges: [{ start: 1800000, end: 54000000, label: 'HF+6m',
      bands: [{ name: '20m', start: 14000000, end: 14350000, default: 14195000 }] }],
    modes: ['USB'], filters: ['FIL1', 'FIL2', 'FIL3'],
    controls: {
      pbt_inner: { raw_center: 128, display_min: -1200, display_max: 1200 },
      pbt_outer: { raw_center: 128, display_min: -1200, display_max: 1200 },
    },
    filterConfig: { USB: { defaults: [2400], fixed: false, pbtStepHz: STEP_HZ } },
    audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
    webrtc: { available: false, enabled: false },
    txBands: [{ start: 14000000, end: 14350000, name: '20m' }],
    scopeSource: null, audioFftAvailable: false,
  } as unknown as Capabilities;
}

function liveState(seq = 3): ServerState {
  const slot = { freqHz: 14250000, mode: 'USB', filterNum: 1, dataMode: 0 };
  // The `isValidServerState` fields (updatedAt, tunerStatus, connection,
  // sMeter/att/preamp/nb/nr/afLevel/rfGain/squelch) are load-bearing: the
  // real epoch-gated `setRadioState` this file seeds through refuses a
  // payload without them, and a rejected seed fails every assertion below
  // for the wrong reason.
  const receiver = {
    ...slot, vfoA: slot, vfoB: { ...slot, freqHz: 14300000 }, activeSlot: 'A', filter: 1,
    filterWidth: WIDTH_HZ, filterShape: 1, ifShift: 0, pbtInner: 100, pbtOuter: 40,
    sMeter: -12, att: 12, preamp: 1, nb: true, nr: false,
    afLevel: 0.4, rfGain: 0.75, squelch: 0.1,
  };
  const base = {
    stateContractVersion: 1, providerGeneration: PROVIDER_GENERATION,
    revision: seq, stateRevision: seq, freshnessRevision: seq, observationSeq: seq,
    updatedAt: '2026-09-17T00:00:00Z',
    active: 'MAIN', split: false, dualWatch: false, ptt: false, tunerStatus: 0,
    connection: { rigConnected: true, radioReady: true, controlConnected: true },
    txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 14250000 },
    main: receiver, sub: { ...receiver, pbtInner: 128, pbtOuter: 128 },
  };
  const paths = (node: unknown, prefix = ''): string[] => {
    if (node === null || typeof node !== 'object' || Array.isArray(node)) {
      return prefix === '' ? [] : [prefix];
    }
    const own = prefix === '' ? [] : [prefix];
    return own.concat(...Object.entries(node as Record<string, unknown>)
      .map(([key, value]) => paths(value, prefix === '' ? key : `${prefix}.${key}`)));
  };
  return { ...base,
    fieldStatus: Object.fromEntries(paths(base).map((path) => [path, fresh])),
  } as unknown as ServerState;
}

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;
let txHarness: ManagedAppTxHarness;

function render(): void {
  target = document.createElement('div');
  document.body.appendChild(target);
  component = mount(SemanticRadioSurfaces, { target });
  flushSync();
}

const rowInput = (field: string): HTMLInputElement | null =>
  target.querySelector<HTMLInputElement>(`[data-testid="filter-${field}"] input`);

beforeEach(() => {
  txHarness = new ManagedAppTxHarness();
  h.txController = txHarness.controller;
  // `radio-intents` subscribes once at module import and never unsubscribes;
  // dropping that registration keeps the afterEach count about THIS test's
  // mounts only (same idiom as `semantic-pbt-continuity`).
  h.listeners.clear();
  h.commands.mockClear();
  resetRadioState();
  clearCapabilities();
  expect(setCapabilities(liveCaps())).toBe(true);
  expect(setRadioState(liveState())).toBe(true);
});

afterEach(() => {
  if (component) unmount(component);
  component = null;
  expect(h.listeners.size).toBe(0);
  expect(txHarness.trace()).toEqual([]);
  document.body.innerHTML = '';
  resetRadioState();
  clearCapabilities();
  resetCommandLifecycle();
});

describe('pending passband commands reach the mounted FilterSurface rows over the real wiring (MOR-1687 part 2)', () => {
  it.each([
    ['set_pbt_inner', 150, 'pbtInner', 'pbtOuter'],
    ['set_pbt_outer', 170, 'pbtOuter', 'pbtInner'],
  ] as const)('%s: pending target on the thumb, phase on the input, sibling untouched', (intent, raw, field, sibling) => {
    render();
    dispatchRadioIntent({ name: intent, params: { value: raw, receiver: 0 } });
    flushSync();

    // The command itself is live in the real store — the feedback below is
    // its projection, not a fixture.
    const command = getCommandLifecycles().find((candidate) => candidate.name === intent);
    expect(command?.status).toBe('pending');

    const input = rowInput(field)!;
    expect(input).not.toBeNull();
    expect(input.dataset.commandPhase).toBe('submitted');
    expect(input.getAttribute('aria-busy')).toBe('true');
    expect(input.value).toBe(String(measuredPbtRawToHz(raw, WIDTH_HZ, STEP_HZ)));
    // The row NOT targeted keeps its non-pending presentation — a
    // swapped/shared feedback prop would mark both rows.
    expect(rowInput(sibling)!.dataset.commandPhase).not.toBe('submitted');
    expect(rowInput(sibling)!.value).not.toBe(input.value);
  });

  it('set_if_shift on a radio with its own if_shift command: pending offset (identity Hz) on the IF-shift row', () => {
    expect(setCapabilities(liveCaps(['if_shift']))).toBe(true);
    expect(setRadioState(liveState(4))).toBe(true);
    render();
    const row = target.querySelector('[data-testid="filter-ifShift"]');
    expect(row).not.toBeNull();

    dispatchRadioIntent({ name: 'set_if_shift', params: { offset: 300, receiver: 0 } });
    flushSync();

    const input = rowInput('ifShift')!;
    expect(input.dataset.commandPhase).toBe('submitted');
    expect(input.getAttribute('aria-busy')).toBe('true');
    // if_shift is identity-mapped Hz on the wire (IF_SHIFT_COMMAND_DESCRIPTOR
    // reads `offset` raw), so the thumb carries the offset itself.
    expect(input.value).toBe('300');
  });
});
