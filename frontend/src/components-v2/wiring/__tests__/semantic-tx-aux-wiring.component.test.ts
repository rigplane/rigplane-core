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
  runtime: {
    get state() { return h.state; },
    get caps() { return h.caps; },
    // MOR-1279 slice 3B: the wiring now also hands the adapter an
    // App-owned RX-audio snapshot (the FOURTH argument). Muted with no
    // browser stream keeps every fixture below on its pre-1279 path.
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
// The names below are the REAL `makeTxHandlers`/`makeVoxHandlers` surface —
// agreement with the shipped module is proven separately, against the real
// module, in `tx-aux-command-bus.isolated.test.ts`.
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
  // MOR-1279 slice 3B: the RX-audio intent vocabulary.
  makeRxAudioHandlers: () => ({ onMonitorModeChange: h.noop, onAfLevelChange: h.noop }),
  makeAudioRoutingHandlers: () => ({ onFocusChange: h.noop, onSplitStereoChange: h.noop }),
  makeModeHandlers: () => ({ onModInputChange: h.noop }),
}));

import SemanticRadioSurfaces from '../SemanticRadioSurfaces.svelte';
// MOR-1082: the REAL manifests, through the app-wide registration barrel, and
// the REAL resolution seam — the plans below are what App would hand down.
import {
  dualReceiverCockpitLayout, sdrTestLayout,
} from '../../../presentation/layouts/declarations';
import { readWorkspace } from '../../../presentation/workspace/contract';
import {
  resolveSurfacePlan, SURFACE_PLAN_CONTEXT_KEY, type SurfacePlan,
} from '../../../presentation/workspace/resolution';

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

/**
 * MOR-1082: `plan` is what the composition root (App) resolves and hands down
 * through context. Omitting it is the pre-1082 mount — no plan, everything the
 * composition declares — which every suite above therefore still exercises.
 */
function render(
  props: { strips?: 'single' | 'dual' } = {},
  plan?: SurfacePlan,
): void {
  target = document.createElement('div');
  document.body.appendChild(target);
  const context = plan === undefined
    ? undefined
    : new Map<unknown, unknown>([[SURFACE_PLAN_CONTEXT_KEY, () => plan]]);
  component = mount(SemanticRadioSurfaces, { target, props, context });
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
  /** Every element under the root, in document order — the identity probe
   *  proper: a mount that renders nothing still cannot slip past this. */
  const outline = () => [...q('[data-testid="semantic-radio-surfaces"]')!.querySelectorAll('*')]
    .map((el) => el.tagName.toLowerCase()).join(' ');
  const DEFAULT_PATH_OUTLINE = 'div p div div span span span span span div span span span button '
    + 'div span span span button div span span span button div button button '
    // MOR-1321 (S3a): the ops row (four action buttons) and the split RX/TX
    // digest (a `p` carrying an RX and a TX span), both part of the vfo
    // surface's radio-wide half, immediately after the split/dualWatch toggles.
    + 'div button button button button p span span '
    + 'section p span span span span p div button button ul'
    // MOR-1279 slice 3B: the rxAudio surface, mounted because this fixture's
    // radio has an audio chain. See the testid literal above.
    + ' section div button button button label span input output'
    + ' div button button button output div button button output';

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

// ── MOR-1082: the workspace's per-zone visibility/order, consulted HERE ─────
//
// The plans below are built by the real `resolveSurfacePlan` from a real,
// validated workspace against the real registered manifests, so these probes
// fail if either the manifest or the resolution rules drift — not just if this
// component's wiring does.

describe('MOR-1082 — the semantic vertical consults the resolved surface plan', () => {
  /** What App resolves for `layout` given a stored workspace `fields`. */
  function planFor(layout: typeof sdrTestLayout, fields: Record<string, unknown>): SurfacePlan {
    return resolveSurfacePlan(layout, readWorkspace({ version: 1, ...fields }).workspace);
  }

  const stripIds = () => [...target.querySelectorAll<HTMLElement>('[data-testid^="channel-strip-"]')]
    .map((el) => el.dataset.testid!);
  /** `vfo-surface` / `rx-tx-surface`, in document order — the order probe. */
  const surfaceOrder = () => [...target.querySelectorAll<HTMLElement>(
    '[data-testid="vfo-surface"], [data-testid="rx-tx-surface"]',
  )].map((el) => el.dataset.testid!);

  it('hides a strip the operator switched off, and only that strip', () => {
    // MUTATION KILLED: ignoring `visibleSurfaces` in the dual composition —
    // the SUB strip would still mount. Also kills gating the wrong zone: the
    // MAIN strip, the global row and the RX/TX surface must all survive.
    render({ strips: 'dual' }, planFor(dualReceiverCockpitLayout, {
      visibleSurfaces: { 'secondary-vfo': [] },
    }));

    expect(stripIds()).toEqual(['channel-strip-MAIN']);
    expect(q('[data-testid="cockpit-zone-global"]')).not.toBeNull();
    expect(q('[data-testid="rx-tx-surface"]')).not.toBeNull();
    expect(q('[data-testid="rx-tx-key"]')).not.toBeNull();
  });

  it('renders every declared zone when the operator expressed nothing', () => {
    render({ strips: 'dual' }, planFor(dualReceiverCockpitLayout, {}));

    expect(stripIds()).toEqual(['channel-strip-MAIN', 'channel-strip-SUB']);
    expect(q('[data-testid="cockpit-zone-global"]')).not.toBeNull();
  });

  it('refuses to hide the RX/TX surface — the only unkey affordance', () => {
    // `rxTx` is `requiredSemanticSurfaces` and exactly one zone mounts it, so
    // the plan restores it. MUTATION KILLED: dropping the required-coverage
    // arm of `resolveSurfacePlan`, which would leave a keyed operator with no
    // way to stop transmitting.
    render({ strips: 'dual' }, planFor(dualReceiverCockpitLayout, {
      visibleSurfaces: { 'rx-tx': [] },
    }));

    expect(q('[data-testid="rx-tx-surface"]')).not.toBeNull();
    expect(q('[data-testid="rx-tx-unkey"]')).not.toBeNull();
  });

  it('cannot force-show a surface whose view-model group is absent', () => {
    // The S0 self-gate outranks the workspace. This radio's MOR-1244 evidence
    // gate declined `txAux`; a workspace that names it everywhere it could be
    // named still mounts nothing. MUTATION KILLED: rendering a surface because
    // the plan lists it, rather than because the view model carries it.
    h.state = liveState(false);
    h.caps = liveCaps(false);
    render({ strips: 'dual' }, planFor(dualReceiverCockpitLayout, {
      visibleSurfaces: { 'rx-tx': ['rxTx', 'txAux'], 'primary-vfo': ['vfo', 'txAux'] },
      zoneOrder: { 'rx-tx': ['txAux', 'rxTx'] },
    }));

    expect(q('[data-testid="tx-aux-surface"]')).toBeNull();
    expect(target.innerHTML).not.toContain('tx-aux');
    // …and the surface that IS declared there is still exactly where it was.
    expect(q('.rx-tx-zone [data-testid="rx-tx-surface"]')).not.toBeNull();
  });

  it('reorders the single composition from the zone that mounts both surfaces', () => {
    // sdr-test's `main` zone declares ['vfo', 'rxTx']; the operator flipped it.
    // MUTATION KILLED: ignoring `zoneOrder` in the single composition, or
    // hard-coding the VFO-before-RX/TX sequence.
    render({ strips: 'single' }, planFor(sdrTestLayout, {
      zoneOrder: { main: ['rxTx', 'vfo'] },
    }));

    expect(surfaceOrder()).toEqual(['rx-tx-surface', 'vfo-surface']);
  });

  it('keeps the default sequence with a plan that expresses nothing, and with none at all', () => {
    render({ strips: 'single' }, planFor(sdrTestLayout, {}));
    expect(surfaceOrder()).toEqual(['vfo-surface', 'rx-tx-surface']);

    if (component) unmount(component);
    document.body.innerHTML = '';
    render({ strips: 'single' });
    expect(surfaceOrder()).toEqual(['vfo-surface', 'rx-tx-surface']);
  });
});
