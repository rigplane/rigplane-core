/**
 * MOR-1312 — the semantic scope-display surface wired into
 * `SemanticRadioSurfaces` (vocabulary slice 12B, the LAST vocabulary slice).
 *
 * The unit tests in `semantic/__tests__/ScopeDisplaySurface.test.ts` prove
 * what the surface does with a view model. This file proves the thing only
 * the composed tree can prove: WHERE the `scopeDisplaySnapshot` comes from,
 * and that the reachability gap 12A left (N3 in the 12A verify report — the
 * group was production-unreachable until this slice wired the snapshot) is
 * actually closed.
 *
 *   (a) The snapshot is built from `runtime.defaultScopeStatus` +
 *       `runtime.radioPowerOn` + `runtime.scope.hardwareScopeConnected` —
 *       never guessed, never read from `state`.
 *   (b) `hardwareConnected` (MOR-1352 finding) moves independently of
 *       `defaultScopeStatus` — the composed-tree analogue of the adapter-level
 *       probe in `scope-display-adapter.test.ts`.
 *   (c) Unlike `rxAudio` (control-bearing, single-composition-only), this
 *       surface is PURE READOUT and mounts BARE in BOTH compositions, the
 *       `meters`/`txAux` shape — proved by mounting it in `dual` too.
 *   (d) The default path must stay byte-identical: a radio that declares no
 *       scope capability renders exactly the pre-1312 element shape.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';

type Snapshot = {
  phase: string; intent: string | null; guard: { leaseId: string } | null;
  radioTx: string; txRisk: string; mayOwnKey: boolean; fault: string | null;
};
type ScopeStatus = {
  source: 'hardware' | 'audio_fft' | null;
  available: boolean; resourceSelected: boolean; demand: number;
  lifecycle: 'inactive' | 'starting' | 'streaming' | 'failed';
  transport: 'disconnected' | 'connecting' | 'connected' | 'reconnecting';
  frameSeen: boolean;
};

const OFF_SCOPE_STATUS: ScopeStatus = {
  source: null, available: false, resourceSelected: false, demand: 0,
  lifecycle: 'inactive', transport: 'disconnected', frameSeen: false,
};
const LIVE_SCOPE_STATUS: ScopeStatus = {
  source: 'hardware', available: true, resourceSelected: true, demand: 1,
  lifecycle: 'streaming', transport: 'connected', frameSeen: true,
};

const h = vi.hoisted(() => ({
  state: null as unknown,
  caps: null as unknown,
  snapshot: null as unknown,
  listeners: new Set<(next: unknown) => void>(),
  start: vi.fn(),
  release: vi.fn(),
  noop: vi.fn(),
  scopeStatus: {
    source: null, available: false, resourceSelected: false, demand: 0,
    lifecycle: 'inactive', transport: 'disconnected', frameSeen: false,
  } as ScopeStatus,
  radioPowerOn: null as boolean | null,
  hardwareScopeConnected: false,
}));

vi.mock('$lib/runtime', () => ({
  runtime: {
    get state() { return h.state; },
    get caps() { return h.caps; },
    get audio() { return { muted: true, rxEnabled: false, volume: 0 }; },
    get connectionAudio() { return false; },
    // MOR-1312 slice 12B: the wiring's `scopeDisplaySnapshot` (the FIFTH
    // adapter argument) is built from these three reads.
    get defaultScopeStatus() { return h.scopeStatus; },
    get radioPowerOn() { return h.radioPowerOn; },
    get scope() { return { hardwareScopeConnected: h.hardwareScopeConnected }; },
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
  makeRxAudioHandlers: () => ({ onMonitorModeChange: h.noop, onAfLevelChange: h.noop }),
  makeAudioRoutingHandlers: () => ({ onFocusChange: h.noop, onSplitStereoChange: h.noop }),
  makeModeHandlers: () => ({
    onModInputChange: h.noop, onModeChange: h.noop, onDataModeChange: h.noop,
  }),
  // MOR-1312 slice 12B (rebase fix): `SemanticRadioSurfaces` now calls every
  // one of these factories unconditionally at init — this file's own mock
  // predates 4B-9B and only needs each module-scope call not to throw; none
  // of these are reachable through the scopeDisplay-only fixtures below.
  makeFilterHandlers: () => ({
    onFilterChange: h.noop, onFilterWidthChange: h.noop, onFilterShapeChange: h.noop,
    onIfShiftChange: h.noop, onPbtInnerChange: h.noop, onPbtOuterChange: h.noop,
  }),
  makeDspHandlers: () => ({
    onNrModeChange: h.noop, onNrLevelChange: h.noop, onNbToggle: h.noop,
    onNbLevelChange: h.noop, onNbDepthChange: h.noop, onNbWidthChange: h.noop,
    onNotchModeChange: h.noop, onNotchFreqChange: h.noop,
    onManualNotchWidthChange: h.noop, onAgcTimeChange: h.noop,
  }),
  makeAgcHandlers: () => ({ onAgcModeChange: h.noop }),
  makeRfFrontEndHandlers: () => ({
    onAttChange: h.noop, onPreChange: h.noop, onRfGainChange: h.noop,
    onSquelchChange: h.noop, onDigiSelToggle: h.noop, onIpPlusToggle: h.noop,
  }),
  makeBandHandlers: () => ({ onBandSelect: h.noop }),
  makeAntennaHandlers: () => ({ onSelectAnt1: h.noop, onSelectAnt2: h.noop, onToggleRxAnt: h.noop }),
  makeRitXitHandlers: () => ({
    onRitToggle: h.noop, onXitToggle: h.noop, onRitOffsetChange: h.noop,
    onXitOffsetChange: h.noop, onClear: h.noop,
  }),
  makeScanHandlers: () => ({
    onScanStart: h.noop, onScanStop: h.noop, onDfSpanChange: h.noop, onResumeChange: h.noop,
  }),
  makeCwPanelHandlers: () => ({
    onKeySpeedChange: h.noop, onCwPitchChange: h.noop, onBreakInDelayChange: h.noop,
    onBreakInModeChange: h.noop, onApfChange: h.noop, onTwinPeakToggle: h.noop,
    onReversePaddleToggle: h.noop,
  }),
  // MOR-1311 slice 11B: the scope-toolbar/popover intent vocabulary.
  makeScopeControlsHandlers: () => ({
    onModeChange: h.noop, onEdgeChange: h.noop, onSpanChange: h.noop, onSpeedChange: h.noop,
    onHoldChange: h.noop, onRefChange: h.noop, onDualChange: h.noop, onReceiverChange: h.noop,
    onDuringTxChange: h.noop, onCenterTypeChange: h.noop, onVbwChange: h.noop, onRbwChange: h.noop,
  }),
}));

import SemanticRadioSurfaces from '../SemanticRadioSurfaces.svelte';
// MOR-1365 (S6a): the REAL manifests + the REAL resolution seam, mirroring
// `semantic-tx-aux-wiring.component.test.ts`'s "MOR-1082 — the semantic
// vertical consults the resolved surface plan" shape — the only way to prove
// the `scope-display` zone binding and the S5 subtraction asymmetry, since
// `useSurfacePlan()` falls back to `NO_PLAN` on a standalone mount.
import {
  desktopV2Layout, dualReceiverCockpitLayout,
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

function liveState(): ServerState {
  const paths = ['active', 'split', 'dualWatch', 'txTarget'];
  for (const rx of ['main', 'sub']) {
    paths.push(`${rx}.freqHz`, `${rx}.mode`, `${rx}.filter`, `${rx}.activeSlot`);
    for (const v of ['vfoA', 'vfoB']) {
      paths.push(`${rx}.${v}.freqHz`, `${rx}.${v}.mode`, `${rx}.${v}.filterNum`);
    }
  }
  const receiver = (hz: number) => ({
    ...slot(hz), vfoA: slot(hz), vfoB: slot(hz + 50000), activeSlot: 'A', filter: 1,
  });
  return {
    active: 'MAIN', split: false, dualWatch: false, ptt: false,
    txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 14250000 },
    main: receiver(14250000), sub: receiver(14300000),
    fieldStatus: Object.fromEntries(paths.map((p) => [p, fresh])),
  } as unknown as ServerState;
}

/** `withScope=false` mirrors `semantic-meters-wiring`'s `liveCaps(false)` —
 *  the exact fixture the default-path element-shape literal is pinned
 *  against, so that literal stays valid as a cross-file invariant. */
const liveCaps = (withScope: boolean): Capabilities => ({
  model: 'fixture', scope: withScope, audio: true, tx: true,
  capabilities: ['audio', 'tx', 'dual_rx'],
  receivers: 2, vfoScheme: 'main_sub', freqRanges: [], modes: [], filters: [],
  audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
  webrtc: { available: false, enabled: false },
  txBands: [{ start: 14000000, end: 14350000, name: '20m' }],
  scopeSource: withScope ? 'hardware' : null, audioFftAvailable: false,
} as unknown as Capabilities);

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;

function render(props: { strips?: 'single' | 'dual' } = {}, plan?: SurfacePlan): void {
  target = document.createElement('div');
  document.body.appendChild(target);
  const context = plan === undefined
    ? undefined
    : new Map<unknown, unknown>([[SURFACE_PLAN_CONTEXT_KEY, () => plan]]);
  component = mount(SemanticRadioSurfaces, { target, props, context });
  flushSync();
}

const q = <T extends HTMLElement>(sel: string) => target.querySelector(sel) as T | null;

beforeEach(() => {
  h.state = liveState();
  h.caps = liveCaps(false);
  h.snapshot = { ...IDLE };
  h.listeners.clear();
  h.start.mockReset();
  h.release.mockReset();
  h.noop.mockReset();
  h.scopeStatus = { ...OFF_SCOPE_STATUS };
  h.radioPowerOn = null;
  h.hardwareScopeConnected = false;
});

afterEach(() => {
  if (component) unmount(component);
  component = null;
  document.body.innerHTML = '';
});

// ── 1. The structural gate: absent group ⇒ no surface, no element drift ────

describe('the scope-display surface mounts only when the view model carries the group', () => {
  /** Same literal `semantic-meters-wiring`'s DEFAULT_PATH_TESTIDS pins,
   *  against the SAME `scope:false`/`dual_rx`-only fixture, so a scope-caps
   *  radio's default path is provably unaffected by this slice. */
  const DEFAULT_PATH_TESTIDS = [
    'vfo-surface', 'vfo-active-receiver', 'vfo-list',
    'vfo-ops', 'vfo-split-digest',
    'rx-tx-surface', 'rx-tx-state', 'rx-tx-rf-mark', 'rx-tx-rf-label',
    'rx-tx-target', 'rx-tx-key', 'rx-tx-unkey', 'rx-tx-blocked',
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

  it.each(['single', 'dual'] as const)(
    'renders no scope-display surface at all without the scope capability (%s)',
    (strips) => {
      render({ strips });
      expect(q('[data-testid="scope-display-surface"]')).toBeNull();
    },
  );

  it('leaves the default path element sequence exactly as it is today', () => {
    render();
    expect(testids()).toEqual(DEFAULT_PATH_TESTIDS);
  });

  // MUTATION PROBE — source-selection rendering (2 of 2 required by the
  // ticket, integration half of the unit-level probe in
  // ScopeDisplaySurface.test.ts): a radio WITH the scope capability, wired
  // through the REAL adapter, must actually mount the surface.
  it.each(['single', 'dual'] as const)(
    'mounts the scope-display surface when the scope capability is declared (%s)',
    (strips) => {
      h.caps = liveCaps(true);
      h.scopeStatus = { ...LIVE_SCOPE_STATUS };
      render({ strips });
      expect(target.querySelectorAll('[data-testid="scope-display-surface"]')).toHaveLength(1);
      expect(q('[data-testid="scope-display-source"]')!.textContent).toContain('hardware');
    },
  );

  // Same shape as `meters`/`txAux`: declarable, but no manifest declares a
  // `scopeDisplay` zone in this slice — the surface renders bare in BOTH
  // compositions, unlike `rxAudio`'s single-only mount.
  it('binds no zone id to the scope-display surface in either composition', () => {
    h.caps = liveCaps(true);
    h.scopeStatus = { ...LIVE_SCOPE_STATUS };
    render({ strips: 'dual' });
    const zones = [...target.querySelectorAll<HTMLElement>('[data-zone-id]')]
      .map((el) => el.dataset.zoneId);
    expect(zones).toEqual(['primary-vfo', 'secondary-vfo', 'global', 'rx-tx']);
    expect(q('[data-testid="scope-display-surface"]')!.closest('[data-zone-id]')).toBeNull();
  });

  it('mounts in the dual composition too — pure readout, not the rxAudio single-only shape', () => {
    h.caps = liveCaps(true);
    h.scopeStatus = { ...LIVE_SCOPE_STATUS };
    render({ strips: 'dual' });
    expect(q('[data-testid="scope-display-surface"]')).not.toBeNull();
  });
});

// ── 2. The snapshot is built from the runtime facade, never from `state` ──

describe('the scope-display snapshot comes from runtime.defaultScopeStatus / radioPowerOn / scope', () => {
  it('reflects a live snapshot honestly', () => {
    h.caps = liveCaps(true);
    h.scopeStatus = { ...LIVE_SCOPE_STATUS };
    render();
    expect(q('[data-testid="scope-display-health"]')!.textContent).toContain('connected');
  });

  // MOR-1352 finding, wiring-level proof (carry-forward from the 12A
  // verify): `hardwareConnected` must move independently of
  // `defaultScopeStatus` — it comes from a SEPARATE runtime read
  // (`runtime.scope.hardwareScopeConnected`), never derived from `health`.
  it('renders hardwareConnected independently of health/source (MOR-1352)', () => {
    h.caps = liveCaps(true);
    // Selected source is audio_fft and fully healthy...
    h.scopeStatus = { ...LIVE_SCOPE_STATUS, source: 'audio_fft' };
    // ...while the hardware channel is explicitly reported down.
    h.hardwareScopeConnected = false;
    render();
    expect(q('[data-testid="scope-display-source"]')!.textContent).toContain('audio_fft');
    expect(q('[data-testid="scope-display-health"]')!.textContent).toContain('connected');
    expect(q('[data-testid="scope-display-hardware"]')!.textContent).toContain('off');
  });

  it('renders hardwareConnected "on" when the hardware channel is up, independent of source', () => {
    h.caps = liveCaps(true);
    h.scopeStatus = { ...LIVE_SCOPE_STATUS, source: 'audio_fft' };
    h.hardwareScopeConnected = true;
    render();
    expect(q('[data-testid="scope-display-hardware"]')!.textContent).toContain('on');
  });

  // `isPoweredOff` — the status bar's own override input — must reach the
  // group through `runtime.radioPowerOn`, overriding an otherwise-live
  // snapshot exactly as `classifyScopeHealth` requires.
  it('forces health to disconnected when the radio is powered off, even with a live snapshot', () => {
    h.caps = liveCaps(true);
    h.scopeStatus = { ...LIVE_SCOPE_STATUS };
    h.radioPowerOn = false;
    render();
    expect(q('[data-testid="scope-display-health"]')!.textContent).toContain('disconnected');
  });

  it('never reads scope facts off `state` — only the runtime facade', () => {
    h.caps = liveCaps(true);
    h.scopeStatus = { ...LIVE_SCOPE_STATUS };
    // A raw `state.scopeControls`-shaped value has no bearing on this group.
    h.state = { ...liveState(), scopeControls: { mode: 99 } } as unknown as ServerState;
    render();
    expect(q('[data-testid="scope-display-surface"]')).not.toBeNull();
    expect(q('[data-testid="scope-display-source"]')!.textContent).toContain('hardware');
  });
});

// ── 3. R9: the scope-display surface is a readout, never an action path ───

describe('the scope-display surface adds no control and no TX path', () => {
  it('keeps exactly one key/unkey authority in the composed tree', () => {
    h.caps = liveCaps(true);
    h.scopeStatus = { ...LIVE_SCOPE_STATUS };
    render();
    expect(target.querySelectorAll('[data-testid="rx-tx-surface"]')).toHaveLength(1);
    expect(target.querySelectorAll('[data-testid="rx-tx-key"]')).toHaveLength(1);
    expect(target.querySelectorAll('[data-testid="rx-tx-unkey"]')).toHaveLength(1);
  });

  // MUTATION PROBE (required by the ticket, integration half of the
  // unit-level probe): the composed tree's ONLY new focusable elements after
  // this slice must be zero, in EITHER composition.
  it.each(['single', 'dual'] as const)(
    'contributes no focusable control to the composition (%s)',
    (strips) => {
      h.caps = liveCaps(true);
      h.scopeStatus = { ...LIVE_SCOPE_STATUS };
      render({ strips });
      const surface = q('[data-testid="scope-display-surface"]')!;
      expect(surface.querySelectorAll('button, input, select, a[href], [tabindex]'))
        .toHaveLength(0);
      expect(h.start).not.toHaveBeenCalled();
      expect(h.release).not.toHaveBeenCalled();
    },
  );
});

// ── 4. MOR-1365 (S6a) — desktop-v2 REALLY declares the zone; the cockpit ──
// ── does not, and the plan can only ever cost the wrapper, never the fact ─

/**
 * `desktopV2Layout` now carries a `scope-display` zone
 * (`presentation/layouts/desktop-declarations.ts`). This describes the two
 * things that follow from it, using the REAL manifest + the REAL
 * `resolveSurfacePlan` seam, exactly as `semantic-tx-aux-wiring.component
 * .test.ts`'s "MOR-1082 — the semantic vertical consults the resolved
 * surface plan" section does for `txAux`:
 *
 *   (a) the zone binds — `zoneOwning('scopeDisplay')` now answers
 *       `'scope-display'` against desktop-v2's real plan, so the composed
 *       tree wraps the surface in `<div data-zone-id="scope-display">`;
 *   (b) the S5/S6-pre asymmetry: a workspace that SUBTRACTS `scopeDisplay`
 *       from that zone costs the operator the wrapper `<div>`, never the
 *       readout — `zoned()` degrades to bare (S5-N3), so "the workspace
 *       hid it" and "no zone declares it" are indistinguishable and both
 *       render exactly the pre-1365 element shape. MUTATION PROBE: remove
 *       the `zoned(...)` mount from `scopeDisplaySurface`'s call site and
 *       BOTH tests below go red — (a) loses the wrapper, (b) loses the
 *       surface entirely, proving the wrapper mount is what keeps the
 *       readout alive when subtracted, not a redundant safety net.
 *   (c) the dual-receiver cockpit manifest is untouched by this slice, so
 *       the surface keeps mounting bare there — MOR-1069 unmoved.
 */
describe('desktop-v2 declares a REAL scope-display zone; the cockpit does not (MOR-1365)', () => {
  /** What App resolves for `layout` given a stored workspace `fields`. */
  function planFor(layout: typeof desktopV2Layout, fields: Record<string, unknown>): SurfacePlan {
    return resolveSurfacePlan(layout, readWorkspace({ version: 1, ...fields }).workspace);
  }

  it('binds the scope-display zone id against desktop-v2\'s real plan', () => {
    h.caps = liveCaps(true);
    h.scopeStatus = { ...LIVE_SCOPE_STATUS };
    render({ strips: 'single' }, planFor(desktopV2Layout, {}));
    expect(q('[data-testid="scope-display-surface"]')!.closest('[data-zone-id="scope-display"]'))
      .not.toBeNull();
  });

  // THE ASYMMETRY (S5 shape): a workspace subtraction costs the wrapper, not
  // the fact. MUTATION PROBE: reading the PLAN instead of the MANIFEST for
  // suppression anywhere in this channel would make this subtraction able to
  // resurrect a legacy twin — this test only proves the surface side (the
  // legacy-twin side is `semantic-desktop-migration.component.test.ts`'s
  // job), but it is the half that shows the readout itself never disappears.
  it('degrades to a bare readout — never disappears — when the workspace subtracts scopeDisplay from its zone', () => {
    h.caps = liveCaps(true);
    h.scopeStatus = { ...LIVE_SCOPE_STATUS };
    render({ strips: 'single' }, planFor(desktopV2Layout, {
      visibleSurfaces: { 'scope-display': [] },
    }));
    expect(q('[data-testid="scope-display-surface"]')).not.toBeNull();
    expect(q('[data-testid="scope-display-source"]')!.textContent).toContain('hardware');
    expect(q('[data-testid="scope-display-surface"]')!.closest('[data-zone-id]')).toBeNull();
  });

  it('keeps mounting bare in the dual-receiver cockpit — its manifest is untouched by this slice', () => {
    h.caps = liveCaps(true);
    h.scopeStatus = { ...LIVE_SCOPE_STATUS };
    render({ strips: 'dual' }, planFor(dualReceiverCockpitLayout, {}));
    expect(q('[data-testid="scope-display-surface"]')).not.toBeNull();
    expect(q('[data-testid="scope-display-surface"]')!.closest('[data-zone-id]')).toBeNull();
  });
});
