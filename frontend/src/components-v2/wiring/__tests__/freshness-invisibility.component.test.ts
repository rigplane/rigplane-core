/**
 * MOR-2425 §C — the R41 contract: the web paints no per-field freshness.
 *
 * One set of values, published twice through the real adapter into the real
 * `SemanticRadioSurfaces` tree — every `fieldStatus` leaf `fresh`/`available`,
 * then every leaf `stale`/`stale`. `visibleShape()` strips `data-*` and
 * `aria-*` (`data-display-state`/`data-presentation` deliberately still
 * differ, for design languages), `.sr-only` nodes, and `id`/`for` (per-mount
 * counters); what is left must be identical but for one pinned exception.
 *
 * ARIA DIFFERENCES FOUND AND LEFT: none — accessible NAMES are compared
 * directly, past the strip. Two of the three the class sweep found are
 * exercised here and fixed: `VfoIndicatorRow`'s "(stale, last observed)"
 * name and the `†` cue's `aria-describedby` sentence. `bar-meter-projector`'s
 * "Stale observation" description is a third instance of the same class,
 * also fixed, but NOT caught by this comparison: the TX harness's default
 * RX authority snapshot makes the TX meters not relevant in receive, so
 * they take the `relevance === 'idle'` early return before that description
 * branch runs, and both mounts render the same idle text either way. That fix is pinned by `bar-meter-projector.test.ts` and
 * `MetersSurface.test.ts` instead. The segmentline LCD skin still
 * distinguishes the two and is not mounted here: out of scope.
 *
 * Isolated pool by name (`*.component.test.ts`), per the MOR-1272 doctrine.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { Capabilities } from '$lib/types/capabilities';
import type { FieldStatus, ServerState } from '$lib/types/state';
import type { ManagedAppTxController } from '$lib/runtime/tx-controller/managed-app-host';
import type { ControlSessionSnapshot } from '$lib/runtime/frontend-runtime';
import type { RxAudioTargetSnapshot } from '$lib/stores/audio.svelte';

const h = vi.hoisted(() => ({
  state: null as unknown,
  caps: null as unknown,
  controlSession: { state: 'connected', epoch: 1 } as ControlSessionSnapshot,
  authoritySubscribers: new Set<(next: {
    state: unknown; caps: unknown; session: ControlSessionSnapshot;
    rxAudioTarget: RxAudioTargetSnapshot;
  }) => void>(),
  txController: null as ManagedAppTxController | null,
  audio: { muted: false, rxEnabled: true, volume: 42 },
}));

vi.mock('$lib/transport/ws-client', () => ({
  sendCommand: vi.fn(),
  onCommandDelivery: () => () => {},
  onControlSessionTransition: () => () => {},
  getControlSession: () => ({ state: 'connected', epoch: 1 }),
}));
vi.mock('$lib/stores/radio.svelte', () => ({
  getActiveReceiver: vi.fn(() => (h.state as ServerState | null)?.main ?? null),
  getRadioState: vi.fn(() => h.state as ServerState | null),
  patchActiveReceiver: vi.fn(), patchRadioState: vi.fn(), patchReceiver: vi.fn(),
}));
vi.mock('$lib/stores/capabilities.svelte', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/stores/capabilities.svelte')>();
  return {
    ...actual,
    getCapabilities: vi.fn(() => h.caps as Capabilities | null),
    getControlRange: vi.fn(() => null),
    getSmeterCalibration: vi.fn(() => null),
    getSmeterRedline: vi.fn(() => null),
  };
});
vi.mock('$lib/runtime/commands/radio-intents', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/runtime/commands/radio-intents')>();
  const { sendCommand } = await import('$lib/transport/ws-client');
  return {
    ...actual,
    dispatchRadioIntent: ({ name, params }: { name: string; params: Record<string, unknown> }) => sendCommand(name, params),
  };
});
vi.mock('$lib/audio/audio-manager', () => ({
  audioManager: {
    get rxEnabled() { return true; },
    startRx: vi.fn(), stopRx: vi.fn(), setRxVolume: vi.fn(), setAudioConfig: vi.fn(),
  },
}));
vi.mock('$lib/runtime/frontend-runtime', () => ({
  runtime: {
    onTxAudioDied: () => () => {},
    get state() { return h.state; },
    get caps() { return h.caps; },
    get controlSession() { return h.controlSession; },
    subscribeControlAuthority(handler: (typeof h.authoritySubscribers extends Set<infer T> ? T : never)) {
      h.authoritySubscribers.add(handler);
      handler({
        state: h.state, caps: h.caps, session: h.controlSession,
        rxAudioTarget: Object.freeze({ muted: h.audio.muted, rxEnabled: h.audio.rxEnabled }),
      });
      return () => { h.authoritySubscribers.delete(handler); };
    },
    get audio() { return h.audio; },
    get connectionAudio() { return true; },
    get rxEnabled() { return true; },
    setVolume: vi.fn(), setMuted: vi.fn(), setRxLive: vi.fn(), setRxVolume: vi.fn(),
    get defaultScopeStatus() {
      return { source: null, available: false, resourceSelected: false, demand: 0,
        lifecycle: 'inactive', transport: 'disconnected', frameSeen: false };
    },
    get scope() { return { hardwareScopeConnected: false }; },
  },
}));
vi.mock('$lib/runtime', async () => ({
  runtime: (await import('$lib/runtime/frontend-runtime')).runtime,
}));
vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({
  getManagedAppTxController: () => h.txController,
}));
vi.mock('$lib/runtime/adapters/mod-input-tx-guard.svelte', () => ({
  deriveModInputTxGuardProps: () => ({ visible: false, sourceLabel: 'MIC' }),
  getModInputTxGuardHandlers: () => ({ onSetLan: vi.fn(), onDismiss: vi.fn() }),
}));

import SemanticRadioSurfaces from '../SemanticRadioSurfaces.svelte';
import MemorySurface from '../../../semantic/MemorySurface.svelte';
import { deriveMemoryPanelProps } from '$lib/runtime/adapters/panel-adapters';
import { ManagedAppTxHarness } from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';

/** Every semantic surface (`frontend/src/semantic/*Surface.svelte`), by testid. */
const SURFACES = [
  'antenna-surface', 'band-surface', 'cw-keyer-surface', 'dsp-surface', 'filter-surface',
  'memory-surface', 'meters-surface', 'rf-front-end-surface', 'ritxit-scan-surface',
  'rx-audio-surface', 'rx-tx-surface', 'scope-controls-surface', 'scope-display-surface',
  'tx-aux-surface', 'vfo-surface',
] as const;

const slot = (hz: number) => ({ freqHz: hz, mode: 'USB', filterNum: 1, dataMode: 0 });

const receiver = (hz: number, sMeter: number) => ({
  ...slot(hz), vfoA: slot(hz), vfoB: slot(hz + 30000), activeSlot: 'A', filter: 1,
  filterWidth: 2400, filterShape: 1, ifShift: 0, pbtInner: 128, pbtOuter: 128, att: 12,
  preamp: 1, nb: true, nr: false, digisel: false, ipplus: true, agc: 2, agcTimeConstant: 2,
  autoNotch: false, manualNotch: true, notchFilter: 64, manualNotchWidth: 1, apfFreq: 600,
  twinPeakFilter: false, apfOn: true, apfTypeLevel: 1, afLevel: 0.4, rfGain: 0.75,
  squelch: 0.1, sMeter, nrLevel: 0.3, nbLevel: 0.5,
});

/** Everything the semantic tree reads, at one set of values. */
function baseState(): Record<string, unknown> {
  return {
    stateContractVersion: 1, providerGeneration: 1,
    revision: 3, stateRevision: 3, freshnessRevision: 3, observationSeq: 3,
    active: 'MAIN', split: true, dualWatch: true, ptt: false, powerOn: true,
    powerLevel: 0.6, tunerStatus: 0, dialLock: false, mainSubTracking: false,
    scanning: false, scanType: 0, scanResumeMode: 1, tuningStep: 3, ritFreq: 250,
    ritOn: true, ritTx: false, txAntenna: 1, rxAntenna1: true, rxAntenna2: false,
    dataOffModInput: 5, data1ModInput: 5, cwPitch: 600, keySpeed: 24, breakIn: 1,
    breakInDelay: 30, dashRatio: 30, cwSpot: false, micGain: 128, driveGain: 127,
    monitorOn: true, monitorGain: 40, compressorOn: true, compressorLevel: 60,
    ssbTxBandwidth: 1, voxOn: false, voxGain: 64, antiVoxGain: 32, voxDelay: 20,
    nbDepth: 4, nbWidth: 20, powerMeter: 120, swrMeter: 30, alcMeter: 40,
    compMeter: 20, vdMeter: 200, idMeter: 90,
    txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 14250000 },
    scopeControls: { receiver: 0, dual: false, mode: 0, span: 3, edge: 1, hold: false,
      refDb: -5, speed: 1, duringTx: false, centerType: 1, vbwNarrow: false, rbw: 1,
      fixedEdge: { rangeIndex: 0, edge: 1, startHz: 14000000, endHz: 14350000 } },
    main: receiver(14250000, -12), sub: receiver(14300000, -30),
  };
}

/** Every path in `node`, container paths included — the adapter gates on both. */
function paths(node: unknown, prefix = ''): string[] {
  if (node === null || typeof node !== 'object' || Array.isArray(node)) {
    return prefix === '' ? [] : [prefix];
  }
  const own = prefix === '' ? [] : [prefix];
  return own.concat(...Object.entries(node as Record<string, unknown>)
    .map(([key, value]) => paths(value, prefix === '' ? key : `${prefix}.${key}`)));
}

function liveState(freshness: 'fresh' | 'stale'): ServerState {
  const base = baseState();
  const status = (storePath: string): FieldStatus => ({
    storePath, observed: true, freshness,
    availability: freshness === 'fresh' ? 'available' : 'stale',
    lastObservedMonotonic: 500,
  });
  return { ...base,
    fieldStatus: Object.fromEntries(paths(base).map((path) => [path, status(path)])),
  } as unknown as ServerState;
}

const liveCaps = (): Capabilities => ({
  stateContractVersion: 1, providerGeneration: 1,
  model: 'fixture', scope: true, audio: true, tx: true,
  capabilities: ['audio', 'tx', 'dual_rx', 'tuner', 'dual_watch', 'af_level', 'rf_gain',
    'squelch', 'attenuator', 'preamp', 'digisel', 'ip_plus', 'antenna', 'rx_antenna', 'nb',
    'nr', 'notch', 'apf', 'twin_peak', 'pbt', 'filter_width', 'filter_shape', 'split',
    'ssb_tx_bw', 'cw', 'break_in', 'rit', 'xit', 'meters', 'data_mode', 'scope', 'vox',
    'mod_input_routing', 'agc', 'power_control', 'dial_lock', 'scan', 'tuning_step',
    'compressor', 'monitor', 'drive_gain', 'if_shift', 'memory'],
  receivers: 2, vfoScheme: 'main_sub',
  freqRanges: [{ start: 1800000, end: 54000000, label: 'HF+6m',
    bands: [{ name: '20m', start: 14000000, end: 14350000, default: 14195000 }] }],
  modes: ['USB', 'LSB', 'CW', 'AM', 'FM', 'RTTY'], filters: ['FIL1', 'FIL2', 'FIL3'],
  antennas: 2, attValues: [0, 3, 6, 9, 12], preValues: [0, 1, 2],
  agcModes: [1, 2, 3], agcLabels: { '1': 'FAST', '2': 'MID', '3': 'SLOW' },
  dataModeCount: 1, dataModeLabels: { '0': 'OFF', '1': 'DATA' },
  controls: { pbt_inner: { raw_center: 128, display_min: -1200, display_max: 1200 },
    pbt_outer: { raw_center: 128, display_min: -1200, display_max: 1200 } },
  audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
  webrtc: { available: false, enabled: false },
  txBands: [{ start: 14000000, end: 14350000, name: '20m' }],
  scopeSource: null, audioFftAvailable: false,
} as unknown as Capabilities);

/** Every accessible name in the tree, in document order. */
function ariaLabels(root: HTMLElement): string[] {
  return [...root.querySelectorAll('[aria-label]')]
    .map((node) => node.getAttribute('aria-label') ?? '');
}

/** Anything left after this that differs is something the page paints. */
function visibleShape(root: HTMLElement): string {
  const clone = root.cloneNode(true) as HTMLElement;
  for (const node of clone.querySelectorAll('.sr-only')) node.remove();
  for (const node of clone.querySelectorAll<HTMLElement>('*')) {
    for (const name of [...node.getAttributeNames()]) {
      if (name.startsWith('data-') || name.startsWith('aria-') || name === 'id' || name === 'for') {
        node.removeAttribute(name);
      }
    }
  }
  // One element per line, so a failure diffs where the trees part.
  return clone.innerHTML.replace(/></g, '>\n<');
}

let target: HTMLDivElement;
let mounted: ReturnType<typeof mount>[] = [];
let txHarness: ManagedAppTxHarness;

/** `MemorySurface` reaches the DOM only through the wiring's `hostedMemory`
 *  snippet, so it is mounted alongside with the same props the wiring passes. */
function render(freshness: 'fresh' | 'stale'): HTMLElement {
  h.state = liveState(freshness);
  target = document.createElement('div');
  document.body.appendChild(target);
  mounted = [mount(SemanticRadioSurfaces, { target, props: { strips: 'single' } })];
  flushSync();
  mounted.push(mount(MemorySurface, { target, props: { facts: deriveMemoryPanelProps() } }));
  flushSync();
  return target;
}

function teardown(): void {
  for (const instance of mounted.reverse()) unmount(instance);
  mounted = [];
  document.body.innerHTML = '';
}

beforeEach(() => {
  txHarness = new ManagedAppTxHarness();
  h.txController = txHarness.controller;
  h.caps = liveCaps();
});

afterEach(() => {
  teardown();
  expect(h.authoritySubscribers.size).toBe(0);
});

describe('MOR-2425/R41 — freshness is invisible in the semantic tree', () => {
  /** Without this the comparison below would be vacuous for a missing surface. */
  function assertPopulated(root: HTMLElement, freshness: string): void {
    for (const testid of SURFACES) {
      expect(root.querySelector(`[data-testid="${testid}"]`), `${testid} @ ${freshness}`).not.toBeNull();
    }
    // Showing the held values, and the rows this contract is about are real.
    expect(root.textContent, freshness).toContain('14.250.000');
    expect(root.textContent, freshness).toContain('2400');
    expect(root.querySelectorAll('[data-testid="receiver-s-meter"]'), freshness).toHaveLength(2);
    expect(root.querySelectorAll('[data-indicator-fact="rf-gain"]'), freshness).toHaveLength(2);
    expect(root.querySelectorAll('[data-testid="filter-pbtInner"]'), freshness).toHaveLength(1);
    for (const token of ['\u2020', '\u25f7', 'too old to trust', 'Stale observation']) {
      expect(root.textContent, `${token} @ ${freshness}`).not.toContain(token);
    }
  }

  /**
   * THE ONE DIFFERENCE LEFT: the wiring's PBT presentation-continuity effect
   * (`SemanticRadioSurfaces.svelte`, the `pbtEvidence()` `$effect`) rewrites
   * `pbtInner`/`pbtOuter` to `reading: unknown` + `operational: false` for any
   * evidence status but `'fresh'`, disabling the two sliders on a held reading
   * whatever `FilterSurface.svelte: pbtUsable` decides. That mechanism is
   * outside MOR-2425's rulings and is left unfixed; pinning the residue makes
   * a NEW difference fail, and makes fixing that mechanism update this row.
   */
  it('renders the identical visible DOM, but for the two PBT sliders the continuity effect still disables', () => {
    const liveRoot = render('fresh');
    assertPopulated(liveRoot, 'fresh');
    const live = visibleShape(liveRoot).split('\n');
    const liveNames = ariaLabels(liveRoot);
    teardown();
    const heldRoot = render('stale');
    assertPopulated(heldRoot, 'stale');
    const held = visibleShape(heldRoot).split('\n');

    // Accessible names are compared past the strip: `aria-label` is where a
    // freshness cue would otherwise survive it.
    expect(ariaLabels(heldRoot)).toEqual(liveNames);
    expect(held).toHaveLength(live.length);
    const differing = live
      .map((line, index) => ({ live: line, held: held[index] }))
      .filter((row) => row.live !== row.held);
    expect(differing.map((row) => row.held))
      .toEqual(differing.map((row) => row.live.replace('>', ' disabled="">')));
    expect(differing).toHaveLength(2);
    for (const row of differing) {
      expect(row.live).toMatch(/^<input type="range" [^>]*min="-1200" max="1200" step="25">$/);
    }
  });
});
