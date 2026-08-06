/**
 * MOR-1273 — the semantic meters surface wired into `SemanticRadioSurfaces`.
 *
 * The unit tests in `semantic/__tests__/MetersSurface.test.ts` prove what the
 * surface does with a view model. This file proves the thing only the composed
 * tree can prove: WHERE the meters' TX truth comes from.
 *
 *   (a) MOR-1235 must stay fixed. The whole chain here is real — the shipped
 *       `radio-view-model-adapter`, the real `meters` evidence gate, the real
 *       surface — and only the runtime/authority seams are spies. So the two
 *       decisive experiments are possible: move the raw transmit wire bit and
 *       nothing about the meters may change; move the App TX authority and
 *       everything must.
 *   (b) The default path must stay byte-identical. `SemanticRadioSurfaces`
 *       mounts on sdr-test, both LCD layouts, mobile and the cockpit; a radio
 *       that reports no meters at all must render exactly the element shape it
 *       renders today.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';

type Snapshot = {
  phase: string; intent: string | null; guard: { leaseId: string } | null;
  radioTx: string; txRisk: string; mayOwnKey: boolean; fault: string | null;
};

const h = vi.hoisted(() => ({
  state: null as unknown,
  caps: null as unknown,
  snapshot: null as unknown,
  listeners: new Set<(next: unknown) => void>(),
  start: vi.fn(),
  release: vi.fn(),
  noop: vi.fn(),
}));

vi.mock('$lib/runtime', () => ({
  runtime: {
    get state() { return h.state; },
    get caps() { return h.caps; },
    // MOR-1279 slice 3B: the wiring now also hands the adapter an
    // App-owned RX-audio snapshot (the FOURTH argument). Muted with no
    // browser stream keeps every fixture below on its pre-1279 path.
    get audio() { return { muted: true, rxEnabled: false, volume: 0 }; },
    get connectionAudio() { return false; },
    // MOR-1312 slice 12B: the wiring now also hands the adapter a
    // scope-display snapshot (the FIFTH argument). Every fixture below
    // declares no scope capability, so this stays on its pre-1312 path
    // regardless of these values.
    get defaultScopeStatus() {
      return {
        source: null, available: false, resourceSelected: false, demand: 0,
        lifecycle: 'inactive', transport: 'disconnected', frameSeen: false,
      };
    },
    get radioPowerOn() { return null; },
    get scope() { return { hardwareScopeConnected: false }; },
  },
}));
vi.mock('$lib/runtime/tx-controller/app-host', () => ({
  getAppTxController: () => ({
    snapshot: () => h.snapshot,
    subscribe: (listener: (next: unknown) => void) => {
      h.listeners.add(listener);
      return () => { h.listeners.delete(listener); };
    },
    start: h.start,
    setIntent: vi.fn(),
    release: h.release,
    resetFault: vi.fn(),
  }),
}));
vi.mock('$lib/runtime/adapters/mod-input-tx-guard.svelte', () => ({
  deriveModInputTxGuardProps: () => ({ visible: false, sourceLabel: null }),
  getModInputTxGuardHandlers: () => ({ onSetLan: vi.fn(), onDismiss: vi.fn() }),
}));
vi.mock('../command-bus', () => ({
  makeVfoHandlers: () => ({
    onVfoSelect: h.noop, onSplitToggle: h.noop, onDualWatchToggle: h.noop,
  }),
  makeVoxHandlers: () => ({
    onVoxToggle: h.noop, onVoxGainChange: h.noop,
    onAntiVoxGainChange: h.noop, onVoxDelayChange: h.noop,
  }),
  makeTxHandlers: () => ({
    onRfPowerChange: h.noop, onMicGainChange: h.noop, onAtuToggle: h.noop,
    onAtuTune: h.noop, onVoxToggle: h.noop, onCompToggle: h.noop,
    onCompLevelChange: h.noop, onMonToggle: h.noop,
    onMonLevelChange: h.noop, onDriveGainChange: h.noop,
  }),
  // MOR-1279 slice 3B: the RX-audio intent vocabulary.
  makeRxAudioHandlers: () => ({ onMonitorModeChange: h.noop, onAfLevelChange: h.noop }),
  // MOR-1310 slice 9B: the semantic CW-keyer surface's setting intents.
  makeCwPanelHandlers: () => ({
    onKeySpeedChange: h.noop, onCwPitchChange: h.noop, onBreakInDelayChange: h.noop,
    onBreakInModeChange: h.noop, onApfChange: h.noop, onTwinPeakToggle: h.noop,
    onReversePaddleToggle: h.noop,
  }),
  makeAudioRoutingHandlers: () => ({ onFocusChange: h.noop, onSplitStereoChange: h.noop }),
  // MOR-1304 — the wiring now also composes the modeFilter/filterPassband
  // intent vocabulary; `makeModeHandlers` is composed at both call sites
  // (rxAudio's MOD-input remedy and filterIntents), so the stub carries both.
  makeModeHandlers: () => ({
    onModInputChange: h.noop, onModeChange: h.noop, onDataModeChange: h.noop,
  }),
  makeFilterHandlers: () => ({
    onFilterChange: h.noop, onFilterWidthChange: h.noop, onFilterShapeChange: h.noop,
    onIfShiftChange: h.noop, onPbtInnerChange: h.noop, onPbtOuterChange: h.noop,
  }),
  // MOR-1305 — the wiring now also composes the dsp intent vocabulary. This
  // fixture declares no dsp capability or state, so none of these is reachable.
  makeDspHandlers: () => ({
    onNrModeChange: h.noop, onNrLevelChange: h.noop, onNbToggle: h.noop,
    onNbLevelChange: h.noop, onNbDepthChange: h.noop, onNbWidthChange: h.noop,
    onNotchModeChange: h.noop, onNotchFreqChange: h.noop,
    onManualNotchWidthChange: h.noop, onAgcTimeChange: h.noop,
  }),
  makeAgcHandlers: () => ({ onAgcModeChange: h.noop }),
  // MOR-1306 slice 6B: the RF-front-end intent vocabulary.
  makeRfFrontEndHandlers: () => ({
    onAttChange: h.noop, onPreChange: h.noop, onRfGainChange: h.noop,
    onSquelchChange: h.noop, onDigiSelToggle: h.noop, onIpPlusToggle: h.noop,
  }),
  // MOR-1307 slice 7B: the band-select intent the band surface composes.
  makeBandHandlers: () => ({ onBandSelect: h.noop }),
  // MOR-1309 slice 8C: the antenna intent vocabulary.
  makeAntennaHandlers: () => ({ onSelectAnt1: h.noop, onSelectAnt2: h.noop, onToggleRxAnt: h.noop }),
  // MOR-1308 slice 8B: the RIT/XIT and scan intent vocabularies.
  makeRitXitHandlers: () => ({
    onRitToggle: h.noop, onXitToggle: h.noop, onRitOffsetChange: h.noop,
    onXitOffsetChange: h.noop, onClear: h.noop,
  }),
  makeScanHandlers: () => ({
    onScanStart: h.noop, onScanStop: h.noop, onDfSpanChange: h.noop, onResumeChange: h.noop,
  }),
}));

import SemanticRadioSurfaces from '../SemanticRadioSurfaces.svelte';

const IDLE: Snapshot = {
  phase: 'idle', intent: null, guard: null, radioTx: 'off', txRisk: 'none',
  mayOwnKey: false, fault: null,
};

const fresh = { storePath: 'x', observed: true, freshness: 'fresh', availability: 'available' };
const slot = (freqHz: number) => ({ freqHz, mode: 'USB', filterNum: 1, dataMode: 0 });

/** Every raw meter the MOR-1269 adapter reads, all observed fresh. The
 *  compressor is ON so the txAux-gated COMP tile is reachable. */
const METER_STATE = {
  powerMeter: 120, swrMeter: 30, alcMeter: 40, compMeter: 20,
  vdMeter: 200, idMeter: 90, compressorOn: true, compressorLevel: 40,
} as const;
const METER_PATHS = [
  'powerMeter', 'swrMeter', 'alcMeter', 'compMeter', 'vdMeter', 'idMeter',
  'main.sMeter', 'sub.sMeter', 'compressorOn', 'compressorLevel',
];

function liveState(withMeters: boolean, over: Partial<ServerState> = {}): ServerState {
  const paths = ['active', 'split', 'dualWatch', 'txTarget'];
  for (const rx of ['main', 'sub']) {
    paths.push(`${rx}.freqHz`, `${rx}.mode`, `${rx}.filter`, `${rx}.activeSlot`);
    for (const v of ['vfoA', 'vfoB']) {
      paths.push(`${rx}.${v}.freqHz`, `${rx}.${v}.mode`, `${rx}.${v}.filterNum`);
    }
  }
  if (withMeters) paths.push(...METER_PATHS);
  const receiver = (hz: number) => ({
    ...slot(hz), vfoA: slot(hz), vfoB: slot(hz + 50000), activeSlot: 'A', filter: 1,
    ...(withMeters ? { sMeter: -12 } : {}),
  });
  return {
    active: 'MAIN', split: false, dualWatch: false, ptt: false,
    txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 14250000 },
    main: receiver(14250000), sub: receiver(14300000),
    ...(withMeters ? METER_STATE : {}),
    ...over,
    fieldStatus: Object.fromEntries(paths.map((p) => [p, fresh])),
  } as unknown as ServerState;
}

const liveCaps = (withMeters: boolean): Capabilities => ({
  model: 'fixture', scope: false, audio: true, tx: true,
  capabilities: withMeters
    ? ['audio', 'tx', 'dual_rx', 'compressor']
    : ['audio', 'tx', 'dual_rx'],
  receivers: 2, vfoScheme: 'main_sub', freqRanges: [], modes: [], filters: [],
  audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
  webrtc: { available: false, enabled: false },
  txBands: [{ start: 14000000, end: 14350000, name: '20m' }],
  scopeSource: null, audioFftAvailable: false,
} as unknown as Capabilities);

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;

function render(props: { strips?: 'single' | 'dual' } = {}): void {
  target = document.createElement('div');
  document.body.appendChild(target);
  component = mount(SemanticRadioSurfaces, { target, props });
  flushSync();
}

function push(next: Partial<Snapshot>): void {
  h.snapshot = { ...(h.snapshot as Snapshot), ...next };
  for (const listener of h.listeners) listener(h.snapshot);
  flushSync();
}

const q = <T extends HTMLElement>(sel: string) => target.querySelector(sel) as T | null;
const rfState = (): string | undefined => q('[data-testid="meters-surface"]')!.dataset.rfState;
/** `data-meter -> data-relevant` for every rendered tile. */
const relevance = (): Record<string, string> => Object.fromEntries(
  [...target.querySelectorAll<HTMLElement>('[data-meter-tile]')]
    .map((el) => [el.dataset.meter!, el.dataset.relevant!]),
);

beforeEach(() => {
  h.state = liveState(true);
  h.caps = liveCaps(true);
  h.snapshot = { ...IDLE };
  h.listeners.clear();
  h.start.mockReset();
  h.release.mockReset();
  h.noop.mockReset();
});

afterEach(() => {
  if (component) unmount(component);
  component = null;
  document.body.innerHTML = '';
});

// ── 1. The structural gate: absent group ⇒ no surface, no element drift ────

describe('the meters surface mounts only when the view model carries the group', () => {
  /**
   * The element shape of the default (single) path, as a LITERAL — the same
   * probe MOR-1265 introduced, re-pinned here.
   *
   * MUTATION KILLED: mounting `MetersSurface` unconditionally (dropping the
   * `{#if view.meters}` structural gate), or wrapping it in a shell every path
   * renders — either changes this sequence for a radio whose MOR-1269 evidence
   * gate declined the group.
   */
  const DEFAULT_PATH_TESTIDS = [
    'vfo-surface', 'vfo-active-receiver', 'vfo-list',
    // MOR-1321 (S3a): the VFO ops row and the split RX/TX digest are part of
    // the vfo surface's radio-wide half now, so they belong to the default
    // path's element shape. This fixture's radio is dual-receiver, so the
    // structural gate (more than one VFO) legitimately opens; the single-VFO
    // absence is pinned in `semantic/__tests__/VfoSurface.test.ts`.
    'vfo-ops', 'vfo-split-digest',
    'rx-tx-surface', 'rx-tx-state', 'rx-tx-rf-mark', 'rx-tx-rf-label',
    'rx-tx-target', 'rx-tx-key', 'rx-tx-unkey', 'rx-tx-blocked',
    // MOR-1279 slice 3B: this fixture's radio DOES have an audio chain
    // (`audio` + `dual_rx`), so the rxAudio surface legitimately mounts here.
    // Its own absent-group gate is pinned in
    // `semantic-rx-audio-wiring.component.test.ts`; what this literal still
    // kills is an UNGATED txAux/meters mount.
    'rx-audio-surface', 'rx-audio-monitor',
    'rx-audio-monitor-local', 'rx-audio-monitor-live', 'rx-audio-monitor-mute',
    'rx-audio-af', 'rx-audio-af-value',
    'rx-audio-focus', 'rx-audio-focus-main', 'rx-audio-focus-sub', 'rx-audio-focus-both',
    'rx-audio-focus-value',
    'rx-audio-split', 'rx-audio-split-on', 'rx-audio-split-off', 'rx-audio-split-value',
  ];
  const testids = () => [...target.querySelectorAll<HTMLElement>('[data-testid]')]
    .map((el) => el.dataset.testid!)
    .filter((id) => id !== 'semantic-radio-surfaces');

  it.each(['single', 'dual'] as const)('renders no meters surface at all without the group (%s)', (strips) => {
    h.state = liveState(false);
    h.caps = liveCaps(false);
    render({ strips });
    expect(q('[data-testid="meters-surface"]')).toBeNull();
    expect(target.innerHTML).not.toContain('data-meter-tile');
  });

  it('leaves the default path element sequence exactly as it is today', () => {
    h.state = liveState(false);
    h.caps = liveCaps(false);
    render();
    expect(testids()).toEqual(DEFAULT_PATH_TESTIDS);
  });

  it.each(['single', 'dual'] as const)('mounts the meters surface when the group is present (%s)', (strips) => {
    render({ strips });
    expect(target.querySelectorAll('[data-testid="meters-surface"]')).toHaveLength(1);
    expect(Object.keys(relevance())).toContain('power');
  });

  // MUTATION KILLED: giving the meters surface a `data-zone-id`. `meters` is
  // declarable after this slice, but no manifest declares a meters zone — and
  // the zone schema stays config-free (risk R3).
  it('binds no zone id to the meters surface in either composition', () => {
    render({ strips: 'dual' });
    const zones = [...target.querySelectorAll<HTMLElement>('[data-zone-id]')]
      .map((el) => el.dataset.zoneId);
    expect(zones).toEqual(['primary-vfo', 'secondary-vfo', 'global', 'rx-tx']);
    expect(q('[data-testid="meters-surface"]')!.closest('[data-zone-id]')).toBeNull();
  });
});

// ── 2. Carry-forward (1): TX truth comes from the App authority, not ptt ──

describe('meter TX relevance follows the App TX authority and nothing else', () => {
  // MUTATION KILLED: reading the raw transmit wire bit anywhere on this path
  // (`state.ptt`) — the MOR-1235 disagreement. The bit is flipped to `true`
  // here while the App authority stays idle; the meters must not notice.
  it('ignores the raw transmit wire bit entirely', () => {
    render();
    const before = { rf: rfState(), tiles: relevance() };
    h.state = liveState(true, { ptt: true } as Partial<ServerState>);
    push({});
    expect(rfState()).toBe(before.rf);
    expect(rfState()).toBe('receiving');
    expect(relevance()).toEqual(before.tiles);
  });

  // MUTATION KILLED: any second derivation — a surface that computed relevance
  // itself would not move when the ONLY thing that changed is the authority.
  it('flips every TX-gated meter when the App authority says the radio is transmitting', () => {
    render();
    expect(rfState()).toBe('receiving');
    const rx = relevance();
    expect(rx.signal).toBe('true');
    expect(rx.power).toBe('false');

    push({ radioTx: 'on', txRisk: 'confirmed-on', phase: 'active', mayOwnKey: true });
    expect(rfState()).toBe('transmitting');
    const tx = relevance();
    expect(tx.signal).toBe('false');
    for (const field of ['power', 'swr', 'alc', 'drainCurrent', 'compression']) {
      expect(tx[field]).toBe('true');
    }
    // Vd is the station supply rail, relevant in every RF state.
    expect(tx.drainVoltage).toBe('true');
  });

  // MUTATION KILLED: collapsing 'uncertain' onto 'receiving' — the boolean
  // `txActive` shape the shipped v2 dock uses, and the reason MOR-1235 could
  // hide. An uncertain transmitter must not read as RX.
  it('renders an uncertain transmitter as uncertain, never as receiving', () => {
    render();
    push({ txRisk: 'uncertain' });
    expect(rfState()).toBe('uncertain');
    expect(relevance().power).toBe('true');
  });
});

// ── 3. Carry-forward (4): the cold-start unknown window ──────────────────

describe('the cold-start window renders fail-closed', () => {
  // MUTATION KILLED: defaulting an unknown authority to 'receiving' (RX
  // styling on a radio that may be keyed), or suppressing the surface until
  // the authority speaks (a flash of nothing, then a reflow).
  it('renders the surface with rfState unknown before the authority has spoken', () => {
    h.snapshot = { ...IDLE, radioTx: 'unknown' };
    render();
    expect(q('[data-testid="meters-surface"]')).not.toBeNull();
    expect(rfState()).toBe('unknown');
    expect(Object.keys(relevance()).length).toBeGreaterThan(0);
  });

  // The adapter emits NO meters group without a TX authority snapshot, so the
  // "no honest relevance can be stated" case is absence, not a guess.
  it('never renders RX styling while the RF state is unknown', () => {
    h.snapshot = { ...IDLE, radioTx: 'unknown' };
    render();
    expect(target.innerHTML).not.toContain('data-rf-state="receiving"');
  });
});

// ── 4. R9: the meters surface is a readout, never an action path ─────────

describe('the meters surface adds no control and no TX path', () => {
  it('keeps exactly one key/unkey authority in the composed tree', () => {
    render();
    expect(target.querySelectorAll('[data-testid="rx-tx-surface"]')).toHaveLength(1);
    expect(target.querySelectorAll('[data-testid="rx-tx-key"]')).toHaveLength(1);
    expect(target.querySelectorAll('[data-testid="rx-tx-unkey"]')).toHaveLength(1);
  });

  // MUTATION KILLED: a meters surface that grew a peak-reset / source-select
  // control. Zero controls also keeps the cockpit's focus order and its
  // zone-less-control count exactly as MOR-1069/1070 pinned them.
  it('contributes no focusable control to the composition', () => {
    render({ strips: 'dual' });
    const surface = q('[data-testid="meters-surface"]')!;
    expect(surface.querySelectorAll('button, input, select, a[href], [tabindex]'))
      .toHaveLength(0);
    expect(h.start).not.toHaveBeenCalled();
    expect(h.release).not.toHaveBeenCalled();
  });
});
