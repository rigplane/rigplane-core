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
  runtime: { get state() { return h.state; }, get caps() { return h.caps; } },
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
  makeDspHandlers: () => ({
    onNrModeChange: h.nrMode, onNrLevelChange: h.nrLevel, onNbToggle: h.nbToggle,
    onNbLevelChange: h.nbLevel, onNbDepthChange: h.nbDepth, onNbWidthChange: h.nbWidth,
    onNotchModeChange: h.notchMode, onNotchFreqChange: h.notchFreq,
    onManualNotchWidthChange: h.manualNotchWidth, onAgcTimeChange: h.agcTime,
  }),
  makeAgcHandlers: () => ({ onAgcModeChange: h.agcMode }),
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
  const DEFAULT_PATH_TESTIDS = [
    'vfo-surface', 'vfo-active-receiver', 'vfo-list',
    'rx-tx-surface', 'rx-tx-state', 'rx-tx-rf-mark', 'rx-tx-rf-label',
    'rx-tx-target', 'rx-tx-key', 'rx-tx-unkey', 'rx-tx-blocked',
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

  it.each(['single', 'dual'] as const)('mounts the dsp surface when the group is present (%s)', (strips) => {
    render({ strips });
    expect(target.querySelectorAll('[data-testid="dsp-surface"]')).toHaveLength(1);
  });

  it('binds no zone id to the dsp surface in either composition', () => {
    render({ strips: 'dual' });
    const zones = [...target.querySelectorAll<HTMLElement>('[data-zone-id]')]
      .map((el) => el.dataset.zoneId);
    expect(zones).toEqual(['primary-vfo', 'secondary-vfo', 'global', 'rx-tx']);
    expect(q('[data-testid="dsp-surface"]')!.closest('[data-zone-id]')).toBeNull();
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
