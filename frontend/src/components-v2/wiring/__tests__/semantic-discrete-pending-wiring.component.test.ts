/**
 * MOR-1488 / MOR-1473 — the discrete-control pending affordance (MOR-1441
 * leg 2: `FilterSurface`/`RfFrontEndSurface`/`DspSurface`'s `pendingFilter`/
 * `pendingPreamp`/`pendingNb`/`pendingNr`), proved over the REAL wiring
 * path: the real `$lib/stores/radio.svelte` + `$lib/stores/capabilities
 * .svelte` stores (seeded through their own epoch-gated setters, never
 * module-mocked — the PR #2409 "seed real store" idiom), a real
 * `$lib/stores/commands.svelte` command lifecycle (populated by an actual
 * click through the UNMOCKED `panel-commands.ts` handlers /
 * `dispatchRadioIntent`), the real `panel-adapters.ts` pending accessors,
 * and the real `SemanticRadioSurfaces` -> semantic-surface prop wiring.
 *
 * WHY THIS FILE EXISTS (MOR-1473, the test-gap ticket): MOR-1441 leg 2
 * (#2410) added `getPendingFilterSelection`/`getPendingPreampLevel`/
 * `getPendingNbOn`/`getPendingNrOn` and wired them through
 * `SemanticRadioSurfaces` to the three semantic surfaces, but its own test
 * (`lib/runtime/adapters/__tests__/mor1441-pending-discrete.isolated.test.ts`)
 * only exercises the four accessor functions against a hand-built fake
 * command list — it never mounts the wiring layer, so a dropped or
 * misnamed prop between the `$derived`s in `SemanticRadioSurfaces.svelte`
 * and the semantic surface's own `Props` would pass that suite unnoticed.
 * The existing `semantic-dsp-wiring.component.test.ts` /
 * `semantic-rf-front-end-wiring.component.test.ts` files replace every
 * command-bus handler with a `vi.fn()` spy, so clicking never actually
 * populates the real command-lifecycle store there either — a click in
 * those files can never observe a 'pending' status.
 *
 * This file closes both gaps: it mounts the REAL wiring, drives a REAL
 * command lifecycle for each of the five MOR-1441 pending accessors (leg
 * 1's `pendingFrequencyHz` included, for completeness), and asserts the DOM
 * marker the operator actually sees.
 *
 * Isolated pool by name (`*.component.test.ts`), per the MOR-1272 doctrine.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';

type Snapshot = {
  phase: string; intent: string | null; guard: { leaseId: string } | null;
  radioTx: string; txRisk: string; mayOwnKey: boolean; fault: string | null;
};

const IDLE: Snapshot = {
  phase: 'idle', intent: null, guard: null, radioTx: 'off', txRisk: 'none',
  mayOwnKey: false, fault: null,
};

// The App TX controller lives in Svelte context, provided by `AppGlobalHost`
// in production; a standalone mount needs a stand-in or `getAppTxController`
// throws. Not the seam this file tests — same stand-in role as every other
// wiring-component test in this directory.
vi.mock('$lib/runtime/tx-controller/app-host', () => ({
  getAppTxController: () => ({
    snapshot: () => IDLE,
    subscribe: () => () => {},
    start: vi.fn(), setIntent: vi.fn(), release: vi.fn(), resetFault: vi.fn(),
  }),
}));
vi.mock('$lib/runtime/adapters/mod-input-tx-guard.svelte', () => ({
  deriveModInputTxGuardProps: () => ({ visible: false, sourceLabel: null }),
  getModInputTxGuardHandlers: () => ({ onSetLan: vi.fn(), onDismiss: vi.fn() }),
}));
// The ONE seam this file replaces: the actual network write. Everything
// upstream of it stays real — `dispatchRadioIntent`, `beginCommand`, the
// real `$lib/stores/commands.svelte` lifecycle list — so a pending entry
// this file observes after CLICKING a real control is the exact one
// production code creates, not a stand-in shape this file made up.
vi.mock('$lib/transport/ws-client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/transport/ws-client')>();
  return { ...actual, sendCommand: vi.fn(() => true) };
});

import SemanticRadioSurfaces from '../SemanticRadioSurfaces.svelte';
import { setCapabilities, clearCapabilities } from '$lib/stores/capabilities.svelte';
import { setRadioState, resetRadioState } from '$lib/stores/radio.svelte';
import { acknowledgeCommand, getCommandLifecycles, resetCommandLifecycle } from '$lib/stores/commands.svelte';
import { dispatchRadioIntent } from '$lib/runtime/commands/radio-intents';

const PROVIDER_GENERATION = 0;
const fresh = { storePath: 'x', observed: true, freshness: 'fresh' as const, availability: 'available' as const };

/**
 * Single-receiver (IC-7300/FTX-1-shaped) fixture — the live-bench topology
 * the MOR-1488 symptom was reported against — with filter, preamp and nb/nr
 * all evidenced, so `FilterSurface`/`RfFrontEndSurface`/`DspSurface` all
 * mount live. Accepted by the REAL `setCapabilities`/`setRadioState` epoch
 * gate, not read back through a mock.
 */
function liveCaps(): Capabilities {
  return {
    model: 'fixture', scope: false, audio: true, tx: true,
    capabilities: ['audio', 'tx', 'preamp', 'nb', 'nr'],
    preValues: [0, 1, 2], attValues: [0, 6, 12, 18],
    receivers: 1, vfoScheme: 'single',
    freqRanges: [], modes: ['USB', 'LSB'], filters: ['FIL1', 'FIL2', 'FIL3'],
    audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
    webrtc: { available: false, enabled: false }, txBands: null,
    scopeSource: null, audioFftAvailable: false,
    stateContractVersion: 1, providerGeneration: PROVIDER_GENERATION,
  } as unknown as Capabilities;
}

function liveState(): ServerState {
  const paths = [
    'active', 'split', 'dualWatch', 'txTarget',
    'main.freqHz', 'main.mode', 'main.filter', 'main.activeSlot',
    'main.nb', 'main.nr', 'main.filterWidth', 'main.preamp',
  ];
  return {
    stateContractVersion: 1,
    providerGeneration: PROVIDER_GENERATION,
    revision: 1, stateRevision: 1, freshnessRevision: 1, observationSeq: 1,
    updatedAt: '2026-08-12T00:00:00Z',
    active: 'MAIN', ptt: false, split: false, dualWatch: false, tunerStatus: 0,
    txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 14250000 },
    main: {
      freqHz: 14250000, mode: 'USB', filter: 1, dataMode: 0, sMeter: 20,
      att: 0, preamp: 0, nb: false, nr: false, afLevel: 100, rfGain: 255, squelch: 0,
      activeSlot: 'A', filterWidth: 2400,
    },
    connection: { rigConnected: true, radioReady: true, controlConnected: true },
    fieldStatus: Object.fromEntries(paths.map((p) => [p, fresh])),
  } as unknown as ServerState;
}

/**
 * A confirming observation for `set_nb`: `liveState()` with `main.nb`
 * flipped to `true` and every revision counter bumped past the initial
 * fixture's — the exact "radio echoed the new value back" event, distinct
 * from a transport ack, that the MOR-1488 fix waits for.
 */
function nbConfirmedState(): ServerState {
  const base = liveState();
  return {
    ...base,
    revision: 2, stateRevision: 2, freshnessRevision: 2, observationSeq: 2,
    main: { ...base.main, nb: true },
  } as unknown as ServerState;
}

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;

function render(): void {
  target = document.createElement('div');
  document.body.appendChild(target);
  component = mount(SemanticRadioSurfaces, { target, props: {} });
  flushSync();
}

const q = <T extends HTMLElement>(sel: string) => target.querySelector(sel) as T | null;

beforeEach(() => {
  // Assert acceptance, not just call it — a rejected fixture would leave
  // `runtime.state`/`runtime.caps` at `null` and every assertion below would
  // pass or fail for the wrong reason (mirrors
  // `FrequencyDisplayInteractive.component.svelte.test.ts`'s own discipline).
  expect(setCapabilities(liveCaps())).toBe(true);
  expect(setRadioState(liveState())).toBe(true);
});

afterEach(() => {
  if (component) unmount(component);
  component = null;
  document.body.innerHTML = '';
  resetRadioState();
  clearCapabilities();
  resetCommandLifecycle();
});

describe('discrete pending markers reach the mounted DOM over the real wiring path (MOR-1488, closes MOR-1473)', () => {
  it('marks the clicked filter choice pending while set_filter is in flight (FilterSurface)', () => {
    render();
    expect(q('[data-testid="filter-select"]')!.dataset.filterStatus).toBe('confirmed');

    q<HTMLButtonElement>('[data-testid="filter-select-2"]')!.click();
    flushSync();

    expect(q('[data-testid="filter-select"]')!.dataset.filterStatus).toBe('pending');
    expect(q('[data-testid="filter-select-2"]')!.dataset.pending).toBe('true');
    // The choice NOT targeted must not also read pending — a swapped/shared
    // accessor (e.g. reading `pendingPreamp` here) would mark every choice,
    // or none, rather than the one actually in flight.
    expect(q('[data-testid="filter-select-1"]')!.dataset.pending).toBe('false');
  });

  it('marks the clicked preamp choice pending while set_preamp is in flight (RfFrontEndSurface)', () => {
    render();
    expect(q('[data-testid="rf-front-end-preamp"]')!.dataset.preampStatus).toBe('confirmed');

    q<HTMLButtonElement>('[data-testid="rf-front-end-preamp-1"]')!.click();
    flushSync();

    expect(q('[data-testid="rf-front-end-preamp"]')!.dataset.preampStatus).toBe('pending');
    expect(q('[data-testid="rf-front-end-preamp-1"]')!.dataset.pending).toBe('true');
  });

  it('marks the NB toggle pending while set_nb is in flight, NR untouched (DspSurface)', () => {
    render();
    expect(q('[data-testid="dsp-nbActive"]')!.dataset.pendingStatus).toBe('confirmed');
    expect(q('[data-testid="dsp-nrActive"]')!.dataset.pendingStatus).toBe('confirmed');

    q<HTMLButtonElement>('[data-testid="dsp-nbActive"]')!.click();
    flushSync();

    expect(q('[data-testid="dsp-nbActive"]')!.dataset.pendingStatus).toBe('pending');
    // Kills a `pendingNb`/`pendingNr` swap in the wiring's two DSP props —
    // the untouched toggle must stay confirmed.
    expect(q('[data-testid="dsp-nrActive"]')!.dataset.pendingStatus).toBe('confirmed');
  });

  it('marks the NR toggle pending while set_nr is in flight, NB untouched (DspSurface)', () => {
    render();
    q<HTMLButtonElement>('[data-testid="dsp-nrActive"]')!.click();
    flushSync();

    expect(q('[data-testid="dsp-nrActive"]')!.dataset.pendingStatus).toBe('pending');
    expect(q('[data-testid="dsp-nbActive"]')!.dataset.pendingStatus).toBe('confirmed');
  });

  // Leg 1 parity (MOR-1441's original `pendingFrequencyHz`) — same wiring
  // seam (`SemanticRadioSurfaces`'s `$derived`s), same real-store path, so
  // this suite proves leg 1 and leg 2 travel the identical wiring
  // discipline rather than asserting leg 2 in isolation.
  it('marks the VFO frequency readout pending while set_freq is in flight (VfoSurface, leg 1)', () => {
    render();
    const freqEl = () => target.querySelector('[data-vfo-receiver="MAIN"] [data-freq-status]') as HTMLElement | null;
    expect(freqEl()?.dataset.freqStatus).toBe('confirmed');

    dispatchRadioIntent({ name: 'set_freq', params: { freq: 14260000, receiver: 0 } });
    flushSync();

    expect(freqEl()?.dataset.freqStatus).toBe('pending');
  });

  it('clears the pending marker once the command resolves (acknowledged), for all four discrete controls', () => {
    render();
    q<HTMLButtonElement>('[data-testid="filter-select-2"]')!.click();
    q<HTMLButtonElement>('[data-testid="rf-front-end-preamp-1"]')!.click();
    q<HTMLButtonElement>('[data-testid="dsp-nbActive"]')!.click();
    q<HTMLButtonElement>('[data-testid="dsp-nrActive"]')!.click();
    flushSync();
    expect(q('[data-testid="filter-select"]')!.dataset.filterStatus).toBe('pending');
    expect(q('[data-testid="rf-front-end-preamp"]')!.dataset.preampStatus).toBe('pending');
    expect(q('[data-testid="dsp-nbActive"]')!.dataset.pendingStatus).toBe('pending');
    expect(q('[data-testid="dsp-nrActive"]')!.dataset.pendingStatus).toBe('pending');

    // A resolved command must stop reading as pending — the leg-1 honesty
    // rule (`mor1441-pending-discrete.isolated.test.ts`'s own doc comment)
    // applied over the real wiring path this time, not the fake command list.
    resetCommandLifecycle();
    flushSync();

    expect(q('[data-testid="filter-select"]')!.dataset.filterStatus).toBe('confirmed');
    expect(q('[data-testid="rf-front-end-preamp"]')!.dataset.preampStatus).toBe('confirmed');
    expect(q('[data-testid="dsp-nbActive"]')!.dataset.pendingStatus).toBe('confirmed');
    expect(q('[data-testid="dsp-nrActive"]')!.dataset.pendingStatus).toBe('confirmed');
  });

  /**
   * MOR-1488 live-bench finding: a transport ack is not a confirming
   * observation. The WS ack for `set_nb` typically lands within
   * milliseconds — long before the next state poll (~500ms keep-alive)
   * echoes `main.nb` back — so a pending marker that clears on ack alone
   * collapses to something the operator can never actually see, exactly
   * the reported symptom ("values flip instantly, pending presented as
   * confirmed"). This drives the command to 'acknowledged' directly
   * (`acknowledgeCommand`, the same primitive `radio-intents.ts`'s
   * `onCommandDelivery` ack handler calls) WITHOUT changing the confirmed
   * radio state, and requires the marker to survive that ack — only a
   * `setRadioState` observation that actually confirms the target
   * (`main.nb === true`) may clear it.
   */
  it('keeps the NB marker pending across a transport ack until a confirming state observation arrives (MOR-1488)', () => {
    render();
    q<HTMLButtonElement>('[data-testid="dsp-nbActive"]')!.click();
    flushSync();
    expect(q('[data-testid="dsp-nbActive"]')!.dataset.pendingStatus).toBe('pending');

    const command = getCommandLifecycles().find(
      (candidate) => candidate.name === 'set_nb' && candidate.status === 'pending',
    );
    expect(command).toBeDefined();
    acknowledgeCommand(command!.id, command!.originalEpoch, command!.originalEpoch);
    flushSync();

    // The ack alone must NOT confirm the marker — `main.nb` is still `false`
    // in the store, so the operator has no honest evidence the radio applied
    // the change yet.
    expect(q('[data-testid="dsp-nbActive"]')!.dataset.pendingStatus).toBe('pending');

    // The confirming observation: the radio's own state now echoes `nb: true`.
    expect(setRadioState(nbConfirmedState())).toBe(true);
    flushSync();

    expect(q('[data-testid="dsp-nbActive"]')!.dataset.pendingStatus).toBe('confirmed');
  });
});
