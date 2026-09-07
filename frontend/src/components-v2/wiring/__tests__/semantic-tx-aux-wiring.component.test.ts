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
// @ts-expect-error -- Svelte does not publish types for its reactive test harness.
import { proxy } from 'svelte/internal/client';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import type { ManagedAppTxController } from '$lib/runtime/tx-controller/managed-app-host';
import type { ControlSessionSnapshot } from '$lib/runtime/frontend-runtime';
import type { RxAudioTargetSnapshot } from '$lib/stores/audio.svelte';

const h = vi.hoisted(() => ({
  state: null as unknown,
  caps: null as unknown,
  txController: null as ManagedAppTxController | null,
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
  modeChange: vi.fn(),
  filterChange: vi.fn(),
  filterShapeChange: vi.fn(),
  dataModeChange: vi.fn(),
  split: vi.fn(),
  dualWatch: vi.fn(),
  mainReceiver: vi.fn(),
  subReceiver: vi.fn(),
  equalize: vi.fn(),
  swap: vi.fn(),
  quickSplit: vi.fn(),
  quickDualWatch: vi.fn(),
  speak: vi.fn(),
  noop: vi.fn(),
  session: { state: 'connected', epoch: 7 } as ControlSessionSnapshot,
  sessionSubscriber: null as ((next: ControlSessionSnapshot) => void) | null,
  authoritySubscribers: new Set<(next: {
    state: unknown; caps: unknown; session: ControlSessionSnapshot;
    rxAudioTarget: RxAudioTargetSnapshot;
  }) => void>(),
  audio: { muted: true, rxEnabled: false, volume: 0 },
  radioListeners: new Set<(state: ServerState | null) => void>(),
  selectedFiniteAppearance: undefined as unknown,
}));

vi.mock('../../../component-kits/activation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../component-kits/activation')>();
  return { ...actual, getSelectedFiniteControlAppearance: () => h.selectedFiniteAppearance };
});

/** MOR-2425 F1-C2 — the `createContinuousScalar` capture wrapper
 *  `semantic-dsp-wiring.component.test.ts`'s `dspBindings()` recipe
 *  establishes, transplanted for `createChoiceRendererSeat`: every finite
 *  seat this file mounts (Mode/Filter/Filter shape/DATA mode included, plus
 *  every other family's choice seats) is recorded by its OWN label so the
 *  Filter shape/DATA persistence witness below can name the host-owned seat
 *  OBJECT — a DOM testid is re-created by the layout switch on both sides
 *  and proves nothing about whether the underlying seat survived it. */
const finiteSeats = vi.hoisted(() => ({ seats: [] as Array<{ label: string; seat: unknown }> }));
vi.mock('../../../primitives/control-instruments/control-instrument-renderer.svelte', async (importOriginal) => {
  const actual = await importOriginal<
    typeof import('../../../primitives/control-instruments/control-instrument-renderer.svelte')>();
  return {
    ...actual,
    createChoiceRendererSeat: (...args: Parameters<typeof actual.createChoiceRendererSeat>) => {
      const seat = actual.createChoiceRendererSeat(...args);
      finiteSeats.seats.push({ label: args[0]().label, seat });
      return seat;
    },
  };
});

vi.mock('$lib/runtime', () => ({
  runtime: {
    onTxAudioDied: () => () => {},
    get state() { return h.state; },
    get caps() { return h.caps; },
    get controlSession() { return h.session; },
    subscribeControlSession(handler: (next: ControlSessionSnapshot) => void) {
      h.sessionSubscriber = handler;
      return () => { if (h.sessionSubscriber === handler) h.sessionSubscriber = null; };
    },
    subscribeControlAuthority(handler: (typeof h.authoritySubscribers extends Set<infer T> ? T : never)) {
      h.authoritySubscribers.add(handler);
      handler({
        state: h.state, caps: h.caps, session: h.session,
        rxAudioTarget: Object.freeze({ muted: h.audio.muted, rxEnabled: h.audio.rxEnabled }),
      });
      return () => { h.authoritySubscribers.delete(handler); };
    },
    // MOR-1279 slice 3B: the wiring now also hands the adapter an
    // App-owned RX-audio snapshot (the FOURTH argument). Muted with no
    // browser stream keeps every fixture below on its pre-1279 path.
    get audio() { return h.audio; },
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
vi.mock('$lib/runtime/frontend-runtime', () => ({
  runtime: {
    get state() { return h.state; },
    get caps() { return h.caps; },
    get controlSession() { return h.session; },
    subscribeControlAuthority(handler: (typeof h.authoritySubscribers extends Set<infer T> ? T : never)) {
      h.authoritySubscribers.add(handler);
      handler({
        state: h.state, caps: h.caps, session: h.session,
        rxAudioTarget: Object.freeze({ muted: h.audio.muted, rxEnabled: h.audio.rxEnabled }),
      });
      return () => { h.authoritySubscribers.delete(handler); };
    },
  },
}));
vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({
  getManagedAppTxController: () => {
    if (!h.txController) throw new Error('managed TX harness is not installed');
    return h.txController;
  },
}));
vi.mock('$lib/stores/radio.svelte', () => ({
  radio: { get current() { return h.state; } },
  getRadioState: () => h.state,
  subscribeRadioState(listener: (state: ServerState | null) => void) {
    h.radioListeners.add(listener);
    listener(h.state as ServerState | null);
    return () => h.radioListeners.delete(listener);
  },
}));
vi.mock('$lib/runtime/adapters/mod-input-tx-guard.svelte', () => ({
  deriveModInputTxGuardProps: () => ({ visible: false, sourceLabel: null }),
  getModInputTxGuardHandlers: () => ({ onSetLan: vi.fn(), onDismiss: vi.fn() }),
}));
// The names below are the REAL `makeTxHandlers`/`makeVoxHandlers` surface —
// agreement with the shipped module is proven separately, against the real
// module, in `tx-aux-command-bus.isolated.test.ts`.
vi.mock('$lib/runtime/commands/panel-commands', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/runtime/commands/panel-commands')>();
  return {
    ...actual,
    makeVfoHandlers: () => ({
      onVfoSelect: h.noop,
      onSplitToggle: h.split,
      onDualWatchToggle: h.dualWatch,
      onMainVfoClick: h.mainReceiver,
      onSubVfoClick: h.subReceiver,
      onEqual: h.equalize,
      onSwap: h.swap,
      onQuickSplit: h.quickSplit,
      onQuickDw: h.quickDualWatch,
    }),
    makeSystemHandlers: () => ({ onSpeak: h.speak }),
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
      onModInputChange: h.noop, onModeChange: h.modeChange, onDataModeChange: h.dataModeChange,
    }),
    makeFilterHandlers: () => ({
      onFilterChange: h.filterChange, onFilterWidthChange: h.noop, onFilterShapeChange: h.filterShapeChange,
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
    // MOR-1306 slice 6B: the RF-front-end intent vocabulary — routing is pinned
    // separately in `semantic-rf-front-end-wiring.component.test.ts`; this file
    // only needs the wiring's module-scope `makeRfFrontEndHandlers()` call not
    // to throw.
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
    // MOR-1311 slice 11B: the scope-toolbar/popover intent vocabulary.
    makeScopeControlsHandlers: () => ({
      onModeChange: h.noop, onEdgeChange: h.noop, onSpanChange: h.noop, onSpeedChange: h.noop,
      onHoldChange: h.noop, onRefChange: h.noop, onDualChange: h.noop, onReceiverChange: h.noop,
      onDuringTxChange: h.noop, onCenterTypeChange: h.noop, onVbwChange: h.noop, onRbwChange: h.noop,
    }),
  };
});

import SemanticRadioSurfaces from '../SemanticRadioSurfaces.svelte';
import HostedRadioLayoutFixture from '../../layout/__tests__/fixtures/HostedRadioLayoutFixture.svelte';
// MOR-1082: the REAL manifests, through the app-wide registration barrel, and
// the REAL resolution seam — the plans below are what App would hand down.
import {
  desktopV2Layout, dualReceiverCockpitLayout, mobileLayout, sdrTestLayout,
} from '../../../presentation/layouts/declarations';
import { readWorkspace } from '../../../presentation/workspace/contract';
import {
  resolveSurfacePlan, SURFACE_PLAN_CONTEXT_KEY, type SurfacePlan,
} from '../../../presentation/workspace/resolution';
import {
  ManagedAppTxHarness, type ManagedAppTxServerSnapshot,
} from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';
import {
  TX_AUX_COMMAND_DESCRIPTORS, acknowledgeCommand, beginCommand, getCommandLifecycles,
  resetCommandLifecycle,
} from '$lib/stores/commands.svelte';
import FiniteControlRendererFixture, {
  resetRetainedInvocations, retainedInvocations,
} from '../../../primitives/control-instruments/__tests__/support/FiniteControlRendererFixture.svelte';
import type { FiniteControlAppearance } from '../../../primitives/control-instruments/control-instrument-renderer.svelte';

const fresh = { storePath: 'x', observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 0 };
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
    stateContractVersion: 1, providerGeneration: 1,
    active: 'MAIN', split: false, dualWatch: false, ptt: false,
    txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 14250000 },
    main: receiver(14250000), sub: receiver(14300000),
    ...(withTxAux ? TX_AUX_STATE : {}),
    fieldStatus: Object.fromEntries(paths.map((p) => [p, fresh])),
  } as unknown as ServerState;
}

const liveCaps = (withTxAux: boolean): Capabilities => ({
  stateContractVersion: 1, providerGeneration: 1,
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

const vfoCaps = (): Capabilities => ({
  ...liveCaps(true),
  capabilities: [
    ...liveCaps(true).capabilities,
    'dual_watch', 'split', 'vfo_equalize', 'vfo_swap', 'speech',
  ],
});

function filterState(): ServerState {
  const state = liveState(true);
  const receiver = (value: typeof state.main) => ({
    ...value, filterWidth: 2400, filterShape: 1, ifShift: 0,
    pbtInner: 128, pbtOuter: 128, dataMode: 0,
  });
  const fields = ['filterWidth', 'filterShape', 'ifShift', 'pbtInner', 'pbtOuter', 'dataMode'];
  return {
    ...state, main: receiver(state.main), sub: receiver(state.sub!),
    fieldStatus: {
      ...state.fieldStatus,
      ...Object.fromEntries(['main', 'sub'].flatMap(rx => fields.map(field => [`${rx}.${field}`, fresh]))),
    },
  } as ServerState;
}

const filterCaps = (): Capabilities => ({
  ...liveCaps(true),
  capabilities: [...liveCaps(true).capabilities, 'filter_shape', 'if_shift', 'pbt', 'data_mode'],
  modes: ['USB', 'CW', 'FM'], filters: ['FIL1', 'FIL2', 'FIL3'],
  dataModeCount: 2,
  controls: {
    pbt_inner: { raw_min: 0, raw_max: 255, raw_center: 128, display_min: -1200, display_max: 1200 },
  },
} as unknown as Capabilities);

const finiteFixture = FiniteControlRendererFixture as FiniteControlAppearance['action'];
const finiteAppearance = {
  action: finiteFixture,
  toggle: FiniteControlRendererFixture as FiniteControlAppearance['toggle'],
  choice: FiniteControlRendererFixture as FiniteControlAppearance['choice'],
} satisfies FiniteControlAppearance;

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
) {
  target = document.createElement('div');
  document.body.appendChild(target);
  const context = plan === undefined
    ? undefined
    : new Map<unknown, unknown>([[SURFACE_PLAN_CONTEXT_KEY, () => plan]]);
  const reactiveProps = proxy(props);
  component = mount(SemanticRadioSurfaces, { target, props: reactiveProps, context });
  flushSync();
  return reactiveProps;
}

function renderHostedDesktop(): void {
  renderHostedLayout('desktop-v2');
}

function renderHostedLayout(skinId: 'desktop-v2' | 'sdr-test') {
  target = document.createElement('div');
  document.body.appendChild(target);
  const props = proxy({ skinId });
  component = mount(HostedRadioLayoutFixture, {
    target, props,
    context: new Map<unknown, unknown>([[SURFACE_PLAN_CONTEXT_KEY, () => resolveSurfacePlan(
      props.skinId === 'desktop-v2' ? desktopV2Layout : sdrTestLayout,
      readWorkspace({ version: 1 }).workspace,
    )]]),
  });
  flushSync();
  return props;
}

function push(next: ManagedAppTxServerSnapshot): void {
  txHarness.emitServerSnapshot(next);
  flushSync();
}

function publishAuthority(): void {
  for (const subscriber of h.authoritySubscribers) subscriber({
    state: h.state, caps: h.caps, session: h.session,
    rxAudioTarget: Object.freeze({ muted: h.audio.muted, rxEnabled: h.audio.rxEnabled }),
  });
}

function pushSession(next: ControlSessionSnapshot): void {
  h.session = next;
  h.sessionSubscriber?.(next);
  publishAuthority();
  flushSync();
}

function pushRadioState(next: ServerState | null): void {
  h.state = next;
  for (const listener of h.radioListeners) listener(next);
  publishAuthority();
  flushSync();
}

const q = <T extends HTMLElement>(sel: string) => target.querySelector(sel) as T | null;
let txHarness: ManagedAppTxHarness;

beforeEach(() => {
  resetCommandLifecycle();
  txHarness = new ManagedAppTxHarness();
  h.txController = txHarness.controller;
  h.state = liveState(true);
  h.caps = liveCaps(true);
  h.session = { state: 'connected', epoch: 7 };
  h.sessionSubscriber = null;
  h.selectedFiniteAppearance = undefined;
  resetRetainedInvocations();
  finiteSeats.seats.length = 0;
  for (const value of Object.values(h)) {
    if (typeof value === 'function' && 'mockReset' in value) (value as ReturnType<typeof vi.fn>).mockReset();
  }
});

afterEach(() => {
  if (component) unmount(component);
  component = null;
  resetCommandLifecycle();
  resetRetainedInvocations();
  expect(h.sessionSubscriber).toBeNull();
  expect(h.authoritySubscribers.size).toBe(0);
  document.body.innerHTML = '';
});

describe('L1 hosted desktop TX auxiliary composition', () => {
  it('places all five finite and eight scalar handles independently', () => {
    txHarness.emitServerSnapshot({ intent: 'transmit', observedPtt: 'on' });
    renderHostedDesktop();

    const fields = [
      'rfPower', 'micGain', 'driveGain', 'voxGain', 'antiVoxGain', 'voxDelay',
      'compressorLevel', 'monitorLevel',
    ] as const;
    const zone = q('[data-zone-id="tx-aux"]');
    const remainder = q('[data-testid="tx-aux-surface"]');
    const finiteGrid = q('.tx-aux-finite-grid');
    const grid = q('.tx-aux-scalar-grid');
    const reasons = q('[data-testid="tx-aux-tune-blocked"]');

    expect(target.querySelectorAll('[data-zone-id="tx-aux"]')).toHaveLength(1);
    expect(target.querySelectorAll('[data-testid="tx-aux-surface"]')).toHaveLength(1);
    expect(zone).not.toBeNull();
    expect(remainder?.closest('[data-zone-id="tx-aux"]')).toBe(zone);
    expect(finiteGrid?.closest('[data-zone-id="tx-aux"]')).toBe(zone);
    expect(grid?.closest('[data-zone-id="tx-aux"]')).toBe(zone);
    expect(finiteGrid?.querySelectorAll(':scope > .tx-aux-finite-seat')).toHaveLength(5);
    expect(grid?.querySelectorAll(':scope > .tx-aux-scalar-seat')).toHaveLength(8);
    expect(target.querySelectorAll('[data-testid="tx-aux-tune-blocked"]')).toHaveLength(1);
    expect(reasons?.querySelectorAll('[data-reason]')).toHaveLength(2);
    expect(grid!.compareDocumentPosition(reasons!) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    for (const [field, testid] of [
      ['atu', 'tx-aux-atu'], ['vox', 'tx-aux-vox'], ['compressor', 'tx-aux-compressor'],
      ['monitor', 'tx-aux-monitor'], ['atuTune', 'tx-aux-atu-tune'],
    ] as const) {
      const seat = finiteGrid?.querySelector(`:scope > .tx-aux-finite-seat[data-field="${field}"]`);
      expect(seat, `${field} seat`).not.toBeNull();
      expect(seat?.querySelector(`[data-testid="${testid}"]`)).not.toBeNull();
      expect(target.querySelectorAll(`[data-testid="${testid}"]`)).toHaveLength(1);
    }

    for (const field of fields) {
      const seat = grid?.querySelector(`:scope > .tx-aux-scalar-seat[data-field="${field}"]`);
      expect(seat, `${field} seat`).not.toBeNull();
      expect(seat?.querySelector(`[data-testid="tx-aux-${field}"]`)).not.toBeNull();
      expect(target.querySelectorAll(`[data-testid="tx-aux-${field}"]`)).toHaveLength(1);
    }

    expect(remainder?.querySelector('.tx-aux-scalar-grid')).toBeNull();
    expect(target.querySelectorAll('[data-testid="tx-aux-atu-tune"]')).toHaveLength(1);
    expect(target.querySelectorAll('.tx-aux-toggle')).toHaveLength(4);
  });
});

describe('hosted Standard VFO operation instruments', () => {
  it('keeps native absolute receiver options actionable when current identity is unknown', () => {
    h.caps = vfoCaps();
    const unknownActive = liveState(true);
    delete unknownActive.fieldStatus?.active;
    h.state = unknownActive;
    renderHostedDesktop();

    const main = q<HTMLButtonElement>('[data-active-receiver-segment="MAIN"]')!;
    const sub = q<HTMLButtonElement>('[data-active-receiver-segment="SUB"]')!;
    expect(main.ariaChecked).toBe('false');
    expect(sub.ariaChecked).toBe('false');
    expect(sub.disabled).toBe(false);
    sub.click();
    expect(h.subReceiver).toHaveBeenCalledOnce();
  });

  it('places every admitted named seat once, invokes current callbacks, and keeps one digest', () => {
    h.caps = vfoCaps();
    h.selectedFiniteAppearance = finiteAppearance;
    renderHostedDesktop();

    const grid = q('[data-testid="vfo-operation-instrument-grid"]')!;
    expect([...grid.children].map((seat) => seat.getAttribute('data-field'))).toEqual([
      'split', 'dualWatch', 'activeReceiver', 'equalize', 'swap', 'speak',
    ]);
    expect(q('[data-vfo-split]')).toBeNull();
    expect(target.querySelectorAll('[data-testid="vfo-split-digest"]')).toHaveLength(1);
    expect(q('[data-testid="external-Quick split"]')).toBeNull();
    expect(q('[data-testid="external-Quick dual watch"]')).toBeNull();

    q<HTMLButtonElement>('[data-testid="external-Split"]')!.click();
    q<HTMLButtonElement>('[data-testid="external-Dual watch"]')!.click();
    q<HTMLButtonElement>('[data-testid="external-Receiver-SUB"]')!.click();
    q<HTMLButtonElement>('[data-testid="external-M=S"]')!.click();
    q<HTMLButtonElement>('[data-testid="external-M↔S"]')!.click();
    q<HTMLButtonElement>('[data-testid="external-SPEAK"]')!.click();
    expect(h.split).toHaveBeenCalledOnce();
    expect(h.dualWatch).toHaveBeenCalledExactlyOnceWith(true);
    expect(h.subReceiver).toHaveBeenCalledOnce();
    expect(h.equalize).toHaveBeenCalledOnce();
    expect(h.swap).toHaveBeenCalledOnce();
    expect(h.speak).toHaveBeenCalledOnce();
    expect(h.quickSplit).not.toHaveBeenCalled();
    expect(h.quickDualWatch).not.toHaveBeenCalled();
  });

  it('keeps unknown receiver unselected while admitted external MAIN and SUB establish identity', () => {
    h.caps = vfoCaps();
    const unknownActive = liveState(true);
    delete unknownActive.fieldStatus?.active;
    h.state = unknownActive;
    h.selectedFiniteAppearance = finiteAppearance;
    renderHostedDesktop();

    const main = q<HTMLButtonElement>('[data-testid="external-Receiver-MAIN"]')!;
    const sub = q<HTMLButtonElement>('[data-testid="external-Receiver-SUB"]')!;
    expect(main.ariaChecked).toBe('false');
    expect(sub.ariaChecked).toBe('false');
    expect(main.disabled).toBe(false);
    expect(sub.disabled).toBe(false);
    main.click();
    sub.click();
    expect(h.mainReceiver).toHaveBeenCalledOnce();
    expect(h.subReceiver).toHaveBeenCalledOnce();
  });

  it('retains asymmetric receiver reasons without disabling the offered MAIN option', () => {
    h.caps = vfoCaps();
    const degraded = liveState(true);
    delete (degraded as Partial<ServerState>).sub;
    h.state = degraded;
    h.selectedFiniteAppearance = finiteAppearance;
    renderHostedDesktop();

    const main = q<HTMLButtonElement>('[data-testid="external-Receiver-MAIN"]')!;
    const sub = q<HTMLButtonElement>('[data-testid="external-Receiver-SUB"]')!;
    expect(main.disabled).toBe(false);
    expect(main.title).toBe('');
    expect(sub.disabled).toBe(true);
    expect(sub.title.length).toBeGreaterThan(0);
    expect(document.getElementById(sub.getAttribute('aria-describedby')!)?.textContent).toBe(sub.title);
    main.click();
    expect(h.mainReceiver).toHaveBeenCalledOnce();
  });

  it('does not rotate radio-wide authority when only selected receiver truth becomes unknown', () => {
    h.caps = vfoCaps();
    h.selectedFiniteAppearance = finiteAppearance;
    renderHostedDesktop();
    const receiver = q('[data-testid="external-Receiver"]');
    const retained = retainedInvocations.get('Receiver')!;

    const unknownActive = liveState(true);
    delete unknownActive.fieldStatus?.active;
    pushRadioState(unknownActive);
    push({});

    expect(q('[data-testid="external-Receiver"]')).toBe(receiver);
    expect(retainedInvocations.get('Receiver')).toBe(retained);
    expect(q<HTMLButtonElement>('[data-testid="external-Receiver-MAIN"]')!.ariaChecked).toBe('false');
    expect(q<HTMLButtonElement>('[data-testid="external-Receiver-SUB"]')!.ariaChecked).toBe('false');
    retained('SUB');
    expect(h.subReceiver).toHaveBeenCalledOnce();
  });

  it('re-reads same-context admission and keeps unknown relative toggles inert', () => {
    h.caps = vfoCaps();
    h.selectedFiniteAppearance = finiteAppearance;
    renderHostedDesktop();
    const retained = retainedInvocations.get('Split')!;

    const unknownSplit = liveState(true);
    delete unknownSplit.fieldStatus?.split;
    pushRadioState(unknownSplit);
    push({});
    expect(q<HTMLButtonElement>('[data-testid="external-Split"]')!.disabled).toBe(true);
    h.split.mockClear();
    retained();
    expect(h.split).not.toHaveBeenCalled();

    pushRadioState(liveState(true));
    push({});
    retained();
    expect(h.split).toHaveBeenCalledOnce();
  });

  it('fails selected appearance closed at null authority without losing the digest', () => {
    h.caps = vfoCaps();
    h.selectedFiniteAppearance = finiteAppearance;
    h.session = { state: 'disconnected', epoch: 7 };
    renderHostedDesktop();

    expect(q('[data-vfo-split]')).toBeNull();
    expect(q('[data-testid="external-Split"]')).toBeNull();
    expect(q('[data-testid="external-Receiver"]')).toBeNull();
    expect(target.querySelectorAll('[data-testid="vfo-split-digest"]')).toHaveLength(1);
  });

  it('revokes every retained VFO renderer when the persistent host is destroyed', () => {
    h.caps = vfoCaps();
    h.selectedFiniteAppearance = finiteAppearance;
    renderHostedDesktop();
    const retained = ['Split', 'Dual watch', 'Receiver', 'M=S', 'M↔S', 'SPEAK']
      .map((label) => retainedInvocations.get(label)!);
    unmount(component!);
    component = null;

    retained[0]!();
    retained[1]!();
    retained[2]!('SUB');
    for (const invoke of retained.slice(3)) invoke();
    for (const spy of [
      h.split, h.dualWatch, h.subReceiver, h.equalize, h.swap, h.speak,
    ]) expect(spy).not.toHaveBeenCalled();
  });

  it('keeps the accepted neighboring host subtree mounted through a temporary null view', () => {
    h.caps = vfoCaps();
    h.selectedFiniteAppearance = finiteAppearance;
    renderHostedDesktop();
    const layout = q('.radio-layout');
    const retainedVox = retainedInvocations.get('VOX')!;
    const subscribers = [...h.authoritySubscribers];

    h.caps = null;
    pushRadioState(null);
    push({});
    expect(q('[data-testid="vfo-surface"]')).toBeNull();
    expect(q('[data-testid="external-VOX"]')).toBeNull();
    expect(q('.radio-layout')).toBe(layout);
    expect([...h.authoritySubscribers]).toEqual(subscribers);
    retainedVox();
    expect(h.voxToggle).not.toHaveBeenCalled();

    h.caps = vfoCaps();
    pushRadioState(liveState(true));
    push({});
    expect(q('.radio-layout')).toBe(layout);
    expect(q('[data-testid="external-VOX"]')).not.toBeNull();
    expect([...h.authoritySubscribers]).toEqual(subscribers);
    expect(retainedInvocations.get('VOX')).not.toBe(retainedVox);
    retainedInvocations.get('VOX')!();
    expect(h.voxToggle).toHaveBeenCalledOnce();
  });
});

describe('selected finite TX auxiliary authority lifetime', () => {
  it('keeps one external Standard reason list after every scalar', () => {
    h.selectedFiniteAppearance = finiteAppearance;
    txHarness.emitServerSnapshot({ intent: 'transmit', observedPtt: 'on' });
    renderHostedDesktop();
    const reasons = q('[data-testid="tx-aux-tune-blocked"]')!;
    expect(q('[data-testid="external-TUNE"]')).not.toBeNull();
    expect(target.querySelectorAll('[data-testid="tx-aux-tune-blocked"]')).toHaveLength(1);
    expect(q('.tx-aux-scalar-grid')!.compareDocumentPosition(reasons)
      & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('keeps the selected external appearance inert when authority is absent', () => {
    h.selectedFiniteAppearance = finiteAppearance;
    h.session = { state: 'disconnected', epoch: 7 };
    render();

    expect(target.querySelectorAll('.tx-aux-toggle')).toHaveLength(0);
    expect(q('[data-testid="tx-aux-atu-tune"]')).toBeNull();
    expect(q('[data-testid="external-VOX"]')).toBeNull();
    expect(q('[data-testid="external-TUNE"]')).toBeNull();
    expect(h.authoritySubscribers.size).toBe(6);
  });

  it('revokes retained A1 synchronously on A-B-A before flush and admits only fresh A3', () => {
    h.caps = vfoCaps();
    h.selectedFiniteAppearance = finiteAppearance;
    renderHostedDesktop();
    const retainedA1 = retainedInvocations.get('VOX')!;
    const retainedVfoA1 = retainedInvocations.get('Split')!;

    h.session = { state: 'connected', epoch: 8 };
    publishAuthority();
    h.session = { state: 'connected', epoch: 7 };
    publishAuthority();
    retainedA1();
    retainedVfoA1();
    expect(h.voxToggle).not.toHaveBeenCalled();
    expect(h.split).not.toHaveBeenCalled();

    flushSync();
    const retainedA3 = retainedInvocations.get('VOX')!;
    const retainedVfoA3 = retainedInvocations.get('Split')!;
    expect(retainedA3).not.toBe(retainedA1);
    expect(retainedVfoA3).not.toBe(retainedVfoA1);
    retainedA3();
    retainedVfoA3();
    expect(h.voxToggle).toHaveBeenCalledOnce();
    expect(h.split).toHaveBeenCalledOnce();
    retainedA1();
    retainedVfoA1();
    expect(h.voxToggle).toHaveBeenCalledOnce();
    expect(h.split).toHaveBeenCalledOnce();
  });
});

describe('hosted Filter Mode/Filter/Shape/DATA ownership', () => {
  const residual = ['filter-width', 'filter-ifShift', 'filter-pbtInner', 'filter-pbtOuter'] as const;
  const externalLabels = ['Mode', 'Filter', 'Filter shape', 'DATA mode'] as const;

  it('places named Standard and grouped SDR seats once while residual owners stay single', () => {
    h.state = filterState(); h.caps = filterCaps(); h.selectedFiniteAppearance = finiteAppearance;
    const layout = renderHostedLayout('desktop-v2');
    const subscribers = [...h.authoritySubscribers];
    const grid = q('[data-testid="filter-finite-grid"]')!;
    expect([...grid.children].map(node => node.getAttribute('data-field')))
      .toEqual(['mode', 'filter', 'shape', 'dataMode']);
    for (const label of externalLabels) {
      expect(target.querySelectorAll(`[data-testid="external-${label}"]`)).toHaveLength(1);
    }
    expect(q('[data-testid="external-Mode"]')?.getAttribute('data-reading')).toBe('USB');
    for (const id of residual) expect(target.querySelectorAll(`[data-testid="${id}"]`)).toHaveLength(1);
    retainedInvocations.get('Mode')?.('CW'); retainedInvocations.get('Filter')?.(2);
    retainedInvocations.get('Filter shape')?.(1); retainedInvocations.get('DATA mode')?.(1);
    expect(h.modeChange).toHaveBeenCalledExactlyOnceWith('CW');
    expect(h.filterChange).toHaveBeenCalledExactlyOnceWith(2);
    expect(h.filterShapeChange).toHaveBeenCalledExactlyOnceWith(1);
    expect(h.dataModeChange).toHaveBeenCalledExactlyOnceWith(1);
    const standardMode = retainedInvocations.get('Mode')!;

    publishAuthority(); flushSync();
    expect(retainedInvocations.get('Mode')).toBe(standardMode);
    expect(q('[data-testid="external-Mode"]')?.getAttribute('data-reading')).toBe('USB');
    layout.skinId = 'sdr-test'; flushSync();

    expect(q('[data-testid="filter-finite-grid"]')).toBeNull();
    for (const label of externalLabels) {
      expect(target.querySelectorAll(`[data-testid="external-${label}"]`)).toHaveLength(1);
    }
    for (const id of residual) expect(target.querySelectorAll(`[data-testid="${id}"]`)).toHaveLength(1);
    expect([...h.authoritySubscribers]).toEqual(subscribers);
    const sdrMode = retainedInvocations.get('Mode')!;
    expect(sdrMode).not.toBe(standardMode);
    standardMode('FM'); expect(h.modeChange).toHaveBeenCalledTimes(1);
    sdrMode('FM'); expect(h.modeChange).toHaveBeenLastCalledWith('FM');
  });

  it('keeps selected appearance inert at null Filter authority without hiding residual controls', () => {
    h.state = filterState(); h.caps = filterCaps(); h.selectedFiniteAppearance = finiteAppearance;
    h.session = { state: 'disconnected', epoch: 7 };
    renderHostedDesktop();

    for (const label of externalLabels) expect(q(`[data-testid="external-${label}"]`)).toBeNull();
    expect(q('[data-testid="filter-mode"]')).toBeNull();
    expect(q('[data-testid="filter-select"]')).toBeNull();
    for (const id of residual) expect(target.querySelectorAll(`[data-testid="${id}"]`)).toHaveLength(1);
    expect(h.authoritySubscribers.size).toBe(6);
  });

  it.each(['session', 'provider', 'topology', 'receiver', 'unknown'] as const)(
    'fences retained A1 through synchronous %s A-B-A and admits only fresh A3',
    (kind) => {
      h.state = filterState(); h.caps = filterCaps(); h.selectedFiniteAppearance = finiteAppearance;
      renderHostedDesktop();
      const stateA = h.state as ServerState, capsA = h.caps as Capabilities, sessionA = h.session;
      const retainedA1 = retainedInvocations.get('Mode')!;
      if (kind === 'session') h.session = { state: 'connected', epoch: 8 };
      if (kind === 'provider') {
        h.state = { ...stateA, providerGeneration: 2 }; h.caps = { ...capsA, providerGeneration: 2 };
      }
      if (kind === 'topology') {
        h.caps = { ...capsA, receivers: 1, vfoScheme: 'single',
          capabilities: capsA.capabilities.filter(capability => capability !== 'dual_rx') };
      }
      if (kind === 'receiver') h.state = { ...stateA, active: 'SUB' };
      if (kind === 'unknown') {
        const fieldStatus = { ...stateA.fieldStatus }; delete fieldStatus.active;
        h.state = { ...stateA, fieldStatus };
      }
      publishAuthority();
      h.state = stateA; h.caps = capsA; h.session = sessionA; publishAuthority();
      retainedA1('CW'); expect(h.modeChange).not.toHaveBeenCalled();
      flushSync();
      const retainedA3 = retainedInvocations.get('Mode')!;
      expect(retainedA3).not.toBe(retainedA1);
      retainedA3('CW'); expect(h.modeChange).toHaveBeenCalledExactlyOnceWith('CW');
      retainedA1('FM'); expect(h.modeChange).toHaveBeenCalledTimes(1);
    },
  );

  /**
   * MOR-2425 F1-C2 — the weakest witness: request DATA in Standard, switch
   * to SDR BEFORE any echo updates the confirmed reading, and prove — in
   * this order — (i) the SAME host-owned DATA-mode seat object survives the
   * switch (`finiteSeats`, the `createChoiceRendererSeat` capture wrapper
   * above — a DOM testid alone is re-created by the layout swap on both
   * sides and proves nothing), (ii) the fresh SDR invocation still commands
   * exactly once, (iii) the unconfirmed reading is unaffected by the switch
   * itself, and only then (iv) the stale pre-switch invocation is inert.
   * Counting `filter-data-mode`/`external-DATA mode` occurrences before and
   * after the switch (the vacuous form) would pass unchanged even if the
   * host were torn down and rebuilt, since the grouped SDR surface renders
   * its own DATA seat regardless of which host produced it.
   */
  it('keeps the DATA-mode seat, commands once, and detaches the stale Standard invocation across Standard→SDR', () => {
    h.state = filterState(); h.caps = filterCaps(); h.selectedFiniteAppearance = finiteAppearance;
    const layout = renderHostedLayout('desktop-v2');
    const dataSeatsBefore = finiteSeats.seats.filter(entry => entry.label === 'DATA mode');
    expect(dataSeatsBefore).toHaveLength(1);
    const dataSeat = dataSeatsBefore[0].seat;
    const staleStandardData = retainedInvocations.get('DATA mode')!;
    const beforeReading = q('[data-testid="external-DATA mode"]')?.getAttribute('data-reading');

    layout.skinId = 'sdr-test'; flushSync();

    // (i) Identity, positively: the SAME host-owned seat, not rebuilt.
    const dataSeatsAfter = finiteSeats.seats.filter(entry => entry.label === 'DATA mode');
    expect(dataSeatsAfter).toHaveLength(1);
    expect(dataSeatsAfter[0].seat).toBe(dataSeat);

    // (ii) A fresh, CURRENT invocation exists for the grouped SDR placement
    // and commands exactly once.
    const freshSdrData = retainedInvocations.get('DATA mode')!;
    expect(freshSdrData).not.toBe(staleStandardData);
    freshSdrData(1);
    expect(h.dataModeChange).toHaveBeenCalledExactlyOnceWith(1);

    // (iii) The unconfirmed reading is unaffected by the switch itself —
    // only the click above changes anything downstream.
    expect(q('[data-testid="external-DATA mode"]')?.getAttribute('data-reading')).toBe(beforeReading);

    // (iv) Only now: the stale pre-switch invocation is DETACHED.
    staleStandardData(1);
    expect(h.dataModeChange).toHaveBeenCalledTimes(1);
  });
});

/**
 * MOR-2425 F1-C2 — `filterFiniteAuthority`'s group-presence gate widened
 * from `model?.modeFilter === undefined` alone to `modeFilter === undefined
 * && filterPassband === undefined`, so a passband-only radio (Shape/DATA
 * capability, no mode/filter group at all) still gets a valid Filter
 * authority context. Left at the old gate, authority stays null forever for
 * such a radio, so a Filter finite seat's `createChoiceRendererSeat` lease
 * captures a null context and starts permanently revoked (`createSeat`'s
 * `attachRenderer`, `control-instrument-renderer.svelte.ts`) — the external
 * renderer's `view` is `undefined` and nothing with `data-testid="external-
 * DATA mode"` ever renders, even though the field itself is structurally
 * present.
 */
describe('MOR-2425 F1-C2 — Filter authority admits a passband-only radio', () => {
  function passbandOnlyCaps(): Capabilities {
    return {
      ...liveCaps(true), modes: [], filters: [],
      capabilities: [...liveCaps(true).capabilities, 'data_mode'],
      dataModeCount: 1,
    } as unknown as Capabilities;
  }

  it('renders the DATA-mode seat for a radio with no modeFilter group at all', () => {
    h.state = liveState(true); h.caps = passbandOnlyCaps(); h.selectedFiniteAppearance = finiteAppearance;
    renderHostedDesktop();
    expect(q('[data-testid="filter-finite-grid"]')).not.toBeNull();
    expect(q('[data-testid="external-DATA mode"]')).not.toBeNull();
  });
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
    'vfo-receiver-indicators',
    'vfo-indicator-row', 'receiver-s-meter', 'receiver-s-meter-unknown',
    'vfo-indicator-row', 'receiver-s-meter', 'receiver-s-meter-unknown',
    'vfo-shared-indicators',
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
  /**
   * MOR-1322 (S3b): each VFO tile's `.vfo-freq` slot now holds the self-rendered
   * per-digit tuning control (a `div.freq` of digit/separator spans) instead of a
   * single text node — the interactive filling of the one readout slot. The
   * testid list above is unchanged: the primitive carries no testids, so this
   * outline is the only probe that sees the difference, which is exactly its job.
   */
  /**
   * MOR-1322 (S3b): a tunable VFO tile's `.vfo-freq` slot holds the self-rendered
   * per-digit tuning control (a `div.freq` of digit/separator spans); every other
   * tile keeps its single text node. A tile may tune only when it is the slot its
   * receiver's receiver-scoped `set_freq` would write (verification B1). The
   * testid list above is unchanged — the primitive carries no testids, so this
   * outline is the only probe that sees the difference.
   *
   * MOR-1335 (G4) — DELIBERATE CHANGE, and the end-to-end evidence for it: this
   * mount is `2/main_sub` with BOTH receivers' `activeSlot` observed, through the
   * real adapter and the real wiring. The sequence now carries TWO digit controls
   * (MAIN A and SUB A) where it carried one, because the gate is qualified per
   * RECEIVER instead of per radio. MAIN B and SUB B keep their text nodes — the
   * intra-receiver hazard B1 found stays closed.
   */
  // Four VFO tiles now reserve a cue container, marker and accessible reason.
  const DEFAULT_PATH_OUTLINE = 'div p div div span span div span span span span span span span span span span span span span span span span '
    + 'div span span span span span span button div span span div span span span span span span span span span span span span span span button '
    + 'div span span span span span span button div section header strong div div div '
    + 'section header strong div div div section div span '
    + 'div button button div div button button p span span section p span span span p div button button '
    + 'ul section div button button button label span div div div div div div output div button button button output '
    + 'div button button output';

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
    const cues = target.querySelectorAll('[data-vfo-stale-cue]');
    expect(cues).toHaveLength(4);
    for (const cue of cues) {
      expect(cue.children).toHaveLength(2);
      expect(cue.getAttribute('aria-hidden')).toBe('true');
    }
    const receiverGroup = target.querySelector('[role="radiogroup"][aria-label="Active receiver"]');
    expect(receiverGroup).not.toBeNull();
    expect(receiverGroup?.querySelectorAll('[role="radio"]')).toHaveLength(2);
    expect(outline()).toBe(DEFAULT_PATH_OUTLINE);
  });

  it.each(['single', 'dual'] as const)('mounts the txAux surface when the group is present (%s)', (strips) => {
    render({ strips });
    expect(q('[data-testid="tx-aux-surface"]')).not.toBeNull();
    expect(target.querySelectorAll('[data-testid="tx-aux-surface"]')).toHaveLength(1);
  });

  it('consumes eight scalar handles and one finite/TUNE remainder across composition swaps', () => {
    const props = render({ strips: 'single' });
    const assertExactComposition = () => {
      const root = q<HTMLElement>('[data-testid="tx-aux-surface"]')!;
      expect(root.querySelectorAll('.tx-aux-level [role="slider"]')).toHaveLength(8);
      for (const id of ['atu', 'vox', 'compressor', 'monitor', 'atu-tune']) {
        expect(root.querySelectorAll(`[data-testid="tx-aux-${id}"]`), id).toHaveLength(1);
      }
    };
    assertExactComposition();
    props.strips = 'dual';
    flushSync();
    assertExactComposition();
  });

  // MOR-1336 (S4) UPDATE: the cockpit manifest DOES declare a `tx-aux` zone
  // now (`presentation/layouts/dual-receiver-cockpit.ts`) — what this pin
  // still proves is the STANDALONE-mount path: with no resolved plan handed
  // down through context (`render`'s default here), `zoneOwning` reads no
  // plan and returns `null` for every surface, so the mount stays bare
  // regardless of what any manifest declares (`useSurfacePlan()`'s documented
  // fallback). The zoned case — a plan actually supplied — is pinned
  // separately in `MOR-1336 — the zone-mount mechanism generalizes beyond
  // txAux` below and in `DualReceiverCockpit.component.test.ts`'s F6 suite.
  // MUTATION KILLED: giving the txAux surface a `data-zone-id` here anyway,
  // i.e. ignoring the plan and binding a zone id unconditionally (the
  // MOR-1069 lesson: a zone element must exist only where BOTH a layout
  // declared one AND a plan actually resolved it).
  it('binds no zone id to the txAux surface in either composition, absent a resolved plan', () => {
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

  it('emits no TX intent from a txAux setting', () => {
    render();
    q<HTMLButtonElement>('[data-testid="tx-aux-atu-tune"]')!.click();
    q<HTMLButtonElement>('[data-testid="tx-aux-vox"]')!.click();
    flushSync();
    expect(h.atuTune).toHaveBeenCalledOnce();
    expect(txHarness.trace()).toEqual([]);
  });

  // MOR-1336 (S4) restated (R9): the invariant above holds vacuously once
  // txAux is ALWAYS unzoned — no plan was ever supplied, so `zoneOwning`
  // always returned null. Restated against a RESOLVED plan that actually
  // zones txAux (the cockpit's own), so "no second key authority" is proven
  // for the zoned surface, not merely the bare one.
  it('still keeps exactly one key/unkey authority once a resolved plan actually zones txAux', () => {
    const plan = resolveSurfacePlan(dualReceiverCockpitLayout, readWorkspace({ version: 1 }).workspace);
    render({ strips: 'dual' }, plan);

    const zone = q('[data-zone-id="tx-aux"]');
    expect(zone).not.toBeNull(); // sanity: the zone this pin restates for actually exists
    expect(target.querySelectorAll('[data-testid="rx-tx-surface"]')).toHaveLength(1);
    expect(target.querySelectorAll('[data-testid="rx-tx-key"]')).toHaveLength(1);
    expect(target.querySelectorAll('[data-testid="rx-tx-unkey"]')).toHaveLength(1);
    // ...and none of them live inside the tx-aux zone itself.
    expect(zone!.querySelector('[data-testid="rx-tx-key"]')).toBeNull();
    expect(zone!.querySelector('[data-testid="rx-tx-unkey"]')).toBeNull();
  });
});

// ── 3. ATU TUNE routes through the App-owned TX authority ──────────────────

const BLOCKING: readonly (readonly [string, ManagedAppTxServerSnapshot])[] = [
  ['a fault is reported', { intent: 'rx', observedPtt: 'off', lastError: 'on-timeout' }],
  ['PTT is active', { intent: 'ptt', observedPtt: 'on' }],
  ['TRANSMIT is active', { intent: 'transmit', observedPtt: 'on' }],
  ['the RF state is unknown', { intent: 'rx', observedPtt: 'unknown' }],
  ['TRANSMIT is pending', { intent: 'transmit', observedPtt: 'off' }],
];

describe('ATU TUNE is gated by the live App TX authority', () => {
  it('dispatches the tune command when nothing blocks a key intent', () => {
    render();
    const tune = q<HTMLButtonElement>('[data-testid="tx-aux-atu-tune"]')!;
    expect(tune.disabled).toBe(false);
    tune.click();
    flushSync();
    expect(h.atuTune).toHaveBeenCalledOnce();
    expect(txHarness.trace()).toEqual([]);
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
    expect(txHarness.trace()).toEqual([]);
  });

  it.each(['native', 'external'] as const)('rechecks live authority behind the %s TUNE path', (path) => {
    const liveSnapshot = new ManagedAppTxHarness();
    h.txController = Object.freeze({
      ...txHarness.controller, snapshot: () => liveSnapshot.controller.snapshot(),
    });
    if (path === 'external') h.selectedFiniteAppearance = finiteAppearance;
    render();
    const tune = q<HTMLButtonElement>(path === 'native'
      ? '[data-testid="tx-aux-atu-tune"]' : '[data-testid="external-TUNE"]')!;
    const invoke = path === 'native' ? () => tune.click() : retainedInvocations.get('TUNE')!;
    expect(tune.disabled).toBe(false);
    liveSnapshot.emitServerSnapshot({ intent: 'transmit', observedPtt: 'on' });
    expect(tune.disabled).toBe(false);
    invoke();
    expect(h.atuTune).not.toHaveBeenCalled();
    expect(txHarness.trace()).toEqual([]);
  });

  it('changes TUNE gating only after a server snapshot, never from a command intent', () => {
    render();
    const tune = q<HTMLButtonElement>('[data-testid="tx-aux-atu-tune"]')!;
    const before = txHarness.controller.snapshot();
    txHarness.controller.transmitOn();
    expect(txHarness.controller.snapshot()).toBe(before);
    expect(tune.disabled).toBe(false);
    push({ intent: 'transmit', observedPtt: 'on' });
    expect(tune.disabled).toBe(true);
    tune.disabled = false;
    tune.click();
    flushSync();
    expect(h.atuTune).not.toHaveBeenCalled();
    expect(txHarness.trace()).toEqual([{ transport: 'http', operation: 'transmit_on' }]);
  });

  // MUTATION KILLED: gating the ordinary (non-transmitting) ATU on/off toggle
  // on TX authority too. It sets a tuner mode, it does not emit a carrier —
  // over-gating would strand the operator with an ATU they cannot turn off.
  it('leaves the non-transmitting ATU toggle usable while TUNE is blocked', () => {
    render();
    push({ intent: 'transmit', observedPtt: 'on' });
    expect(q<HTMLButtonElement>('[data-testid="tx-aux-atu-tune"]')!.disabled).toBe(true);
    const atu = q<HTMLButtonElement>('[data-testid="tx-aux-atu"]')!;
    expect(atu.disabled).toBe(false);
    atu.click();
    flushSync();
    expect(h.atuToggle).toHaveBeenCalledOnce();
    expect(txHarness.trace()).toEqual([]);
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
    ['rfPower', 0.81, () => h.rfPower], ['micGain', 129, () => h.micGain],
    ['driveGain', 129, () => h.driveGain], ['voxGain', 51, () => h.voxGain],
    ['antiVoxGain', 31, () => h.antiVoxGain], ['voxDelay', 11, () => h.voxDelay],
    ['compressorLevel', 41, () => h.compLevel], ['monitorLevel', 61, () => h.monLevel],
  ] as const)('routes the "%s" level with its raw value', (field, value, spy) => {
    render();
    const input = q<HTMLElement>(`[data-testid="tx-aux-${field}"] [role="slider"]`)!;
    input.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    flushSync();
    expect(spy()).toHaveBeenCalledExactlyOnceWith(value);
    const others = [h.rfPower, h.micGain, h.driveGain, h.voxGain, h.antiVoxGain,
      h.voxDelay, h.compLevel, h.monLevel].filter((s) => s !== spy());
    for (const other of others) expect(other).not.toHaveBeenCalled();
  });
});

const FEEDBACK_LANES = [
  ['micGain', 'micGain', 'mic-gain', 128, 200],
  ['driveGain', 'driveGain', 'drive-gain', 128, 201],
  ['voxGain', 'voxGain', 'vox-gain', 50, 202],
  ['antiVoxGain', 'antiVoxGain', 'anti-vox-gain', 30, 203],
  ['voxDelay', 'voxDelay', 'vox-delay', 10, 7],
  ['compressorLevel', 'compressorLevel', 'compressor-level', 40, 204],
  ['monitorLevel', 'monitorGain', 'monitor-level', 60, 205],
] as const;

describe('the composed TX/VOX controls consume the real feedback lifecycle', () => {
  it.each(FEEDBACK_LANES)(
    'maps semantic %s to accessor %s and command scope %s', (
      semanticField, accessorField, control, canonical, requested,
    ) => {
      const descriptor = TX_AUX_COMMAND_DESCRIPTORS[accessorField];
      beginCommand({
        id: `feedback-${accessorField}`, name: descriptor.intentName,
        params: { level: requested }, originalEpoch: 7,
      });
      render();
      const row = q<HTMLElement>(`[data-testid="tx-aux-${semanticField}"]`)!;
      const input = row.querySelector<HTMLElement>('[role="slider"]')!;
      expect(input.dataset.commandPhase).toBe('submitted');
      expect(input.getAttribute('aria-valuenow')).toBe(String(canonical));
      expect(row.querySelector('[data-canonical-value]')?.textContent).toContain(
        semanticField === 'voxDelay' ? `${(canonical * 0.1).toFixed(1)}s` : `${Math.round(canonical / 255 * 100)}%`,
      );
      expect(row.dataset.feedbackControl).toBe(control);
      for (const [otherSemantic] of FEEDBACK_LANES) {
        if (otherSemantic !== semanticField) {
          expect(q<HTMLElement>(`[data-testid="tx-aux-${otherSemantic}"] [role="slider"]`)!
            .dataset.commandPhase).toBe('idle');
        }
      }
    },
  );

  it('keeps ACK pending through unrelated, mismatched and stale readback, then confirms fresh exact truth', () => {
    const descriptor = TX_AUX_COMMAND_DESCRIPTORS.micGain;
    const command = beginCommand({
      id: 'mic-feedback', name: descriptor.intentName, params: { level: 200 }, originalEpoch: 7,
    });
    render();
    const input = () => q<HTMLElement>('[data-testid="tx-aux-micGain"] [role="slider"]')!;
    expect(input().dataset.commandPhase).toBe('submitted');
    acknowledgeCommand(command.id, 7, 7);
    flushSync();
    expect(input().dataset.commandPhase).toBe('awaiting-confirmation');

    const observed = (revision: number, micGain: number, freshness: 'fresh' | 'stale') => ({
      ...liveState(true), active: 'SUB', micGain,
      revision, stateRevision: revision, freshnessRevision: revision, observationSeq: revision,
      fieldStatus: {
        ...liveState(true).fieldStatus,
        driveGain: { ...fresh, lastObservedMonotonic: revision + 10 },
        micGain: { ...fresh, freshness, lastObservedMonotonic: revision },
      },
    } as unknown as ServerState);
    pushRadioState(observed(1, 199, 'fresh'));
    pushSession({ state: 'connected', epoch: 7 });
    expect(input().dataset.commandPhase).toBe('awaiting-confirmation');
    // R29: a stale readback no longer fails the control closed, even one
    // that happens to match the requested target exactly — availability and
    // confirmation are separate axes. `reconcileStateBackedCommands`
    // (`commands.svelte.ts`) still requires `freshness === 'fresh'` before
    // marking a command confirmed (untouched by this PR), so the lifecycle
    // stays 'acknowledged' and the control stays in 'awaiting-confirmation'.
    pushRadioState(observed(2, 200, 'stale'));
    pushSession({ state: 'connected', epoch: 7 });
    expect(input().dataset.commandPhase).toBe('awaiting-confirmation');
    expect(input().getAttribute('aria-disabled')).toBe('false');
    expect(getCommandLifecycles()[0]?.status).toBe('acknowledged');
    pushRadioState(observed(3, 200, 'fresh'));
    expect(getCommandLifecycles()[0]?.status).toBe('confirmed');
    pushSession({ state: 'connected', epoch: 7 });
    expect(input().dataset.commandPhase).toBe('confirmed');
    expect(input().getAttribute('aria-valuenow')).toBe('200');
    expect(input().closest('[data-testid]')?.querySelector('[data-command-status]')?.textContent)
      .toContain('confirmed');
    expect(TX_AUX_COMMAND_DESCRIPTORS.micGain.scope(command)?.receiver).toBe(0);
  });

  it('fails closed and recovers in place across provider and session replacement', () => {
    render();
    const input = () => q<HTMLElement>('[data-testid="tx-aux-monitorLevel"] [role="slider"]')!;
    expect(input().dataset.commandPhase).toBe('idle');
    const original = input();

    h.state = { ...liveState(true), providerGeneration: 2 } as ServerState;
    h.caps = { ...liveCaps(true), providerGeneration: 2 } as Capabilities;
    pushSession({ state: 'disconnected', epoch: 8 });
    expect(input()).toBe(original);
    expect(input().getAttribute('aria-disabled')).toBe('true');
    expect(input().dataset.commandPhase).toBe('unavailable');

    pushSession({ state: 'connected', epoch: 8 });
    expect(input()).toBe(original);
    expect(input().getAttribute('aria-disabled')).toBe('false');
    expect(input().dataset.commandPhase).toBe('idle');
    expect(input().getAttribute('aria-valuenow')).toBe('60');
  });

  it('emits only the selected lane handler and no PTT, TUNE, toggle or lifecycle side effect', () => {
    render();
    for (const [semanticField] of FEEDBACK_LANES) {
      const input = q<HTMLElement>(`[data-testid="tx-aux-${semanticField}"] [role="slider"]`)!;
      input.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    }
    flushSync();
    expect([h.micGain, h.driveGain, h.voxGain, h.antiVoxGain, h.voxDelay, h.compLevel, h.monLevel]
      .map(mock => mock.mock.calls.length)).toEqual([1, 1, 1, 1, 1, 1, 1]);
    expect([h.atuTune, h.atuToggle, h.voxToggle, h.compToggle, h.monToggle]
      .map(mock => mock.mock.calls.length)).toEqual([0, 0, 0, 0, 0]);
    expect(txHarness.trace()).toEqual([]);
    expect(getCommandLifecycles()).toEqual([]);
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
    // `mobile`'s `portrait-deck` zone declares ['vfo', 'rxTx']; the operator
    // flipped it. Read from `mobile` since MOR-2231 split sdr-test's pair into
    // `receiver-deck` + `rx-tx`, leaving it no zone that mounts both.
    // MUTATION KILLED: ignoring `zoneOrder` in the single composition, or
    // hard-coding the VFO-before-RX/TX sequence.
    render({ strips: 'single' }, planFor(mobileLayout, {
      zoneOrder: { 'portrait-deck': ['rxTx', 'vfo'] },
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

// ── MOR-1336 (S4) — the zone-mount mechanism is generic, not txAux-shaped ───
//
// `zoneOwning`/`zoned` in `SemanticRadioSurfaces` is written ONCE and applied
// uniformly to every optional surface (txAux, meters, and — single
// composition only — rxAudio). Every pin above proves it exclusively against
// txAux, which was the only one OF THOSE THREE a real manifest declared a
// zone for when these pins were written (`vfo`/`rxTx` had zones from the
// start) —
// a wiring change that special-cased `if (surface === 'txAux')` would pass
// every one of them just as well. These pins exercise the SAME mechanism
// against `meters`, a structurally unrelated surface, through a SYNTHETIC
// plan no shipped manifest declares, so it is the mechanism's own generality
// under test, not any layout's arrangement.
describe('MOR-1336 — the zone-mount mechanism generalizes beyond txAux', () => {
  /**
   * Enough raw state for `deriveMeters` (`radio-view-model-adapter.ts`) to
   * emit the `meters` group: the TX authority and `state` are already
   * supplied by every fixture here, so one observed raw meter field is the
   * only thing missing — `deriveMeters` emits the group the moment any of
   * its seven raw fields is defined, `caps.tx` untouched.
   */
  function withMeterReading(state: ServerState): ServerState {
    return {
      ...state,
      powerMeter: 50,
      fieldStatus: { ...(state.fieldStatus as Record<string, unknown>), powerMeter: fresh },
    } as unknown as ServerState;
  }

  it('mounts a real zone element for meters when a plan declares one, under an id no shipped manifest uses', () => {
    h.state = withMeterReading(liveState(false));
    h.caps = liveCaps(false);
    // MUTATION KILLED: a mechanism secretly keyed on the literal 'tx-aux' id
    // or on `SEMANTIC_SURFACE_NAMES` order rather than the plan's own keys.
    const plan: SurfacePlan = new Map([['synthetic-meters-zone', ['meters']]]);
    render({ strips: 'dual' }, plan);

    const zone = q('[data-zone-id="synthetic-meters-zone"]');
    expect(zone).not.toBeNull();
    expect(zone!.classList.contains('surface-zone')).toBe(true);
    expect(q('[data-testid="meters-surface"]')!.parentElement).toBe(zone);
  });

  it('renders the identical meters content bare when no plan declares a zone for it', () => {
    h.state = withMeterReading(liveState(false));
    h.caps = liveCaps(false);
    render({ strips: 'dual' }); // no plan at all — the pre-S4 / standalone-mount path

    const surface = q('[data-testid="meters-surface"]');
    expect(surface).not.toBeNull();
    expect(surface!.closest('.surface-zone')).toBeNull();
    expect(surface!.closest('[data-zone-id]')).toBeNull();
  });
});

// ── MOR-1336 (S4) — a declared zone is never an empty promise ───────────────
describe('MOR-1336 — a declared zone renders nothing for a radio without the group', () => {
  // MUTATION KILLED: wrapping the zone unconditionally on `zoneId !== null`
  // rather than gating on the surface's own `present` argument first — the
  // exact regression the `zoned` snippet's `present` parameter exists to
  // prevent (see the handover note on `SemanticRadioSurfaces.svelte`).
  it('mounts no tx-aux zone element for a radio without the group, even though the cockpit declares one', () => {
    h.state = liveState(false);
    h.caps = liveCaps(false);
    const plan = resolveSurfacePlan(dualReceiverCockpitLayout, readWorkspace({ version: 1 }).workspace);
    render({ strips: 'dual' }, plan);

    expect(q('[data-testid="tx-aux-surface"]')).toBeNull();
    expect(q('[data-zone-id="tx-aux"]')).toBeNull();
    expect(target.innerHTML).not.toContain('tx-aux');
  });

  // MOR-1341 (S5): the same pin, for `meters` against `desktop-v2`'s own real
  // plan (the manifest that actually declares the zone in production, unlike
  // the synthetic plan the generic-mechanism describe above uses). `liveState`
  // reports no meter fields, so `deriveMeters` emits no group at all.
  it('mounts no meters zone element for a radio without the group, even though desktop-v2 declares one', () => {
    h.state = liveState(false);
    h.caps = liveCaps(false);
    const plan = resolveSurfacePlan(desktopV2Layout, readWorkspace({ version: 1 }).workspace);
    render({ strips: 'single' }, plan);

    expect(q('[data-testid="meters-surface"]')).toBeNull();
    expect(q('[data-zone-id="meters"]')).toBeNull();
    expect(target.innerHTML).not.toContain('data-zone-id="meters"');
  });
});

/**
 * MOR-1304 fix round (verify-MOR-1304 F1) — the zone-mount ruling applied to
 * `filter`, the MOR-1279 rxAudio shape.
 *
 * `FilterSurface` renders up to 14 focusable controls (mode/filter/shape
 * choice buttons, the width slider, three passband-level sliders), and the
 * DUAL composition's only layout (`dual-receiver-cockpit.ts`) declares no
 * `filter` zone. `desktop-v2` DOES declare one — MOR-1366 (S7) — and
 * `filter-declarability.test.ts` pins the declaring set as exactly
 * `['desktop-v2']`. This sentence previously cited that same file as evidence
 * that NO shipped manifest declared the zone, which that file has contradicted
 * since S7 landed.
 * The cockpit's MOR-1069 invariant requires every focusable control to sit
 * inside a zone the active layout's manifest actually declares, with `rx-tx`
 * last in the tab order — a control-bearing surface mounted bare in the DUAL
 * composition breaks both clauses the moment the fixture's caps carry real
 * modes/filters (every real radio does). `caps.modes`/`caps.filters` are
 * empty in this file's own `liveCaps`, which is why this describe supplies
 * its OWN caps override — a fixture that cannot see the group is not
 * evidence the surface behaves, it is the bug this pin exists to catch (the
 * verify report's Probe P1/P2, reproduced here rather than trusted from afar).
 */
describe('MOR-1304 fix round — filter never mounts bare in the dual composition', () => {
  const withFilterCaps = (caps: Capabilities): Capabilities => ({
    ...caps, modes: ['USB', 'CW', 'FM'], filters: ['FIL1', 'FIL2', 'FIL3'],
  } as unknown as Capabilities);

  it('renders NO filter surface in the dual composition, zoned or unzoned', () => {
    h.state = liveState(false);
    h.caps = withFilterCaps(liveCaps(false));
    render({ strips: 'dual' });

    expect(q('[data-testid="filter-surface"]')).toBeNull();
    expect(target.innerHTML).not.toContain('filter-surface');
  });

  it('leaves the cockpit with no focusable control outside a declared zone', () => {
    h.state = liveState(false);
    h.caps = withFilterCaps(liveCaps(false));
    render({ strips: 'dual' });

    const outside = [...target.querySelectorAll<HTMLElement>('button, input, select, [tabindex]')]
      .filter((node) => node.closest('[data-zone-id]') === null);
    expect(outside).toEqual([]);
  });

  // Control: the SAME caps, in the single composition, DO mount the surface —
  // proves the dual absence above is the zone-mount gate, not the fixture
  // simply being unable to produce a `modeFilter` group at all.
  it('mounts the filter surface in the single composition with the same caps', () => {
    h.state = liveState(false);
    h.caps = withFilterCaps(liveCaps(false));
    render({ strips: 'single' });

    expect(q('[data-testid="filter-surface"]')).not.toBeNull();
  });

  // MOR-1366 (S7), N1 fold (verify-MOR-1365 ruling item 3): desktop-v2 now
  // declares a REAL `filter` zone — mirrors the S6a context-injection recipe
  // (`semantic-scope-display-wiring.component.test.ts`) and this file's own
  // `meters` binding pin below.
  it('binds the filter zone id against desktop-v2\'s real plan', () => {
    h.state = liveState(false);
    h.caps = withFilterCaps(liveCaps(false));
    const plan = resolveSurfacePlan(desktopV2Layout, readWorkspace({ version: 1 }).workspace);
    render({ strips: 'single' }, plan);

    expect(q('[data-testid="filter-surface"]')!.closest('[data-zone-id="filter"]')).not.toBeNull();
  });
});

// ── MOR-1341 (S5) — desktop-v2's OWN real `meters` zone actually binds ──────
//
// The generic-mechanism describe above proves the MECHANISM against a
// synthetic id no manifest ships; this proves the SHIPPED zone — the one
// `desktop-v2` actually declares (`presentation/layouts/desktop-declarations
// .ts`) and the one `RadioLayout.svelte` reads to retire the legacy dock.
describe('MOR-1341 — desktop-v2 mounts a real meters zone when the group is present', () => {
  it('binds [data-zone-id="meters"] around the meters surface, alone in its zone', () => {
    const base = liveState(false) as unknown as { main: Record<string, unknown> };
    h.state = { ...base, main: { ...base.main, sMeter: 120 } };
    h.caps = liveCaps(false);
    const plan = resolveSurfacePlan(desktopV2Layout, readWorkspace({ version: 1 }).workspace);
    render({ strips: 'single' }, plan);

    const zone = q('[data-zone-id="meters"]');
    expect(zone).not.toBeNull();
    expect(zone!.classList.contains('surface-zone')).toBe(true);
    expect(q('[data-testid="meters-surface"]')!.parentElement).toBe(zone);
    // R9 sanity: a readout-only zone adds no key/unkey affordance.
    expect(zone!.querySelector('[data-testid="rx-tx-key"]')).toBeNull();
    expect(zone!.querySelector('[data-testid="rx-tx-unkey"]')).toBeNull();
  });
});
