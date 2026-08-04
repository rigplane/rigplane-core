/**
 * MOR-1265 — the semantic TX-auxiliary surface wired into `SemanticRadioSurfaces`.
 *
 * SAFETY-CRITICAL, for two independent reasons:
 *   (a) ATU **TUNE** emits a carrier. The wiring is the last gate before the
 *       command leaves the browser, and it must consult the LIVE App TX
 *       authority snapshot — not the one the last render happened to see.
 *   (b) The default path must stay byte-identical. `SemanticRadioSurfaces`
 *       mounts on sdr-test, both LCD layouts, mobile and the cockpit; a view
 *       model with no `txAux` group (every radio the MOR-1244 evidence gate
 *       declines) must render exactly the element shape it renders today.
 *
 * The controller here is a spy; the surfaces are the real ones.
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
  atuToggle: vi.fn(),
  atuTune: vi.fn(),
  voxToggle: vi.fn(),
  compToggle: vi.fn(),
  monToggle: vi.fn(),
  rfPower: vi.fn(),
  micGain: vi.fn(),
  driveGain: vi.fn(),
  voxGain: vi.fn(),
  antiVoxGain: vi.fn(),
  voxDelay: vi.fn(),
  compLevel: vi.fn(),
  monLevel: vi.fn(),
  noop: vi.fn(),
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
// The names below are the REAL `makeTxHandlers`/`makeVoxHandlers` surface —
// agreement with the shipped module is proven separately, against the real
// module, in `tx-aux-command-bus.test.ts`.
vi.mock('../command-bus', () => ({
  makeVfoHandlers: () => ({
    onVfoSelect: h.noop, onSplitToggle: h.noop, onDualWatchToggle: h.noop,
  }),
  makeVoxHandlers: () => ({
    onVoxToggle: h.voxToggle, onVoxGainChange: h.voxGain,
    onAntiVoxGainChange: h.antiVoxGain, onVoxDelayChange: h.voxDelay,
  }),
  makeTxHandlers: () => ({
    onRfPowerChange: h.rfPower, onMicGainChange: h.micGain, onAtuToggle: h.atuToggle,
    onAtuTune: h.atuTune, onVoxToggle: h.voxToggle, onCompToggle: h.compToggle,
    onCompLevelChange: h.compLevel, onMonToggle: h.monToggle,
    onMonLevelChange: h.monLevel, onDriveGainChange: h.driveGain,
  }),
}));

import SemanticRadioSurfaces from '../SemanticRadioSurfaces.svelte';

const IDLE: Snapshot = {
  phase: 'idle', intent: null, guard: null, radioTx: 'off', txRisk: 'none',
  mayOwnKey: false, fault: null,
};

const fresh = { storePath: 'x', observed: true, freshness: 'fresh', availability: 'available' };
const slot = (freqHz: number) => ({ freqHz, mode: 'USB', filterNum: 1, dataMode: 0 });

/** Every txAux raw field the MOR-1244 adapter reads, all observed fresh. */
const TX_AUX_STATE = {
  tunerStatus: 0, voxOn: false, voxGain: 50, antiVoxGain: 30, voxDelay: 10,
  compressorOn: false, compressorLevel: 40, monitorOn: false, monitorGain: 60,
  powerLevel: 0.8, micGain: 128, driveGain: 128,
} as const;
const TX_AUX_PATHS = [
  'tunerStatus', 'voxOn', 'voxGain', 'antiVoxGain', 'voxDelay', 'compressorOn',
  'compressorLevel', 'monitorOn', 'monitorGain', 'powerLevel', 'micGain', 'driveGain',
];

function liveState(withTxAux: boolean): ServerState {
  const paths = ['active', 'split', 'dualWatch', 'txTarget'];
  for (const rx of ['main', 'sub']) {
    paths.push(`${rx}.freqHz`, `${rx}.mode`, `${rx}.filter`, `${rx}.activeSlot`);
    for (const v of ['vfoA', 'vfoB']) {
      paths.push(`${rx}.${v}.freqHz`, `${rx}.${v}.mode`, `${rx}.${v}.filterNum`);
    }
  }
  if (withTxAux) paths.push(...TX_AUX_PATHS);
  const receiver = (hz: number) => ({
    ...slot(hz), vfoA: slot(hz), vfoB: slot(hz + 50000), activeSlot: 'A', filter: 1,
  });
  return {
    active: 'MAIN', split: false, dualWatch: false, ptt: false,
    txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 14250000 },
    main: receiver(14250000), sub: receiver(14300000),
    ...(withTxAux ? TX_AUX_STATE : {}),
    fieldStatus: Object.fromEntries(paths.map((p) => [p, fresh])),
  } as unknown as ServerState;
}

const liveCaps = (withTxAux: boolean): Capabilities => ({
  model: 'fixture', scope: false, audio: true, tx: true,
  capabilities: withTxAux
    ? ['audio', 'tx', 'dual_rx', 'vox', 'compressor', 'monitor', 'tuner', 'drive_gain']
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

// ── 1. The structural gate: absent group ⇒ no surface, no element drift ────

describe('the txAux surface mounts only when the view model carries the group', () => {
  /**
   * The element shape of the default (single) path, as a LITERAL. Every entry
   * is `tagName[data-testid]`, depth-first over `.semantic-surfaces`.
   *
   * MUTATION KILLED: mounting `TxAuxSurface` unconditionally (dropping the
   * `{#if view.txAux}` structural gate), or wrapping it in a zone shell that
   * every path renders — either changes this sequence for a radio whose
   * MOR-1244 evidence gate declined the group, i.e. for the byte-identical
   * default path this slice promised not to touch.
   */
  const DEFAULT_PATH_TESTIDS = [
    'vfo-surface', 'vfo-active-receiver', 'vfo-list',
    'rx-tx-surface', 'rx-tx-state', 'rx-tx-rf-mark', 'rx-tx-rf-label',
    'rx-tx-target', 'rx-tx-key', 'rx-tx-unkey', 'rx-tx-blocked',
  ];

  const testids = () => [...target.querySelectorAll<HTMLElement>('[data-testid]')]
    .map((el) => el.dataset.testid!)
    .filter((id) => id !== 'semantic-radio-surfaces');
  /** Every element under the root, in document order — the identity probe
   *  proper: a mount that renders nothing still cannot slip past this. */
  const outline = () => [...q('[data-testid="semantic-radio-surfaces"]')!.querySelectorAll('*')]
    .map((el) => el.tagName.toLowerCase()).join(' ');
  const DEFAULT_PATH_OUTLINE = 'div p div div span span span span span div span span span button '
    + 'div span span span button div span span span button div button button '
    + 'section p span span span span p div button button ul';

  it.each(['single', 'dual'] as const)('renders no txAux surface at all without the group (%s)', (strips) => {
    h.state = liveState(false);
    h.caps = liveCaps(false);
    render({ strips });
    expect(q('[data-testid="tx-aux-surface"]')).toBeNull();
    expect(q('[data-testid="tx-aux-atu-tune"]')).toBeNull();
    expect(target.innerHTML).not.toContain('tx-aux');
  });

  it('leaves the default path element sequence exactly as it is today', () => {
    h.state = liveState(false);
    h.caps = liveCaps(false);
    render();
    expect(testids()).toEqual(DEFAULT_PATH_TESTIDS);
    expect(outline()).toBe(DEFAULT_PATH_OUTLINE);
  });

  it.each(['single', 'dual'] as const)('mounts the txAux surface when the group is present (%s)', (strips) => {
    render({ strips });
    expect(q('[data-testid="tx-aux-surface"]')).not.toBeNull();
    expect(target.querySelectorAll('[data-testid="tx-aux-surface"]')).toHaveLength(1);
  });

  // MUTATION KILLED: giving the txAux surface a `data-zone-id` here. Zone
  // declarability comes from `SEMANTIC_SURFACE_NAMES`; no manifest declares a
  // txAux zone in this slice, so binding one would put a zone id in the DOM
  // that no layout manifest ever declared (the MOR-1069 lesson).
  it('binds no zone id to the txAux surface in either composition', () => {
    render({ strips: 'dual' });
    const zones = [...target.querySelectorAll<HTMLElement>('[data-zone-id]')]
      .map((el) => el.dataset.zoneId);
    expect(zones).toEqual(['primary-vfo', 'secondary-vfo', 'global', 'rx-tx']);
    expect(q('[data-testid="tx-aux-surface"]')!.closest('[data-zone-id]')).toBeNull();
  });
});

// ── 2. Still exactly ONE key path (safety note iii) ────────────────────────

describe('the txAux surface does not become a second key path', () => {
  // MUTATION KILLED: a TxAuxSurface variant that renders a key control, or a
  // wiring change that mounts a second RxTxSurface alongside it.
  it('keeps exactly one key/unkey authority in the composed tree', () => {
    render();
    expect(target.querySelectorAll('[data-testid="rx-tx-surface"]')).toHaveLength(1);
    expect(target.querySelectorAll('[data-testid="rx-tx-key"]')).toHaveLength(1);
    expect(target.querySelectorAll('[data-testid="rx-tx-unkey"]')).toHaveLength(1);
  });

  it('never starts or releases a TX lease from a txAux intent', () => {
    render();
    q<HTMLButtonElement>('[data-testid="tx-aux-atu-tune"]')!.click();
    q<HTMLButtonElement>('[data-testid="tx-aux-vox"]')!.click();
    flushSync();
    expect(h.atuTune).toHaveBeenCalledOnce();
    expect(h.start).not.toHaveBeenCalled();
    expect(h.release).not.toHaveBeenCalled();
  });
});

// ── 3. ATU TUNE routes through the App-owned TX authority ──────────────────

const BLOCKING: readonly (readonly [string, Partial<Snapshot>])[] = [
  ['a fault is latched', { fault: 'on-timeout' }],
  ['a lease is in progress', { phase: 'key-confirm-pending' }],
  ['this browser may own the key', { mayOwnKey: true }],
  ['the radio is already transmitting', { radioTx: 'on' }],
  ['the RF state is unknown', { radioTx: 'unknown' }],
  ['TX risk is uncertain', { txRisk: 'uncertain' }],
];

describe('ATU TUNE is gated by the live App TX authority', () => {
  it('dispatches the tune command when nothing blocks a key intent', () => {
    render();
    const tune = q<HTMLButtonElement>('[data-testid="tx-aux-atu-tune"]')!;
    expect(tune.disabled).toBe(false);
    tune.click();
    flushSync();
    expect(h.atuTune).toHaveBeenCalledOnce();
  });

  it.each(BLOCKING)('disables and refuses TUNE while %s', (_label, over) => {
    render();
    push(over);
    const tune = q<HTMLButtonElement>('[data-testid="tx-aux-atu-tune"]')!;
    expect(tune.disabled).toBe(true);
    tune.disabled = false; // a restyled / programmatically enabled control
    tune.click();
    flushSync();
    expect(h.atuTune).not.toHaveBeenCalled();
  });

  // MUTATION KILLED: guarding on the snapshot captured at render time instead
  // of reading the authority NOW. The transmitter can start between the last
  // render and the click; a stale snapshot would let TUNE fire into it. Same
  // discipline as `requestUnkey` reading the live guard.
  it('refuses a tune against an authority state the render never saw', () => {
    render();
    const tune = q<HTMLButtonElement>('[data-testid="tx-aux-atu-tune"]')!;
    // Mutate the authority WITHOUT notifying subscribers: no re-render runs.
    h.snapshot = { ...(h.snapshot as Snapshot), radioTx: 'on' };
    expect(tune.disabled).toBe(false);
    tune.click();
    flushSync();
    expect(h.atuTune).not.toHaveBeenCalled();
  });

  // MUTATION KILLED: gating the ordinary (non-transmitting) ATU on/off toggle
  // on TX authority too. It sets a tuner mode, it does not emit a carrier —
  // over-gating would strand the operator with an ATU they cannot turn off.
  it('leaves the non-transmitting ATU toggle usable while TUNE is blocked', () => {
    render();
    push({ radioTx: 'on' });
    expect(q<HTMLButtonElement>('[data-testid="tx-aux-atu-tune"]')!.disabled).toBe(true);
    const atu = q<HTMLButtonElement>('[data-testid="tx-aux-atu"]')!;
    expect(atu.disabled).toBe(false);
    atu.click();
    flushSync();
    expect(h.atuToggle).toHaveBeenCalledOnce();
  });
});

// ── 4. Intents reach the mapped command-bus handler ────────────────────────

describe('every txAux intent reaches its own command-bus handler', () => {
  it.each([
    ['atu', () => h.atuToggle], ['vox', () => h.voxToggle],
    ['compressor', () => h.compToggle], ['monitor', () => h.monToggle],
  ] as const)('routes the "%s" toggle', (field, spy) => {
    render();
    q<HTMLButtonElement>(`[data-testid="tx-aux-${field}"]`)!.click();
    flushSync();
    expect(spy()).toHaveBeenCalledOnce();
  });

  // MUTATION KILLED: a transposed or duplicated entry in the level intent
  // map — e.g. mic gain wired to the drive-gain command. Each case asserts
  // its own spy fired AND that it is the only one that did.
  it.each([
    ['rfPower', 0.5, () => h.rfPower], ['micGain', 200, () => h.micGain],
    ['driveGain', 100, () => h.driveGain], ['voxGain', 77, () => h.voxGain],
    ['antiVoxGain', 12, () => h.antiVoxGain], ['voxDelay', 5, () => h.voxDelay],
    ['compressorLevel', 33, () => h.compLevel], ['monitorLevel', 99, () => h.monLevel],
  ] as const)('routes the "%s" level with its raw value', (field, value, spy) => {
    render();
    const input = q<HTMLInputElement>(`[data-testid="tx-aux-${field}"] input`)!;
    input.value = String(value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(spy()).toHaveBeenCalledExactlyOnceWith(value);
    const others = [h.rfPower, h.micGain, h.driveGain, h.voxGain, h.antiVoxGain,
      h.voxDelay, h.compLevel, h.monLevel].filter((s) => s !== spy());
    for (const other of others) expect(other).not.toHaveBeenCalled();
  });
});
