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
  controlSession: { state: 'connected', epoch: 1 } as ControlSessionSnapshot,
  authoritySubscribers: new Set<(next: {
    state: unknown; caps: unknown; session: ControlSessionSnapshot;
    rxAudioTarget: RxAudioTargetSnapshot;
  }) => void>(),
  audio: { muted: true, rxEnabled: false, volume: 0 },
  txController: null as ManagedAppTxController | null,
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
  selectedFiniteAppearance: undefined as unknown,
}));

vi.mock('../../../component-kits/activation', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../component-kits/activation')>();
  return { ...actual, getSelectedFiniteControlAppearance: () => h.selectedFiniteAppearance };
});

vi.mock('$lib/runtime', () => ({
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
    // MOR-1279 slice 3B: the wiring now also hands the adapter an App-owned
    // RX-audio snapshot (the FOURTH argument). Muted with no browser stream
    // keeps every fixture below off the rxAudio path — this file tests dsp.
    get audio() { return h.audio; },
    get connectionAudio() { return false; },
    get radioPowerOn() { return null; },
    // MOR-1312 slice 12B (rebase fix): the wiring now also hands the adapter
    // a scope-display snapshot (the FIFTH argument). This file tests dsp, so
    // this stays on its pre-1312 path regardless of these values.
    get defaultScopeStatus() {
      return {
        source: null, available: false, resourceSelected: false, demand: 0,
        lifecycle: 'inactive', transport: 'disconnected', frameSeen: false,
      };
    },
    get scope() { return { hardwareScopeConnected: false }; },
  },
}));
// `panel-adapters.ts` reads the runtime through `$lib/runtime/frontend-runtime`,
// not `$lib/runtime`, so the hosted NB scalars' command feedback needs this
// second seam stubbed too; `semantic-tx-aux-wiring.component.test.ts` stubs both.
vi.mock('$lib/runtime/frontend-runtime', () => ({
  runtime: {
    get state() { return h.state; },
    get caps() { return h.caps; },
    get controlSession() { return h.controlSession; },
  },
}));
// `beginCommand` stamps each lifecycle with the radio store's `providerGeneration`,
// and the feedback projection discards a command whose generation does not match.
vi.mock('$lib/stores/radio.svelte', () => ({
  radio: { get current() { return h.state; } },
  getRadioState: () => h.state,
  subscribeRadioState: (listener: (state: unknown) => void) => {
    listener(h.state);
    return () => {};
  },
}));
vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({
  getManagedAppTxController: () => h.txController,
}));
vi.mock('$lib/runtime/adapters/mod-input-tx-guard.svelte', () => ({
  deriveModInputTxGuardProps: () => ({ visible: false, sourceLabel: null }),
  getModInputTxGuardHandlers: () => ({ onSetLan: vi.fn(), onDismiss: vi.fn() }),
}));
// The names below are the REAL `makeDspHandlers`/`makeAgcHandlers` surface —
// agreement with the shipped module is proven separately, against the real
// module, in `command-bus.test.ts`.
vi.mock('$lib/runtime/commands/panel-commands', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/runtime/commands/panel-commands')>();
  return {
    ...actual,
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
    // MOR-1310 slice 9B: the wiring now also composes the CW keyer intent
    // vocabulary unconditionally. This fixture declares no cwKeyer capability,
    // so none of these is reachable — same stand-in role as `makeRitXitHandlers`.
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
  };
});

/** MOR-2425 — the `createContinuousScalar` capture wrapper
 *  `DspScalarHost.isolated.test.ts` establishes, lifted to the composed tree so
 *  the persistence witness below can name the binding OBJECTS. Every scalar in
 *  the tree passes through here; `command` separates the two DSP ones. */
const scalars = vi.hoisted(() => ({
  bindings: [] as Array<{ command: string | null; binding: unknown }>,
  leases: [] as Array<{ binding: unknown; lease: unknown }>,
}));
vi.mock('../../../primitives/scalar/continuous-scalar.svelte', async (importOriginal) => {
  const actual = await importOriginal<
    typeof import('../../../primitives/scalar/continuous-scalar.svelte')>();
  return {
    ...actual,
    createContinuousScalar: (...args: Parameters<typeof actual.createContinuousScalar>) => {
      const binding = actual.createContinuousScalar(...args);
      const attachRenderer = binding.attachRenderer.bind(binding);
      binding.attachRenderer = (...attachArgs) => {
        const lease = attachRenderer(...attachArgs);
        scalars.leases.push({ binding, lease });
        return lease;
      };
      const source = args[0]();
      scalars.bindings.push({
        command: source.evidence === 'command-feedback' ? source.command : null, binding,
      });
      return binding;
    },
  };
});

import SemanticRadioSurfaces from '../SemanticRadioSurfaces.svelte';
import HostedRadioLayoutFixture from '../../layout/__tests__/fixtures/HostedRadioLayoutFixture.svelte';
import { ManagedAppTxHarness } from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';
import { desktopV2Layout, sdrTestLayout } from '../../../presentation/layouts/declarations';
import { readWorkspace } from '../../../presentation/workspace/contract';
import {
  resolveSurfacePlan, SURFACE_PLAN_CONTEXT_KEY, type SurfacePlan,
} from '../../../presentation/workspace/resolution';
import FiniteControlRendererFixture, {
  resetRetainedInvocations, retainedInvocations,
} from '../../../primitives/control-instruments/__tests__/support/FiniteControlRendererFixture.svelte';
import type { FiniteControlAppearance } from '../../../primitives/control-instruments/control-instrument-renderer.svelte';
import { beginCommand, resetCommandLifecycle } from '$lib/stores/commands.svelte';
import type {
  ContinuousScalarBinding, ContinuousScalarRendererLease, ContinuousScalarView,
} from '../../../primitives/scalar/continuous-scalar.svelte';


/** `lastObservedMonotonic` is what `display-observation.ts: validEvidence`
 *  requires before a field counts as evidence, and therefore what the two
 *  hosted NB scalars need before their command feedback reports `available`. */
const fresh = {
  storePath: 'x', observed: true, freshness: 'fresh', availability: 'available',
  lastObservedMonotonic: 0,
};
const slot = (freqHz: number) => ({ freqHz, mode: 'USB', filterNum: 1, dataMode: 0 });

/** Every raw dsp field the MOR-1290 adapter reads, all observed fresh. */
const DSP_STATE = {
  nr: true, nrLevel: 128, nb: false, nbLevel: 64, nbDepth: 4, nbWidth: 2,
  autoNotch: false, manualNotch: false, notchFilter: 0, manualNotchWidth: 1,
  agc: 2, agcTimeConstant: 3,
} as const;
const EXACT_NR_DOMAIN = {
  mapping: 'identity', raw_min: 0, raw_max: 10, raw_step: 1, raw_origin: 0,
  display_min: '0', display_max: '10', display_step: '1', display_origin: '0',
  display_unit: 'level', quantization: 'reject', restoration: 'exact',
} as const;
const DSP_PATHS = [
  'main.nr', 'main.nrLevel', 'main.nb', 'main.nbLevel', 'main.autoNotch',
  'main.manualNotch', 'main.manualNotchWidth', 'main.agc', 'main.agcTimeConstant',
  // notchFilter (MOR-1548): reclassified receiver-scoped, so its fieldStatus
  // path moved from top-level to "main." like the other DSP receiver fields.
  'main.notchFilter',
  'nbDepth', 'nbWidth',
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
      manualNotchWidth: DSP_STATE.manualNotchWidth, notchFilter: DSP_STATE.notchFilter,
      agc: DSP_STATE.agc, agcTimeConstant: DSP_STATE.agcTimeConstant } : {}),
  });
  return {
    stateContractVersion: 1, providerGeneration: 1,
    active: 'MAIN', split: false, dualWatch: false, ptt: false,
    txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 14250000 },
    main: receiver(14250000), sub: receiver(14300000),
    ...(withDsp ? { nbDepth: DSP_STATE.nbDepth, nbWidth: DSP_STATE.nbWidth } : {}),
    fieldStatus: Object.fromEntries(paths.map((p) => [p, fresh])),
  } as unknown as ServerState;
}

const liveCaps = (withDsp: boolean): Capabilities => ({
  stateContractVersion: 1, providerGeneration: 1,
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

const finiteAppearance = {
  action: FiniteControlRendererFixture as FiniteControlAppearance['action'],
  toggle: FiniteControlRendererFixture as FiniteControlAppearance['toggle'],
  choice: FiniteControlRendererFixture as FiniteControlAppearance['choice'],
} satisfies FiniteControlAppearance;

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;
let txHarness: ManagedAppTxHarness;

function render(props: { strips?: 'single' | 'dual' } = {}, plan?: SurfacePlan): void {
  target = document.createElement('div');
  document.body.appendChild(target);
  const context = plan === undefined
    ? undefined
    : new Map<unknown, unknown>([[SURFACE_PLAN_CONTEXT_KEY, () => plan]]);
  component = mount(SemanticRadioSurfaces, { target, props, context });
  flushSync();
}

function renderHosted() {
  target = document.createElement('div');
  document.body.appendChild(target);
  const props = proxy({ skinId: 'desktop-v2' as 'desktop-v2' | 'sdr-test' });
  const context = new Map<unknown, unknown>([[SURFACE_PLAN_CONTEXT_KEY, () =>
    resolveSurfacePlan(props.skinId === 'desktop-v2' ? desktopV2Layout : sdrTestLayout,
      readWorkspace({ version: 1 }).workspace)]]);
  component = mount(HostedRadioLayoutFixture, { target, props, context });
  flushSync();
  return props;
}

function publishAuthority(
  state = h.state, caps = h.caps, session = h.controlSession,
): void {
  for (const subscriber of h.authoritySubscribers) subscriber({
    state, caps, session,
    rxAudioTarget: Object.freeze({ muted: h.audio.muted, rxEnabled: h.audio.rxEnabled }),
  });
}

const q = <T extends HTMLElement>(sel: string) => target.querySelector(sel) as T | null;
const slider = (field: string) => q(`[data-testid="dsp-${field}"] [role="slider"]`)!;
const arrowRight = (field: string) => slider(field)
  .dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));

/** The two DSP scalar bindings, in creation order, as OBJECTS. */
const dspBindings = (): unknown[] => scalars.bindings
  .filter(({ command }) => command === 'set_nb_level' || command === 'set_nb_width')
  .map(({ binding }) => binding);
const dspBinding = (command: string): ContinuousScalarBinding => {
  const found = scalars.bindings.filter((entry) => entry.command === command);
  expect(found).toHaveLength(1);
  return found[0]!.binding as ContinuousScalarBinding;
};
const latestLease = (binding: unknown): ContinuousScalarRendererLease => {
  for (let index = scalars.leases.length - 1; index >= 0; index -= 1) {
    if (scalars.leases[index]!.binding === binding) {
      return scalars.leases[index]!.lease as ContinuousScalarRendererLease;
    }
  }
  throw new Error('renderer lease not captured');
};
/** The command-feedback evidence a persistent binding must carry across a switch. */
const lifecycle = (binding: ContinuousScalarBinding) => {
  const view = binding.view as Extract<ContinuousScalarView, { evidence: 'command-feedback' }>;
  return {
    requested: view.requested, confirmed: view.confirmed, phase: view.phase,
    error: view.error, transitionId: view.feedback.transitionId,
  };
};

beforeEach(() => {
  resetCommandLifecycle();
  resetRetainedInvocations();
  scalars.bindings = [];
  scalars.leases = [];
  txHarness = new ManagedAppTxHarness();
  h.txController = txHarness.controller;
  h.state = liveState(true);
  h.caps = liveCaps(true);
  h.controlSession = { state: 'connected', epoch: 1 };
  h.selectedFiniteAppearance = undefined;
  for (const value of Object.values(h)) {
    if (typeof value === 'function' && 'mockReset' in value) (value as ReturnType<typeof vi.fn>).mockReset();
  }
});

afterEach(() => {
  if (component) unmount(component);
  component = null;
  resetCommandLifecycle();
  resetRetainedInvocations();
  expect(h.authoritySubscribers.size).toBe(0);
  expect(txHarness.listenerCount()).toBe(0);
  expect(txHarness.trace()).toEqual([]);
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
    'vfo-surface', 'vfo-active-receiver', 'vfo-list',
    'vfo-receiver-indicators',
    'vfo-indicator-row', 'receiver-s-meter', 'receiver-s-meter-unknown',
    'vfo-indicator-row', 'receiver-s-meter', 'receiver-s-meter-unknown',
    'vfo-shared-indicators',
    'vfo-ops', 'vfo-split-digest',
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
   * zone, with rx-tx last in the tab order. `dual-receiver-cockpit.ts` — the
   * only layout with a dual composition — declares no `dsp` zone, so the dual
   * composition renders NO dsp surface at all — same
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
    ['nrLevel', 5, () => h.nrLevel],
    ['nbDepth', 3, () => h.nbDepth],
    ['notchFreq', 128, () => h.notchFreq], ['manualNotchWidth', 2, () => h.manualNotchWidth],
    ['agcTimeConstant', 4, () => h.agcTime],
  ] as const)('routes the natively rendered "%s" level with its raw value', (field, value, spy) => {
    render();
    const input = q<HTMLInputElement>(`[data-testid="dsp-${field}"] input`)!;
    input.value = String(value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(spy()).toHaveBeenCalledExactlyOnceWith(value);
  });

  /** MOR-2425: `nbLevel`/`nbWidth` are no longer native ranges — they are
   *  `DspScalarHost` handles, stepped from their confirmed raw value
   *  (`DSP_STATE`: 64 and 2) through the shared continuous-scalar lease. */
  it.each([
    ['nbLevel', 65, () => h.nbLevel], ['nbWidth', 3, () => h.nbWidth],
  ] as const)('routes the hosted "%s" scalar with its raw value', (field, next, spy) => {
    render();
    arrowRight(field);
    flushSync();
    expect(spy()).toHaveBeenCalledExactlyOnceWith(next);
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

describe('mounted exact NR projection (MOR-1737)', () => {
  function exactState(raw: number): ServerState {
    const state = liveState(true);
    return { ...state, main: { ...state.main, nrLevel: raw } };
  }

  function exactCaps(): Capabilities {
    const caps = liveCaps(true);
    return {
      ...caps,
      controls: { ...caps.controls, nr_level: EXACT_NR_DOMAIN },
    } as unknown as Capabilities;
  }

  it.each([0, 1, 4, 10])(
    'carries FTX-1 runtime raw %i through the real semantic adapter and mounted surface',
    (raw) => {
      h.state = exactState(raw);
      h.caps = exactCaps();
      render();
      const input = q<HTMLInputElement>('[data-testid="dsp-nrLevel"] input')!;
      expect(input.min).toBe('0');
      expect(input.max).toBe('10');
      expect(input.step).toBe('1');
      expect(input.valueAsNumber).toBe(raw);
      expect(input.disabled).toBe(false);
    },
  );

  it('routes display/raw 4 only to the terminal NR-level handler', () => {
    h.state = exactState(4);
    h.caps = exactCaps();
    render();
    const input = q<HTMLInputElement>('[data-testid="dsp-nrLevel"] input')!;
    input.value = '4';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    flushSync();
    expect(h.nrLevel).toHaveBeenCalledExactlyOnceWith(4);
  });

  it('preserves the safely absent exact-metadata legacy 0..15 projection', () => {
    render();
    const input = q<HTMLInputElement>('[data-testid="dsp-nrLevel"] input')!;
    expect(input.min).toBe('0');
    expect(input.max).toBe('15');
    expect(input.step).toBe('1');
    expect(input.valueAsNumber).toBe(8);
    expect(input.disabled).toBe(false);
  });
});

describe('carry-forward (1): caps-echo display metadata is read at this seam', () => {
  it('passes agcLabels/nbLevelMax/nbLevelPercent from runtime.caps down as props', () => {
    render();
    expect(q('[data-testid="dsp-agcMode-1"]')!.textContent).toBe('FAST');
    expect(slider('nbLevel').getAttribute('aria-valuemax')).toBe('200');
  });

  it('falls back to the toDspProps defaults when caps carries no nb_level range', () => {
    h.caps = { ...liveCaps(true), controls: undefined } as unknown as Capabilities;
    render();
    expect(slider('nbLevel').getAttribute('aria-valuemax')).toBe('10');
  });

  it('does not fabricate FAST/MID/SLOW labels when caps declares no agcLabels (MOR-1547 follow-up)', () => {
    // Mirror of the toAgcProps fix in lib/runtime/props/panel-props.ts
    // (MOR-1547): this seam carried the same hardcoded IC-7610-shaped
    // `{ '1': 'FAST', '2': 'MID', '3': 'SLOW' }` fallback, fabricating
    // plausible-looking labels for radios whose numeric AGC modes mean
    // something different. With no declared `agcLabels`, `buildAgcOptions`
    // (agc-utils.ts) must fall back to the honest raw mode number instead.
    h.caps = { ...liveCaps(true), agcLabels: undefined } as unknown as Capabilities;
    render();
    expect(q('[data-testid="dsp-agcMode-1"]')!.textContent).toBe('1');
  });
});

describe('desktop-v2 declares a real dsp zone; the cockpit does not (MOR-1368, S9, F1)', () => {
  function planFor(layout: typeof desktopV2Layout, fields: Record<string, unknown>): SurfacePlan {
    return resolveSurfacePlan(layout, readWorkspace({ version: 1, ...fields }).workspace);
  }

  it('binds the dsp zone id against desktop-v2\'s real plan', () => {
    render({ strips: 'single' }, planFor(desktopV2Layout, {}));
    expect(q('[data-testid="dsp-surface"]')!.closest('[data-zone-id="dsp"]')).not.toBeNull();
  });
});

describe('persistent finite DSP composition and authority (MOR-2425)', () => {
  const groupedExternal = ['NR', 'NB', 'Notch mode', 'AGC mode'] as const;
  const standardExternal = ['NB', 'NR', 'NOTCH', 'A-NOTCH', 'AGC mode'] as const;
  /**
   * MOR-2425: the DSP levels `DspSurface` still owns natively, in
   * `DSP_LEVELS` order. Named rather than counted — a bare cardinality is
   * satisfied by ANY two controls going missing, including the regression
   * where a hosted scalar renders neither natively nor as a handle.
   */
  const NATIVE_RANGE_FIELDS = [
    'nrLevel', 'nbDepth', 'notchFreq', 'manualNotchWidth', 'agcTimeConstant',
  ];
  const rangeFields = () => [...target.querySelectorAll<HTMLInputElement>(
    '[data-testid="dsp-surface"] input[type="range"]',
  )].map((input) => input.closest<HTMLElement>('[data-field]')?.dataset.field);
  const seatFields = (selector: string) => [...target.querySelectorAll<HTMLElement>(selector)]
    .map((seat) => seat.dataset.field);

  it('moves one hosted set from named Standard seats to grouped SDR without replacing its host', () => {
    h.selectedFiniteAppearance = finiteAppearance;
    h.state = proxy(liveState(true) as object);
    const props = renderHosted();
    const root = q('[data-testid="semantic-radio-surfaces"]');
    const staleStandardNr = retainedInvocations.get('NR')!;

    expect(seatFields('.dsp-finite-seat'))
      .toEqual(['agcMode', 'nbActive', 'nrActive', 'manualNotch', 'autoNotch']);
    expect(seatFields('.dsp-scalar-seat')).toEqual([]);
    for (const label of standardExternal) {
      expect(target.querySelectorAll(`[data-testid="external-${label}"]`)).toHaveLength(1);
    }
    expect(rangeFields()).toEqual([]);

    (h.state as { main: Record<string, unknown> }).main.nr = false;
    props.skinId = 'sdr-test';
    flushSync();
    expect(q('[data-testid="semantic-radio-surfaces"]')).toBe(root);
    expect(target.querySelectorAll('[data-testid="dsp-surface"]')).toHaveLength(1);
    expect(target.querySelectorAll('.dsp-finite-seat')).toHaveLength(0);
    expect(target.querySelectorAll('.dsp-scalar-seat')).toHaveLength(0);
    for (const label of groupedExternal) {
      expect(target.querySelectorAll(`[data-testid="external-${label}"]`)).toHaveLength(1);
    }
    expect(rangeFields()).toEqual(NATIVE_RANGE_FIELDS);
    for (const field of ['nbLevel', 'nbWidth']) {
      expect(target.querySelectorAll(`[data-testid="dsp-${field}"]`)).toHaveLength(1);
    }

    staleStandardNr();
    expect(h.nrMode).not.toHaveBeenCalled();
    const currentSdrNr = retainedInvocations.get('NR')!;
    expect(currentSdrNr).not.toBe(staleStandardNr);
    currentSdrNr();
    expect(h.nrMode).toHaveBeenCalledExactlyOnceWith(1);
  });

  /**
   * MOR-2425 — the witness the persistent scalar host exists for, in the
   * order its parts must be read. A test that only drove the retained
   * pre-switch lease and asserted `not.toHaveBeenCalled()` would be GREENER
   * under the very defect it is meant to exclude: a host torn down and
   * rebuilt per face loses the binding, and a destroyed binding commands
   * nothing either. So identity comes first, then proof that the current
   * lease still commands, then the surviving evidence — and only then the
   * inertness claim.
   */
  it('keeps the nbWidth binding, its pending evidence and its live lease across Standard→SDR', () => {
    h.selectedFiniteAppearance = finiteAppearance;
    h.state = proxy(liveState(true) as object);
    const props = renderHosted();
    vi.useFakeTimers();
    q('[data-testid="external-NB"]')!.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
    vi.advanceTimersByTime(500);
    q('[data-testid="external-NB"]')!.dispatchEvent(new PointerEvent('pointerup', { bubbles: true }));
    flushSync();
    vi.useRealTimers();
    beginCommand({
      id: 'pending-nb-width', name: 'set_nb_width', params: { level: 7 }, originalEpoch: 1,
    });
    flushSync();
    const before = dspBindings();
    expect(before).toHaveLength(2);
    const nbWidth = dspBinding('set_nb_width');
    const beforeLifecycle = lifecycle(nbWidth);
    expect(beforeLifecycle).toMatchObject({ phase: 'submitted', requested: 7, confirmed: 2 });
    const staleLease = latestLease(nbWidth);

    props.skinId = 'sdr-test';
    flushSync();
    const afterLifecycle = lifecycle(nbWidth);

    // (i) Identity, positively: the SAME two binding objects, not rebuilt ones.
    const after = dspBindings();
    expect(after).toHaveLength(2);
    expect(after[0]).toBe(before[0]);
    expect(after[1]).toBe(before[1]);

    // (ii) Admission proven open: the CURRENT lease emits exactly one command,
    // stepped from the CONFIRMED raw 2 (`DSP_STATE`) — the policy dispatches
    // canonical, so the in-flight target 7 is not the interaction base.
    expect(latestLease(nbWidth)).not.toBe(staleLease);
    arrowRight('nbWidth');
    flushSync();
    expect(h.nbWidth).toHaveBeenCalledExactlyOnceWith(3);

    // (iii) Evidence survival, read at the moment after the switch.
    expect(afterLifecycle).toEqual(beforeLifecycle);
    expect(target.querySelectorAll('[data-feedback-lane="nbWidth"]')).toHaveLength(1);
    expect(q('[data-testid="dsp-nbWidth"]')!.dataset.feedbackReceiver).toBe('0');

    // Only now: the retained Standard lease is inert, and adds no command.
    expect(staleLease.beginPointer()).toBeNull();
    expect(staleLease.key({ key: 'ArrowRight', fine: false })).toBe(false);
    staleLease.nativeInput(200);
    staleLease.wheel({ direction: 1, fine: false });
    flushSync();
    expect(h.nbWidth).toHaveBeenCalledOnce();
  });

  it('keeps selected controls inert without authority and adds no DSP subscriber', () => {
    h.selectedFiniteAppearance = finiteAppearance;
    h.controlSession = { state: 'disconnected', epoch: 1 };
    render();
    expect(target.querySelectorAll('.dsp-toggle, .dsp-choice')).toHaveLength(0);
    for (const label of groupedExternal) expect(q(`[data-testid="external-${label}"]`)).toBeNull();
    expect(rangeFields()).toEqual(NATIVE_RANGE_FIELDS);
    // Receiver + AF + RF level + station meter + antenna hosts + the single
    // finite-renderer fan-in.
    expect(h.authoritySubscribers.size).toBe(6);
  });

  it('maps NOTCH to manual, A-NOTCH to auto, and selected clicks to off', () => {
    h.selectedFiniteAppearance = finiteAppearance;
    h.state = proxy(liveState(true) as object);
    renderHosted();

    retainedInvocations.get('NOTCH')!();
    expect(h.notchMode).toHaveBeenLastCalledWith('manual');
    (h.state as { main: Record<string, unknown> }).main.manualNotch = true;
    flushSync();
    retainedInvocations.get('NOTCH')!();
    expect(h.notchMode).toHaveBeenLastCalledWith('off');

    (h.state as { main: Record<string, unknown> }).main.manualNotch = false;
    (h.state as { main: Record<string, unknown> }).main.autoNotch = false;
    flushSync();
    retainedInvocations.get('A-NOTCH')!();
    expect(h.notchMode).toHaveBeenLastCalledWith('auto');
    (h.state as { main: Record<string, unknown> }).main.autoNotch = true;
    flushSync();
    retainedInvocations.get('A-NOTCH')!();
    expect(h.notchMode).toHaveBeenLastCalledWith('off');
  });

  it('keeps AGC mode in the left panel and exposes AGC time once in right DSP', () => {
    h.state = proxy(liveState(true) as object);
    renderHosted();
    const agcButtons = [...target.querySelectorAll<HTMLButtonElement>('button')]
      .filter(button => button.textContent?.trim().startsWith('AGC-T'));
    expect(agcButtons).toHaveLength(1);
    expect(agcButtons[0].textContent).toContain('▾');
    expect(agcButtons[0].closest('.dsp-agc-time')?.getAttribute('data-expanded')).toBe('false');
    expect(target.querySelectorAll('[data-testid="dsp-agcTimeConstant"]')).toHaveLength(0);
    agcButtons[0].click();
    flushSync();
    expect(agcButtons[0].textContent).toContain('▴');
    expect(agcButtons[0].closest('.dsp-agc-time')?.getAttribute('data-expanded')).toBe('true');
    expect(target.querySelectorAll('[data-testid="dsp-agcTimeConstant"]')).toHaveLength(1);
    expect(q('[data-testid="dsp-agcTimeConstant"]')!.closest('[data-part="dsp"]')).not.toBeNull();
    expect(q('[data-testid="dsp-agcTimeConstant"]')!.closest('[data-part="agc"]')).toBeNull();
  });

  it('opens and closes NB settings from its chevron without sending an NB command', () => {
    h.selectedFiniteAppearance = finiteAppearance;
    h.state = proxy(liveState(true) as object);
    renderHosted();
    const open = q<HTMLButtonElement>('button[title="Open NB settings"]')!;
    expect(open.textContent).toContain('▾');
    expect(open.getAttribute('aria-label')).toBe('NB settings');
    expect(open.getAttribute('aria-expanded')).toBe('false');
    expect(open.getAttribute('aria-controls')).toBe('dsp-nb-settings');
    expect(open.closest('.compact-dsp-button')?.getAttribute('data-expanded')).toBe('false');
    expect(q('button[title="Open NR settings"]')).not.toBeNull();
    expect(q('button[title="Open NOTCH settings"]')).not.toBeNull();
    expect(q('button[title*="A-NOTCH settings"]')).toBeNull();

    open.click();
    flushSync();
    expect(q('[data-testid="dsp-nbLevel"]')).not.toBeNull();
    const close = q<HTMLButtonElement>('button[title="Close NB settings"]')!;
    expect(close.textContent).toContain('▴');
    expect(close.getAttribute('aria-expanded')).toBe('true');
    expect(q(`#${close.getAttribute('aria-controls')}`)).not.toBeNull();
    expect(close.closest('.compact-dsp-button')?.getAttribute('data-expanded')).toBe('true');
    expect(target.querySelectorAll('#dsp-nb-settings .vc-hbar.hw-illum')).toHaveLength(3);
    expect(h.nbToggle).not.toHaveBeenCalled();

    close.click();
    flushSync();
    expect(q('[data-testid="dsp-nbLevel"]')).toBeNull();
    expect(h.nbToggle).not.toHaveBeenCalled();

    q<HTMLButtonElement>('button[title="Open NR settings"]')!.click();
    flushSync();
    expect(target.querySelectorAll('#dsp-nr-settings .vc-hbar.hw-illum')).toHaveLength(1);
    q<HTMLButtonElement>('button[title="Close NR settings"]')!.click();
    q<HTMLButtonElement>('button[title="Open NOTCH settings"]')!.click();
    flushSync();
    expect(target.querySelectorAll('#dsp-notch-settings .vc-hbar.hw-illum')).toHaveLength(2);
    expect(h.nrMode).not.toHaveBeenCalled();
    expect(h.notchMode).not.toHaveBeenCalled();
  });

  it('disables an open AGC-time adjustment when its reading becomes stale', () => {
    h.state = proxy(liveState(true) as object);
    renderHosted();
    const agcButton = [...target.querySelectorAll<HTMLButtonElement>('button')]
      .find(button => button.textContent?.trim().startsWith('AGC-T'))!;
    agcButton.click();
    flushSync();
    expect(agcButton.getAttribute('aria-label')).toBe('AGC time settings');
    expect(agcButton.getAttribute('aria-expanded')).toBe('true');
    expect(q(`#${agcButton.getAttribute('aria-controls')}`)).not.toBeNull();
    const status = (h.state as ServerState).fieldStatus as unknown as Record<string, {
      storePath: string; observed: boolean; freshness: string; availability: string;
      lastObservedMonotonic: number;
    }>;
    status['main.agcTimeConstant'] = {
      ...fresh, observed: false, freshness: 'stale', availability: 'unavailable',
    };
    flushSync();
    const slider = q<HTMLElement>('[data-testid="dsp-agcTimeConstant"] [role="slider"]')!;
    expect(slider.closest('.vc-hbar')?.classList.contains('hw-illum')).toBe(true);
    expect(slider.getAttribute('aria-disabled')).toBe('true');
    slider.dispatchEvent(new KeyboardEvent('keydown', { key: 'ArrowRight', bubbles: true }));
    expect(h.agcTime).not.toHaveBeenCalled();
  });

  it('opens NR settings after one held Space despite key repeat and suppresses its click', () => {
    h.selectedFiniteAppearance = finiteAppearance;
    h.state = proxy(liveState(true) as object);
    renderHosted();
    const nr = q<HTMLButtonElement>('[data-testid="external-NR"]')!;
    vi.useFakeTimers();
    try {
      nr.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', repeat: false, bubbles: true }));
      vi.advanceTimersByTime(300);
      nr.dispatchEvent(new KeyboardEvent('keydown', { key: ' ', repeat: true, bubbles: true }));
      vi.advanceTimersByTime(200);
      nr.dispatchEvent(new KeyboardEvent('keyup', { key: ' ', bubbles: true }));
      nr.click();
      flushSync();
      expect(q('[data-testid="dsp-nrLevel"]')).not.toBeNull();
      expect(h.nrMode).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it.each([
    ['session', (state: ServerState, caps: Capabilities) =>
      ({ state, caps, session: { state: 'connected', epoch: 2 } as ControlSessionSnapshot })],
    ['provider', (state: ServerState, caps: Capabilities) => ({
      state: { ...state, providerGeneration: 2 } as ServerState,
      caps: { ...caps, providerGeneration: 2 } as Capabilities, session: h.controlSession,
    })],
    ['topology', (state: ServerState, caps: Capabilities) => ({
      state, caps: { ...caps, vfoScheme: 'ab_shared', receivers: 2 } as Capabilities,
      session: h.controlSession,
    })],
    ['active receiver', (state: ServerState, caps: Capabilities) => ({
      state: { ...state, active: 'SUB' } as ServerState, caps, session: h.controlSession,
    })],
  ] as const)('synchronously fences A1 across %s A-B-A and admits only A3', (_axis, b) => {
    h.selectedFiniteAppearance = finiteAppearance;
    render();
    const state = h.state as ServerState;
    const caps = h.caps as Capabilities;
    const retainedA1 = retainedInvocations.get('NR')!;
    const alternate = b(state, caps);
    publishAuthority(alternate.state, alternate.caps, alternate.session);
    publishAuthority(state, caps, h.controlSession);
    retainedA1();
    expect(h.nrMode).not.toHaveBeenCalled();

    flushSync();
    const retainedA3 = retainedInvocations.get('NR')!;
    expect(retainedA3).not.toBe(retainedA1);
    retainedA3();
    expect(h.nrMode).toHaveBeenCalledExactlyOnceWith(0);
  });

  it('keeps context through value, pending, slot, and label changes while views stay current', () => {
    h.selectedFiniteAppearance = finiteAppearance;
    h.state = proxy(liveState(true) as object);
    h.caps = proxy(liveCaps(true) as object);
    render();
    const retainedNr = retainedInvocations.get('NR')!;
    const retainedAgc = retainedInvocations.get('AGC mode')!;

    (h.state as { main: Record<string, unknown> }).main = {
      ...(h.state as { main: Record<string, unknown> }).main, nr: false, activeSlot: 'B',
    };
    (h.caps as { agcLabels: Record<string, string> }).agcLabels['1'] = 'QUICK';
    beginCommand({ id: 'pending-nr', name: 'set_nr', params: { on: true, receiver: 0 }, originalEpoch: 1 });
    publishAuthority();
    flushSync();

    expect(retainedInvocations.get('NR')).toBe(retainedNr);
    expect(retainedInvocations.get('AGC mode')).toBe(retainedAgc);
    expect(q('[data-testid="external-NR"]')!.getAttribute('aria-pressed')).toBe('false');
    expect(q('[data-testid="external-AGC mode-1"]')!.textContent).toBe('QUICK');
    retainedNr();
    expect(h.nrMode).toHaveBeenCalledExactlyOnceWith(1);
  });
});
