/**
 * MOR-1305 — the semantic DSP surface wired into `SemanticRadioSurfaces`.
 *
 * The unit tests in `semantic/__tests__/DspSurface.test.ts` prove what the
 * surface does with a view model and plain handler props. This file proves
 * the thing only the composed tree can prove:
 *   (a) the structural gate and default-path byte-identity, same discipline
 *       as MOR-1265/MOR-1273's own wiring tests;
 *   (b) every dsp intent reaches its OWN command-bus handler (no transposed
 *       field, matching `tx-aux-command-bus.test.ts`'s discipline);
 *   (c) `agcLabels`/`nbLevelMax`/`nbLevelPercent` are read off `runtime.caps`
 *       at THIS seam and handed down as plain props — carry-forward (1).
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
  nrMode: vi.fn(),
  nrLevel: vi.fn(),
  nbToggle: vi.fn(),
  nbLevel: vi.fn(),
  nbDepth: vi.fn(),
  nbWidth: vi.fn(),
  notchMode: vi.fn(),
  notchFreq: vi.fn(),
  manualNotchWidth: vi.fn(),
  agcTime: vi.fn(),
  agcMode: vi.fn(),
}));

vi.mock('$lib/runtime', () => ({
  runtime: {
    get state() { return h.state; },
    get caps() { return h.caps; },
    // MOR-1279 slice 3B: the wiring now also hands the adapter an App-owned
    // RX-audio snapshot (the FOURTH argument). Muted with no browser stream
    // keeps every fixture below off the rxAudio path — this file tests dsp.
    get audio() { return { muted: true, rxEnabled: false, volume: 0 }; },
    get connectionAudio() { return false; },
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
// The names below are the REAL `makeDspHandlers`/`makeAgcHandlers` surface —
// agreement with the shipped module is proven separately, against the real
// module, in `command-bus.test.ts`.
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
  // MOR-1279 slice 3B: the RX-audio intent vocabulary. This fixture declares
  // no rxAudio capability, so none of these is reachable — same stand-in role
  // as `makeVfoHandlers`/`makeTxHandlers` above.
  makeRxAudioHandlers: () => ({ onMonitorModeChange: h.noop, onAfLevelChange: h.noop }),
  makeAudioRoutingHandlers: () => ({ onFocusChange: h.noop, onSplitStereoChange: h.noop }),
  // MOR-1304 — the wiring now also composes the modeFilter/filterPassband
  // intent vocabulary; `makeModeHandlers` is composed at both call sites
  // (rxAudio's MOD-input remedy and filterIntents), so the stub carries both.
  // This fixture declares no filter capability, so none of these is
  // reachable — same stand-in role as `makeVfoHandlers`/`makeTxHandlers` above.
  makeModeHandlers: () => ({
    onModInputChange: h.noop, onModeChange: h.noop, onDataModeChange: h.noop,
  }),
  makeFilterHandlers: () => ({
    onFilterChange: h.noop, onFilterWidthChange: h.noop, onFilterShapeChange: h.noop,
    onIfShiftChange: h.noop, onPbtInnerChange: h.noop, onPbtOuterChange: h.noop,
  }),
  makeDspHandlers: () => ({
    onNrModeChange: h.nrMode, onNrLevelChange: h.nrLevel, onNbToggle: h.nbToggle,
    onNbLevelChange: h.nbLevel, onNbDepthChange: h.nbDepth, onNbWidthChange: h.nbWidth,
    onNotchModeChange: h.notchMode, onNotchFreqChange: h.notchFreq,
    onManualNotchWidthChange: h.manualNotchWidth, onAgcTimeChange: h.agcTime,
  }),
  makeAgcHandlers: () => ({ onAgcModeChange: h.agcMode }),
  // MOR-1306 — the wiring now also composes the RF-front-end intent
  // vocabulary. This fixture declares no RF-front-end capability, so none of
  // these is reachable — same stand-in role as `makeVfoHandlers`/
  // `makeTxHandlers` above.
  makeRfFrontEndHandlers: () => ({
    onAttChange: h.noop, onPreChange: h.noop, onRfGainChange: h.noop,
    onSquelchChange: h.noop, onDigiSelToggle: h.noop, onIpPlusToggle: h.noop,
  }),
  // MOR-1307 slice 7B: the band-select intent the band surface composes.
  // This fixture declares no band capability, so it is never reachable —
  // same stand-in role as `makeVfoHandlers`/`makeTxHandlers` above.
  makeBandHandlers: () => ({ onBandSelect: h.noop }),
  // MOR-1309 slice 8C: the wiring now also composes the antenna intent
  // vocabulary unconditionally. This fixture declares no antenna capability,
  // so none of these is reachable — same stand-in role as `makeBandHandlers`.
  makeAntennaHandlers: () => ({ onSelectAnt1: h.noop, onSelectAnt2: h.noop, onToggleRxAnt: h.noop }),
  // MOR-1308 — the wiring now also composes the RIT/XIT and scan intent
  // vocabularies. This fixture declares no rit/xit capability or scan
  // evidence, so none of these is reachable — same stand-in role as
  // `makeVfoHandlers`/`makeTxHandlers` above.
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

/** Every raw dsp field the MOR-1290 adapter reads, all observed fresh. */
const DSP_STATE = {
  nr: true, nrLevel: 128, nb: false, nbLevel: 64, nbDepth: 4, nbWidth: 2,
  autoNotch: false, manualNotch: false, notchFilter: 0, manualNotchWidth: 1,
  agc: 2, agcTimeConstant: 3,
} as const;
const DSP_PATHS = [
  'main.nr', 'main.nrLevel', 'main.nb', 'main.nbLevel', 'main.autoNotch',
  'main.manualNotch', 'main.manualNotchWidth', 'main.agc', 'main.agcTimeConstant',
  'nbDepth', 'nbWidth', 'notchFilter',
];

function liveState(withDsp: boolean): ServerState {
  const paths = ['active', 'split', 'dualWatch', 'txTarget'];
  for (const rx of ['main', 'sub']) {
    paths.push(`${rx}.freqHz`, `${rx}.mode`, `${rx}.filter`, `${rx}.activeSlot`);
    for (const v of ['vfoA', 'vfoB']) {
      paths.push(`${rx}.${v}.freqHz`, `${rx}.${v}.mode`, `${rx}.${v}.filterNum`);
    }
  }
  if (withDsp) paths.push(...DSP_PATHS);
  const receiver = (hz: number) => ({
    ...slot(hz), vfoA: slot(hz), vfoB: slot(hz + 50000), activeSlot: 'A', filter: 1,
    ...(withDsp ? { nr: DSP_STATE.nr, nrLevel: DSP_STATE.nrLevel, nb: DSP_STATE.nb,
      nbLevel: DSP_STATE.nbLevel, autoNotch: DSP_STATE.autoNotch, manualNotch: DSP_STATE.manualNotch,
      manualNotchWidth: DSP_STATE.manualNotchWidth, agc: DSP_STATE.agc,
      agcTimeConstant: DSP_STATE.agcTimeConstant } : {}),
  });
  return {
    active: 'MAIN', split: false, dualWatch: false, ptt: false,
    txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 14250000 },
    main: receiver(14250000), sub: receiver(14300000),
    ...(withDsp ? { nbDepth: DSP_STATE.nbDepth, nbWidth: DSP_STATE.nbWidth, notchFilter: DSP_STATE.notchFilter } : {}),
    fieldStatus: Object.fromEntries(paths.map((p) => [p, fresh])),
  } as unknown as ServerState;
}

const liveCaps = (withDsp: boolean): Capabilities => ({
  model: 'fixture', scope: false, audio: true, tx: true,
  capabilities: withDsp ? ['audio', 'tx', 'dual_rx', 'nr', 'nb', 'notch', 'agc'] : ['audio', 'tx', 'dual_rx'],
  receivers: 2, vfoScheme: 'main_sub', freqRanges: [], modes: [], filters: [],
  audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
  webrtc: { available: false, enabled: false },
  txBands: [{ start: 14000000, end: 14350000, name: '20m' }],
  scopeSource: null, audioFftAvailable: false,
  agcModes: [1, 2, 3], agcLabels: { '1': 'FAST', '2': 'MID', '3': 'SLOW' },
  ...(withDsp ? { controls: {
    nb_level: { raw_min: 0, raw_max: 200, display_min: 0, display_max: 100 },
    nb_depth: { raw_min: 0, raw_max: 9, display_min: 1, display_max: 10 },
  } } : {}),
} as unknown as Capabilities);

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;

function render(props: { strips?: 'single' | 'dual' } = {}): void {
  target = document.createElement('div');
  document.body.appendChild(target);
  component = mount(SemanticRadioSurfaces, { target, props });
  flushSync();
}

const q = <T extends HTMLElement>(sel: string) => target.querySelector(sel) as T | null;

beforeEach(() => {
  h.state = liveState(true);
  h.caps = liveCaps(true);
  h.snapshot = { ...IDLE };
  h.listeners.clear();
  for (const value of Object.values(h)) {
    if (typeof value === 'function' && 'mockReset' in value) (value as ReturnType<typeof vi.fn>).mockReset();
  }
});

afterEach(() => {
  if (component) unmount(component);
  component = null;
  document.body.innerHTML = '';
});

describe('the dsp surface mounts only when the view model carries the group', () => {
  /**
   * The default/single-composition element sequence with NO dsp group —
   * i.e. "today" minus this slice. `liveCaps(false)` still declares the
   * `audio` capability, so `vfo-ops`/`vfo-split-digest` (unrelated VFO
   * ops row) and the full `rxAudio` surface (MOR-1279) are both present —
   * this list pins the CURRENT baseline, not a dsp-slice invention.
   */
  const DEFAULT_PATH_TESTIDS = [
    'vfo-surface', 'vfo-active-receiver', 'vfo-list', 'vfo-ops', 'vfo-split-digest',
    'rx-tx-surface', 'rx-tx-state', 'rx-tx-rf-mark', 'rx-tx-rf-label',
    'rx-tx-target', 'rx-tx-key', 'rx-tx-unkey', 'rx-tx-blocked',
    'rx-audio-surface', 'rx-audio-monitor', 'rx-audio-monitor-local',
    'rx-audio-monitor-live', 'rx-audio-monitor-mute', 'rx-audio-af', 'rx-audio-af-value',
    'rx-audio-focus', 'rx-audio-focus-main', 'rx-audio-focus-sub', 'rx-audio-focus-both',
    'rx-audio-focus-value', 'rx-audio-split', 'rx-audio-split-on', 'rx-audio-split-off',
    'rx-audio-split-value',
  ];
  const testids = () => [...target.querySelectorAll<HTMLElement>('[data-testid]')]
    .map((el) => el.dataset.testid!)
    .filter((id) => id !== 'semantic-radio-surfaces');

  it.each(['single', 'dual'] as const)('renders no dsp surface at all without the group (%s)', (strips) => {
    h.state = liveState(false);
    h.caps = liveCaps(false);
    render({ strips });
    expect(q('[data-testid="dsp-surface"]')).toBeNull();
    expect(target.innerHTML).not.toContain('dsp-surface');
  });

  it('leaves the default path element sequence exactly as it is today', () => {
    h.state = liveState(false);
    h.caps = liveCaps(false);
    render();
    expect(testids()).toEqual(DEFAULT_PATH_TESTIDS);
  });

  it('mounts the dsp surface when the group is present (single)', () => {
    render({ strips: 'single' });
    expect(target.querySelectorAll('[data-testid="dsp-surface"]')).toHaveLength(1);
  });

  it('renders it bare in the single composition, outside every zone', () => {
    render({ strips: 'single' });
    const surface = q('[data-testid="dsp-surface"]')!;
    expect(surface).not.toBeNull();
    expect(surface.closest('[data-zone-id]')).toBeNull();
  });

  /**
   * MOR-1304/MOR-1305 zone-mount ruling (inverted from the pre-fix-round
   * shape, which blessed a bare dual mount). `DspSurface` renders up to 8
   * range inputs and 7 buttons — it is control-bearing, and the cockpit's
   * MOR-1069 rule forbids mounting any control-bearing surface bare in the
   * dual composition: every focusable control must live inside a declared
   * zone, with rx-tx last in the tab order. No manifest declares a `dsp`
   * zone, so the dual composition renders NO dsp surface at all — same
   * precedent as `rxAudioSurface`
   * (`semantic-rx-audio-wiring.component.test.ts`). The view model here DOES
   * carry the `dsp` group (see `beforeEach`) — a fixture that cannot see the
   * surface would repeat the bug this fix closes rather than proving its
   * absence.
   */
  it('renders NO dsp surface in the dual composition, zoned or unzoned', () => {
    render({ strips: 'dual' });
    expect(q('[data-testid="dsp-surface"]')).toBeNull();
    expect(target.innerHTML).not.toContain('dsp-surface');
  });

  it('leaves the cockpit with no focusable control outside a declared zone', () => {
    render({ strips: 'dual' });
    const outside = [...target.querySelectorAll<HTMLElement>('button, input, select, [tabindex]')]
      .filter((node) => node.closest('[data-zone-id]') === null);
    expect(outside).toEqual([]);
  });
});

describe('every dsp intent reaches its own command-bus handler', () => {
  it.each([
    ['nrActive', () => h.nrMode], ['nbActive', () => h.nbToggle],
  ] as const)('routes the "%s" toggle', (field, spy) => {
    render();
    q<HTMLButtonElement>(`[data-testid="dsp-${field}"]`)!.click();
    flushSync();
    expect(spy()).toHaveBeenCalledOnce();
  });

  it.each([
    ['nrLevel', 5, () => h.nrLevel], ['nbLevel', 30, () => h.nbLevel],
    ['nbDepth', 3, () => h.nbDepth], ['nbWidth', 100, () => h.nbWidth],
    ['notchFreq', 1200, () => h.notchFreq], ['manualNotchWidth', 2, () => h.manualNotchWidth],
    ['agcTimeConstant', 4, () => h.agcTime],
  ] as const)('routes the "%s" level with its raw value', (field, value, spy) => {
    render();
    const input = q<HTMLInputElement>(`[data-testid="dsp-${field}"] input`)!;
    input.value = String(value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(spy()).toHaveBeenCalledExactlyOnceWith(value);
  });

  it('routes notchMode as its own three-way callback, not the level/toggle map', () => {
    render();
    q<HTMLButtonElement>('[data-testid="dsp-notchMode-auto"]')!.click();
    flushSync();
    expect(h.notchMode).toHaveBeenCalledExactlyOnceWith('auto');
  });

  it('routes agcMode as its own callback, not the level/toggle map', () => {
    render();
    q<HTMLButtonElement>('[data-testid="dsp-agcMode-1"]')!.click();
    flushSync();
    expect(h.agcMode).toHaveBeenCalledExactlyOnceWith(1);
  });
});

describe('carry-forward (1): caps-echo display metadata is read at this seam', () => {
  it('passes agcLabels/nbLevelMax/nbLevelPercent from runtime.caps down as props', () => {
    render();
    expect(q('[data-testid="dsp-agcMode-1"]')!.textContent).toBe('FAST');
    const input = q<HTMLInputElement>('[data-testid="dsp-nbLevel"] input')!;
    expect(input.max).toBe('200');
  });

  it('falls back to the toDspProps defaults when caps carries no nb_level range', () => {
    h.caps = { ...liveCaps(true), controls: undefined } as unknown as Capabilities;
    render();
    const input = q<HTMLInputElement>('[data-testid="dsp-nbLevel"] input')!;
    expect(input.max).toBe('10');
  });
});
