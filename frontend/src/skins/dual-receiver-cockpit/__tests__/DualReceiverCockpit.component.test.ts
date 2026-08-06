/**
 * MOR-1067 — the compiled dual-receiver-cockpit SHELL, mounted end to end
 * against the REAL adapter and REAL semantic surfaces (only the runtime
 * singleton and the TX authority are mocked, same pattern as
 * `components-v2/wiring/__tests__/semantic-rx-tx-wiring.component.test.ts`).
 *
 * Covers the ticket's discriminating requirements: per-receiver VFO strips
 * driven by the dual-receiver topologies, single-receiver exclusion (proved
 * separately at the registry in `presentation/layouts/__tests__/
 * dual-receiver-cockpit.test.ts`), exactly one TX authority surface, the
 * scope/controls/global zones staying inert, and no shell-level fabrication
 * of an active strip. Each test's doc line names the mutation it kills.
 */
import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';

const h = vi.hoisted(() => ({
  state: null as unknown,
  caps: null as unknown,
  snapshot: null as unknown,
  listeners: new Set<(next: unknown) => void>(),
  start: vi.fn(),
  release: vi.fn(),
  setIntent: vi.fn(),
  resetFault: vi.fn(),
  selectVfo: vi.fn(),
  splitToggle: vi.fn(),
  dualWatchToggle: vi.fn(),
  modInputGuard: { visible: false, sourceLabel: null } as { visible: boolean; sourceLabel: string | null },
  /** MOR-1265: stand-in for every txAux intent. This fixture declares no
   *  txAux capability, so the MOR-1244 evidence gate omits the group. */
  txAuxNoop: vi.fn(),
  /** MOR-1305: same stand-in role for every dsp intent — no dsp capability
   *  or state here either, so the group is absent and these are unreachable. */
  dspNoop: vi.fn(),
}));

vi.mock('$lib/runtime', () => ({
  runtime: {
    get state() { return h.state; },
    get caps() { return h.caps; },
    // MOR-1279 slice 3B: the wiring now also hands the adapter an App-owned
    // RX-audio snapshot (the FOURTH argument).
    get audio() { return { muted: true, rxEnabled: false, volume: 0 }; },
    get connectionAudio() { return false; },
    // MOR-1312 slice 12B: the wiring now also hands the adapter a
    // scope-display snapshot (the FIFTH argument). This fixture declares no
    // scope capability, so this stays on its pre-1312 path regardless.
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
      return () => h.listeners.delete(listener);
    },
    start: h.start,
    setIntent: h.setIntent,
    release: h.release,
    resetFault: h.resetFault,
  }),
}));
vi.mock('$lib/runtime/adapters/mod-input-tx-guard.svelte', () => ({
  deriveModInputTxGuardProps: () => h.modInputGuard,
  getModInputTxGuardHandlers: () => ({ onSetLan: vi.fn(), onDismiss: vi.fn() }),
}));
vi.mock('../../../components-v2/wiring/command-bus', () => ({
  makeVfoHandlers: () => ({
    onVfoSelect: h.selectVfo,
    onSplitToggle: h.splitToggle,
    onDualWatchToggle: h.dualWatchToggle,
  }),
  // MOR-1265 — the wiring now also composes the txAux intent vocabulary.
  makeVoxHandlers: () => ({
    onVoxToggle: h.txAuxNoop, onVoxGainChange: h.txAuxNoop,
    onAntiVoxGainChange: h.txAuxNoop, onVoxDelayChange: h.txAuxNoop,
  }),
  makeTxHandlers: () => ({
    onRfPowerChange: h.txAuxNoop, onMicGainChange: h.txAuxNoop, onAtuToggle: h.txAuxNoop,
    onAtuTune: h.txAuxNoop, onVoxToggle: h.txAuxNoop, onCompToggle: h.txAuxNoop,
    onCompLevelChange: h.txAuxNoop, onMonToggle: h.txAuxNoop,
    onMonLevelChange: h.txAuxNoop, onDriveGainChange: h.txAuxNoop,
  }),
  // MOR-1279 slice 3B: the RX-audio intent vocabulary.
  makeRxAudioHandlers: () => ({ onMonitorModeChange: h.txAuxNoop, onAfLevelChange: h.txAuxNoop }),
  // MOR-1310 slice 9B: the semantic CW-keyer surface's setting intents.
  makeCwPanelHandlers: () => ({
    onKeySpeedChange: h.txAuxNoop, onCwPitchChange: h.txAuxNoop, onBreakInDelayChange: h.txAuxNoop,
    onBreakInModeChange: h.txAuxNoop, onApfChange: h.txAuxNoop, onTwinPeakToggle: h.txAuxNoop,
    onReversePaddleToggle: h.txAuxNoop,
  }),
  makeAudioRoutingHandlers: () => ({ onFocusChange: h.txAuxNoop, onSplitStereoChange: h.txAuxNoop }),
  // MOR-1304 — the wiring now also composes the modeFilter/filterPassband
  // intent vocabulary; `makeModeHandlers` is composed at both call sites
  // (rxAudio's MOD-input remedy and filterIntents), so the stub carries both.
  // The default fixture (`mainSubCaps()`) declares no filter capability, so
  // the MOR-1280/1284 evidence gate omits both groups by default.
  makeModeHandlers: () => ({
    onModInputChange: h.txAuxNoop, onModeChange: h.txAuxNoop, onDataModeChange: h.txAuxNoop,
  }),
  makeFilterHandlers: () => ({
    onFilterChange: h.txAuxNoop, onFilterWidthChange: h.txAuxNoop, onFilterShapeChange: h.txAuxNoop,
    onIfShiftChange: h.txAuxNoop, onPbtInnerChange: h.txAuxNoop, onPbtOuterChange: h.txAuxNoop,
  }),
  // MOR-1305 — the wiring now also composes the dsp intent vocabulary.
  makeDspHandlers: () => ({
    onNrModeChange: h.dspNoop, onNrLevelChange: h.dspNoop, onNbToggle: h.dspNoop,
    onNbLevelChange: h.dspNoop, onNbDepthChange: h.dspNoop, onNbWidthChange: h.dspNoop,
    onNotchModeChange: h.dspNoop, onNotchFreqChange: h.dspNoop,
    onManualNotchWidthChange: h.dspNoop, onAgcTimeChange: h.dspNoop,
  }),
  makeAgcHandlers: () => ({ onAgcModeChange: h.dspNoop }),
  // MOR-1306 slice 6B: the RF-front-end intent vocabulary.
  makeRfFrontEndHandlers: () => ({
    onAttChange: h.txAuxNoop, onPreChange: h.txAuxNoop, onRfGainChange: h.txAuxNoop,
    onSquelchChange: h.txAuxNoop, onDigiSelToggle: h.txAuxNoop, onIpPlusToggle: h.txAuxNoop,
  }),
  // MOR-1307 slice 7B: the band-select intent the band surface composes.
  makeBandHandlers: () => ({ onBandSelect: h.txAuxNoop }),
  // MOR-1309 slice 8C: the antenna intent vocabulary.
  makeAntennaHandlers: () => ({ onSelectAnt1: h.txAuxNoop, onSelectAnt2: h.txAuxNoop, onToggleRxAnt: h.txAuxNoop }),
  // MOR-1308 slice 8B: the RIT/XIT and scan intent vocabularies. MOR-1351
  // hardened `mainSubCaps()` to carry `rit`/`xit` tags, so this group IS
  // reachable through the default fixture below — these stubs are exercised,
  // not merely composition-time stand-ins.
  makeRitXitHandlers: () => ({
    onRitToggle: h.txAuxNoop, onXitToggle: h.txAuxNoop, onRitOffsetChange: h.txAuxNoop,
    onXitOffsetChange: h.txAuxNoop, onClear: h.txAuxNoop,
  }),
  makeScanHandlers: () => ({
    onScanStart: h.txAuxNoop, onScanStop: h.txAuxNoop, onDfSpanChange: h.txAuxNoop,
    onResumeChange: h.txAuxNoop,
  }),
  // MOR-1311 slice 11B: the scope-toolbar/popover intent vocabulary.
  makeScopeControlsHandlers: () => ({
    onModeChange: h.txAuxNoop, onEdgeChange: h.txAuxNoop, onSpanChange: h.txAuxNoop,
    onSpeedChange: h.txAuxNoop, onHoldChange: h.txAuxNoop, onRefChange: h.txAuxNoop,
    onDualChange: h.txAuxNoop, onReceiverChange: h.txAuxNoop, onDuringTxChange: h.txAuxNoop,
    onCenterTypeChange: h.txAuxNoop, onVbwChange: h.txAuxNoop, onRbwChange: h.txAuxNoop,
  }),
}));

import DualReceiverCockpit from '../DualReceiverCockpit.svelte';
// MOR-1068 F6: the manifest's declared zone ids, read through the app-wide
// registration barrel (never '../../../presentation/layouts/
// dual-receiver-cockpit' directly), so the DOM assertions below are checked
// against what the app actually registers rather than a local copy.
import { dualReceiverCockpitLayout } from '../../../presentation/layouts/declarations';
import { readWorkspace } from '../../../presentation/workspace/contract';
import {
  resolveSurfacePlan, SURFACE_PLAN_CONTEXT_KEY, type SurfacePlan,
} from '../../../presentation/workspace/resolution';

type Snapshot = {
  phase: string; intent: string | null; guard: { leaseId: string } | null;
  radioTx: string; txRisk: string; mayOwnKey: boolean; fault: string | null;
};
const IDLE: Snapshot = {
  phase: 'idle', intent: null, guard: null, radioTx: 'off', txRisk: 'none',
  mayOwnKey: false, fault: null,
};
const fresh = { storePath: 'x', observed: true, freshness: 'fresh', availability: 'available' };

/** 2/main_sub: MAIN and SUB each carry A/B slots (4 vfo tiles total). */
function mainSubState(active: 'MAIN' | 'SUB' = 'MAIN'): ServerState {
  const paths = ['active', 'split', 'dualWatch', 'txTarget'];
  for (const rx of ['main', 'sub']) {
    paths.push(`${rx}.activeSlot`);
    for (const v of ['vfoA', 'vfoB']) paths.push(`${rx}.${v}.freqHz`, `${rx}.${v}.mode`, `${rx}.${v}.filterNum`);
  }
  const slot = (hz: number) => ({ freqHz: hz, mode: 'USB', filterNum: 1 });
  const receiver = (hz: number) => ({ vfoA: slot(hz), vfoB: slot(hz + 30000), activeSlot: 'A' });
  return {
    active, split: true, dualWatch: true, ptt: false,
    txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 14250000 },
    main: receiver(14250000), sub: receiver(21295000),
    fieldStatus: Object.fromEntries(paths.map((p) => [p, fresh])),
  } as unknown as ServerState;
}
/**
 * MOR-1336 (S4): the cockpit declares a `tx-aux` zone now, so this fixture must
 * carry enough TX-aux evidence for the MOR-1244 gate to emit the group —
 * otherwise the surface self-gates away, the zone has nothing to hold, and the
 * F6 "every declared zone renders" invariant fails for the right reason but the
 * wrong cause. `tuner` was the cheapest honest evidence (`deriveTxAux` accepts
 * a capability tag OR a raw value) when this fixture carried only one tag; as
 * of MOR-1351 all five `deriveTxAux` evidence tags
 * (`tuner`/`vox`/`compressor`/`monitor`/`drive_gain`) are present. This DOES
 * land the controls inside the declared `tx-aux` zone rather than zone-less —
 * unlike the browser fixture catalog (`fixtures/catalog.ts`), this harness's
 * `render()` (below) DOES accept and resolve a real `SurfacePlan`
 * (`render(defaultPlan())`, used by every MOR-1069 assertion in this file), so
 * `zoneOwning()` is NOT unconditionally null here — the earlier claim that it
 * was (pre-MOR-1351) was wrong for this file; that reasoning only holds for
 * `fixtures/catalog.ts`'s browser harness, which had NO plan context at all
 * pre-MOR-1355. As of MOR-1355 the browser harness ALSO resolves a plan, but
 * only for its one `topology-2-main-sub--planned` fixture — every other
 * `fixtures/catalog.ts` fixture is still plan-less by design (see that
 * ticket's `main.ts`/`catalog.ts` comments), so the statement still holds for
 * every capture except that one.
 *
 * MOR-1351: `modes`/`filters` and the rest of the capability tags were an
 * inert placeholder (`[]` / four tags) — the view-model groups those tags
 * gate (`deriveModeFilter`/`deriveFilterPassband`/`deriveAgc`/
 * `deriveRfFrontEnd`/`deriveRitXit`/`deriveCwKeyer`, all in
 * `radio-view-model-adapter.ts`) are none of them consumed by
 * `SemanticRadioSurfaces.svelte` (verified against its own imports: it
 * renders only `vfo`/`rxTx`/`txAux`/`meters`/`rxAudio`), so a vacuous caps
 * object here proved nothing about those gates being honestly absent versus
 * simply never exercised. Hardened to a REAL radio's shape — IC-7610
 * (`rigs/ic7610.toml`), the only
 * dual-receiver profile in the tree and already this fixture's implied
 * topology (`receivers: 2`, `vfoScheme: 'main_sub'`) — modes/filters/tags
 * verbatim from that profile, WITH `scope` now included: `caps.scope: true`
 * AND the `'scope'` tag together (`presentation-capabilities.ts`'s `agreed()`
 * requires the boolean and the tag to agree — the resolution is to flip BOTH
 * to true, not to drop the tag while leaving the boolean disagreeing). The
 * MOR-1085 `audio-only-scope` contrast fixture (`audioOnlyScopeCaps` below)
 * now states its own `scope: false` condition explicitly instead of
 * inheriting an unstated default.
 */
const mainSubCaps = (): Capabilities => ({
  model: 'fixture', scope: true, audio: true, tx: true,
  capabilities: [
    'audio', 'tx', 'dual_rx', 'tuner', 'dual_watch', 'lan_dual_rx_audio_routing',
    'af_level', 'rf_gain', 'squelch', 'attenuator', 'preamp', 'digisel', 'ip_plus',
    'antenna', 'rx_antenna', 'nb', 'nr', 'notch', 'apf', 'twin_peak', 'pbt',
    'filter_width', 'filter_shape', 'split', 'ssb_tx_bw', 'cw', 'break_in', 'rit', 'xit',
    'meters', 'data_mode', 'mod_input_routing', 'agc', 'power_control', 'dial_lock',
    'scan', 'bsr', 'main_sub_tracking', 'tuning_step', 'band_edge', 'xfc', 'system_settings',
    'scope', 'vox', 'compressor', 'monitor', 'drive_gain',
  ],
  receivers: 2, vfoScheme: 'main_sub',
  freqRanges: [{ start: 1800000, end: 54000000, label: 'HF+6m', bands: [
    { name: '20m', start: 14000000, end: 14350000, default: 14195000 },
    { name: '40m', start: 7000000, end: 7300000, default: 7100000 },
  ] }],
  modes: ['USB', 'LSB', 'CW', 'CW-R', 'AM', 'FM', 'RTTY', 'RTTY-R', 'PSK', 'PSK-R'],
  filters: ['FIL1', 'FIL2', 'FIL3'],
  antennas: 2,
  attValues: [0, 3, 6, 9, 12, 15, 18, 21, 24, 27, 30, 33, 36, 39, 42, 45],
  preValues: [0, 1, 2],
  agcModes: [1, 2, 3], agcLabels: { '1': 'FAST', '2': 'MID', '3': 'SLOW' },
  audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
  webrtc: { available: false, enabled: false },
  txBands: [{ start: 14000000, end: 14350000, name: '20m' }],
  scopeSource: null, audioFftAvailable: false,
} as unknown as Capabilities);

/** 2/ab_shared: MAIN and SUB are each a single unslotted VFO (2 vfo tiles total). */
function abSharedState(): ServerState {
  const paths = ['active', 'split', 'dualWatch', 'txTarget', 'main.freqHz', 'main.mode', 'main.filter',
    'sub.freqHz', 'sub.mode', 'sub.filter'];
  const receiver = (hz: number) => ({ freqHz: hz, mode: 'CW', filter: 1 });
  return {
    active: 'SUB', split: false, dualWatch: true, ptt: false,
    txTarget: { status: 'known', receiver: 'SUB', slot: null, frequencyHz: 3573000 },
    main: receiver(3573000), sub: receiver(3573000),
    fieldStatus: Object.fromEntries(paths.map((p) => [p, fresh])),
  } as unknown as ServerState;
}
/** Same shape as `mainSubState`, minus the 'active' fieldStatus entry — activeReceiver stays unknown. */
function mainSubStateUnknownActive(): ServerState {
  const state = mainSubState();
  const { active: _dropped, ...fieldStatus } = state.fieldStatus as Record<string, unknown>;
  return { ...state, fieldStatus } as unknown as ServerState;
}
const abSharedCaps = (): Capabilities => ({
  ...mainSubCaps(), vfoScheme: 'ab_shared',
} as unknown as Capabilities);

/**
 * MOR-1068 degrade case: 1/single — ONE receiver, one unslotted VFO. The
 * manifest refuses this topology, so resolution sends it to sdr-test
 * (`presentation/layouts/__tests__/cockpit-topology-adaptation.test.ts`); the
 * shell must nonetheless degrade honestly if it is mounted over a
 * single-receiver view model — one strip, no fabricated SUB, and no
 * `secondary-vfo` zone standing empty.
 */
function singleReceiverState(): ServerState {
  const paths = ['active', 'split', 'dualWatch', 'txTarget',
    'main.freqHz', 'main.mode', 'main.filter'];
  return {
    active: 'MAIN', split: false, dualWatch: false, ptt: false,
    txTarget: { status: 'known', receiver: 'MAIN', slot: null, frequencyHz: 14195000 },
    main: { freqHz: 14195000, mode: 'USB', filter: 1 },
    fieldStatus: Object.fromEntries(paths.map((p) => [p, fresh])),
  } as unknown as ServerState;
}
/** 1/single caps: no `dual_rx` tag, or the topology derivation contradicts itself. */
const singleReceiverCaps = (): Capabilities => ({
  ...mainSubCaps(), receivers: 1, vfoScheme: 'single', scope: false, capabilities: ['audio', 'tx'],
} as unknown as Capabilities);

/** The ticket's operational audio-scope condition: scope=false + audioFft=true. */
const audioOnlyScopeCaps = (): Capabilities => ({
  ...mainSubCaps(), scope: false,
  capabilities: mainSubCaps().capabilities.filter((t) => t !== 'scope'),
  audioFftAvailable: true,
  // MOR-1312 slice 12B (rebase fix): `hasAnyScopeCap` (the scopeDisplay
  // facts gate, radio-view-model-adapter.ts) reads `scopeSource`, not
  // `audioFftAvailable` — this fixture predates 12B and only set the latter.
  // Naming the audio-FFT source here keeps `hasAnyScopeCap` true across both
  // caps variants in this describe block, matching this fixture's own intent
  // (scope=false but audio-FFT IS an available scope source) and preserving
  // the "changes nothing in the cockpit" invariant the test proves.
  scopeSource: 'audio_fft',
} as unknown as Capabilities);

/**
 * MOR-1256: `2/main_sub` structurally (receivers=2, main_sub scheme) but
 * missing the `dual_rx` tag — `derivePresentationCapabilities` diagnoses
 * `dual-rx-unavailable` and reports `operationalReceivers: ['MAIN']` while
 * `structuralReceivers` stays `['MAIN', 'SUB']`. The SUB strip must still
 * mount (MOR-977: structural ✓ renders present), just disabled (operational
 * ✗). Reuses `mainSubState` — the diagnostic is purely a capability fact,
 * independent of what state.sub happens to report.
 */
const dualRxUnavailableCaps = (): Capabilities => ({
  ...mainSubCaps(), scope: false, capabilities: ['audio', 'tx'],
} as unknown as Capabilities);

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;

/**
 * `plan` mirrors what App actually hands down through context (MOR-1082) —
 * omitted, this is the pre-1082/standalone mount `useSurfacePlan()` documents
 * ("Absent by design ... every consumer renders its declared composition
 * unchanged"), which is what every pre-existing test below still exercises.
 * The MOR-1336 (S4) generic zone-mount mechanism reads the SAME plan
 * (`zoneOwning` in `SemanticRadioSurfaces`), so proving the cockpit's new
 * `tx-aux` zone actually binds — rather than merely being declared — needs a
 * resolved plan supplied, exactly as it is supplied in production.
 */
function render(plan?: SurfacePlan): void {
  target = document.createElement('div');
  document.body.appendChild(target);
  const context = plan === undefined
    ? undefined
    : new Map<unknown, unknown>([[SURFACE_PLAN_CONTEXT_KEY, () => plan]]);
  component = mount(DualReceiverCockpit, { target, context });
  flushSync();
}

/** The plan App resolves for the cockpit when the operator expressed no
 *  visibleSurfaces/zoneOrder preference — i.e. every declared zone, verbatim. */
function defaultPlan(): SurfacePlan {
  return resolveSurfacePlan(dualReceiverCockpitLayout, readWorkspace({ version: 1 }).workspace);
}

const q = <T extends HTMLElement>(sel: string) => target.querySelector(sel) as T | null;
const qa = <T extends HTMLElement>(sel: string) => [...target.querySelectorAll<T>(sel)];

beforeEach(() => {
  h.snapshot = { ...IDLE };
  h.listeners.clear();
  h.start.mockReset();
  h.release.mockReset();
  h.setIntent.mockReset();
  h.resetFault.mockReset();
  h.selectVfo = vi.fn();
  h.splitToggle = vi.fn();
  h.dualWatchToggle = vi.fn();
  h.modInputGuard = { visible: false, sourceLabel: null };
});

afterEach(() => {
  if (component) unmount(component);
  component = null;
  document.body.innerHTML = '';
});

describe('per-receiver VFO strips, driven by the dual-receiver topologies', () => {
  it('2/main_sub: renders one strip per receiver, each holding only that receiver\'s tiles', () => {
    h.state = mainSubState('MAIN');
    h.caps = mainSubCaps();
    render();

    expect(qa('[data-testid^="channel-strip-"]')).toHaveLength(2);
    const mainStrip = q('[data-testid="channel-strip-MAIN"]')!;
    const subStrip = q('[data-testid="channel-strip-SUB"]')!;
    expect(mainStrip.querySelectorAll('[data-vfo-tile]')).toHaveLength(2);
    expect(subStrip.querySelectorAll('[data-vfo-tile]')).toHaveLength(2);
    // Kills: strips sharing one unfiltered vfo list instead of splitting.
    expect([...mainStrip.querySelectorAll('[data-vfo-tile]')].every(
      (el) => (el as HTMLElement).dataset.vfoReceiver === 'MAIN',
    )).toBe(true);
    expect([...subStrip.querySelectorAll('[data-vfo-tile]')].every(
      (el) => (el as HTMLElement).dataset.vfoReceiver === 'SUB',
    )).toBe(true);
  });

  it('2/ab_shared: renders one strip per receiver with exactly one tile each', () => {
    h.state = abSharedState();
    h.caps = abSharedCaps();
    render();

    expect(qa('[data-testid^="channel-strip-"]')).toHaveLength(2);
    expect(q('[data-testid="channel-strip-MAIN"]')!.querySelectorAll('[data-vfo-tile]')).toHaveLength(1);
    expect(q('[data-testid="channel-strip-SUB"]')!.querySelectorAll('[data-vfo-tile]')).toHaveLength(1);
  });

  // Review cycle 1, F1. `2/ab_shared` gives each receiver exactly ONE
  // unslotted VFO, so a strip that gates selection on its own slice sees
  // `vfos.length === 1` and renders the control ABSENT — the operator loses
  // every way to change the active receiver, on a topology this layout
  // declares itself compatible with. The gate must read the whole radio.
  it('2/ab_shared: offers exactly one control, and it selects the NON-active receiver', () => {
    h.state = abSharedState();          // SUB is the active receiver
    h.caps = abSharedCaps();
    render();

    const selects = qa<HTMLButtonElement>('[data-vfo-select]');
    expect(selects).toHaveLength(1);
    // Present, enabled, and living in the strip the operator would switch TO.
    expect(selects[0].disabled).toBe(false);
    expect(q('[data-testid="channel-strip-MAIN"]')!.contains(selects[0])).toBe(true);

    selects[0].click();
    flushSync();
    expect(h.selectVfo).toHaveBeenCalledTimes(1);
    expect(h.selectVfo).toHaveBeenCalledWith('MAIN', null);
  });

  it('marks the positively-observed active receiver\'s strip, and only that one', () => {
    h.state = mainSubState('SUB');
    h.caps = mainSubCaps();
    render();

    expect(q('[data-testid="channel-strip-SUB"]')!.dataset.stripActive).toBe('true');
    expect(q('[data-testid="channel-strip-MAIN"]')!.dataset.stripActive).toBe('false');
  });

  // The mutation this ticket names explicitly: a shell that fabricates an
  // active strip when activeReceiver was never observed.
  it('fabricates NO active strip when activeReceiver is unknown', () => {
    h.state = mainSubStateUnknownActive();
    h.caps = mainSubCaps();
    render();

    expect(q('[data-testid="channel-strip-MAIN"]')!.dataset.stripActive).toBe('false');
    expect(q('[data-testid="channel-strip-SUB"]')!.dataset.stripActive).toBe('false');
  });
});

// MOR-1256 — MOR-1068 verification finding N2: `operationalReceivers` had
// zero consumers, so a `dual-rx-unavailable` radio (structurally dual,
// operationally degraded) rendered a fully ENABLED SUB strip. The fix must
// leave the SUB strip PRESENT (structural doctrine untouched) but its
// controls genuinely inert, and must not leak into MAIN or the shared TX
// surface — the MOR-557 lens applied in both directions.
describe('dual-rx-unavailable: SUB strip present but operationally disabled (MOR-1256)', () => {
  it('renders the SUB strip present, its select controls really disabled, MAIN fully live', () => {
    h.state = mainSubState('MAIN');
    h.caps = dualRxUnavailableCaps();
    render();

    const subStrip = q('[data-testid="channel-strip-SUB"]')!;
    expect(subStrip).not.toBeNull();
    expect(subStrip.dataset.stripOperational).toBe('false');
    const subSelects = subStrip.querySelectorAll<HTMLButtonElement>('[data-vfo-select]');
    expect(subSelects.length).toBeGreaterThan(0);
    subSelects.forEach((b) => expect(b.disabled).toBe(true));

    // Kill-test (2): the disabled-SUB fixture must not affect MAIN liveness.
    const mainStrip = q('[data-testid="channel-strip-MAIN"]')!;
    expect(mainStrip.dataset.stripOperational).toBe('true');
    const mainSelects = mainStrip.querySelectorAll<HTMLButtonElement>('[data-vfo-select]');
    expect(mainSelects.length).toBeGreaterThan(0);
    mainSelects.forEach((b) => expect(b.disabled).toBe(false));
  });

  // Kill-test (2), the TX half: single TX authority is untouched by this
  // ticket — the shared RxTxSurface must not become per-strip-gated.
  it('does not touch the shared TX surface — the key control stays live and reaches the authority', () => {
    h.state = mainSubState('MAIN');
    h.caps = dualRxUnavailableCaps();
    render();

    expect(qa('[data-testid="rx-tx-surface"]')).toHaveLength(1);
    const key = q<HTMLButtonElement>('[data-testid="rx-tx-key"]')!;
    expect(key.disabled).toBe(false);
    key.click();
    flushSync();
    expect(h.start).toHaveBeenCalledTimes(1);
  });

  // Pin round (verifier N1): the radio-wide row's immunity to the SUB
  // per-receiver gate was correct but unpinned. Split/dual-watch are
  // radio-wide facts (`showRadioWideFacts` only on the global-zone
  // `VfoSurface`, `disabled` only on the per-receiver strips' `VfoSurface`
  // instances) — mirrors the TX-authority protection above, which the
  // original build already pinned; this closes the same gap for the
  // global row.
  it('leaves the radio-wide split and dual-watch switches enabled and reaching their handlers, with SUB operationally degraded', () => {
    h.state = mainSubState('MAIN');
    h.caps = dualRxUnavailableCaps();
    render();

    const split = q<HTMLButtonElement>('[data-vfo-split]')!;
    const dualWatch = q<HTMLButtonElement>('[data-vfo-dual-watch]')!;
    expect(split.disabled).toBe(false);
    expect(dualWatch.disabled).toBe(false);

    split.click();
    dualWatch.click();
    flushSync();
    expect(h.splitToggle).toHaveBeenCalledTimes(1);
    expect(h.dualWatchToggle).toHaveBeenCalledTimes(1);
  });

  // Regression pin: a fully dual-capable radio (dual_rx present) must render
  // both strips operational — this ticket's fix must not fire unconditionally.
  it('a fully dual-capable radio keeps SUB operational', () => {
    h.state = mainSubState('MAIN');
    h.caps = mainSubCaps();
    render();

    const subStrip = q('[data-testid="channel-strip-SUB"]')!;
    expect(subStrip.dataset.stripOperational).toBe('true');
    const subSelects = subStrip.querySelectorAll<HTMLButtonElement>('[data-vfo-select]');
    expect(subSelects.length).toBeGreaterThan(0);
    subSelects.forEach((b) => expect(b.disabled).toBe(false));
  });
});

describe('radio-wide facts render once per cockpit, never once per strip', () => {
  // Review cycle 1, F2. split / dualWatch / activeReceiver are radio-WIDE
  // facts that `forReceiver` (correctly) passes through to every slice, so a
  // naive strip-per-receiver mount renders each of them TWICE: two
  // independent-looking `role="switch"` controls for one radio fact — a
  // duplicated aria-checked pair for assistive tech, and exactly the
  // "duplicated radio behavior" MOR-976 rules out. The shell got the TX half
  // of the one-control-per-radio-wide-authority rule right; this is the
  // split/dual-watch half.
  it.each([
    ['2/main_sub', () => mainSubState('MAIN'), mainSubCaps],
    ['2/ab_shared', abSharedState, abSharedCaps],
  ] as const)('%s: exactly one split switch, one dual-watch switch, one active-receiver line', (
    _topology, makeState, makeCaps,
  ) => {
    h.state = makeState();
    h.caps = makeCaps();
    render();

    expect(qa('[data-testid^="channel-strip-"]')).toHaveLength(2);
    expect(qa('[data-vfo-split]')).toHaveLength(1);
    expect(qa('[data-vfo-dual-watch]')).toHaveLength(1);
    expect(qa('[data-testid="vfo-active-receiver"]')).toHaveLength(1);
  });

  // Rendering the radio-wide row once must not cost the operator the control:
  // the surviving switch still reaches the same radio-wide handler.
  it('the single surviving dual-watch switch still reaches the radio-wide handler', () => {
    h.state = mainSubState('MAIN');
    h.caps = mainSubCaps();
    render();

    const dualWatch = q<HTMLButtonElement>('[data-vfo-dual-watch]')!;
    expect(dualWatch.disabled).toBe(false);
    dualWatch.click();
    flushSync();
    expect(h.dualWatchToggle).toHaveBeenCalledTimes(1);
  });
});

describe('exactly one authoritative TX action surface across both strips', () => {
  it('renders a single shared RX/TX surface and a single key button', () => {
    h.state = mainSubState('MAIN');
    h.caps = mainSubCaps();
    render();

    expect(qa('[data-testid="rx-tx-surface"]')).toHaveLength(1);
    expect(qa('[data-testid="rx-tx-key"]')).toHaveLength(1);
  });

  it('unkey stays ungated and reaches the same App TX authority the sdr-test wiring uses', () => {
    h.state = mainSubState('MAIN');
    h.caps = mainSubCaps();
    render();
    push({ phase: 'failed', fault: 'audio-failed', guard: { leaseId: 'x' }, mayOwnKey: true });

    const unkey = q<HTMLButtonElement>('[data-testid="rx-tx-unkey"]')!;
    expect(unkey.disabled).toBe(false);
    unkey.click();
    flushSync();
    expect(h.release).toHaveBeenCalledTimes(1);
  });
});

describe('scope/controls zones — placed, never falsely active', () => {
  // MOR-1068: `global` left this list — it now carries the real radio-wide
  // row (see below), and MOR-1067's F7 note is explicit that `aria-disabled`
  // may only stand while a zone is honest-by-emptiness. A zone holding live
  // switches must gate at the widget, which the switches already do.
  it('both are present, structurally disabled, and hold nothing interactive', () => {
    h.state = mainSubState('MAIN');
    h.caps = mainSubCaps();
    render();

    for (const zone of ['scope', 'controls']) {
      const el = q(`[data-testid="cockpit-zone-${zone}"]`)!;
      expect(el).not.toBeNull();
      expect(el.getAttribute('aria-disabled')).toBe('true');
      expect(el.dataset.zoneActive).toBe('false');
      // "Unsupported surfaces never appear enabled": empty, so there is no
      // widget for the marker to lie about.
      expect(el.querySelectorAll('button, [role="switch"], input')).toHaveLength(0);
    }
  });

  // Kills: keeping `aria-disabled` on the global zone after real content
  // landed in it — MOR-1067 F7. An `aria-disabled` container wrapping enabled
  // switches is precisely the MOR-557 bug class inverted: a live control
  // presenting as dead.
  it('the global zone is NOT marked disabled once it carries the live row', () => {
    h.state = mainSubState('MAIN');
    h.caps = mainSubCaps();
    render();

    const global = q('[data-zone-id="global"]')!;
    expect(global).not.toBeNull();
    expect(global.getAttribute('aria-disabled')).toBeNull();
    expect(global.dataset.zoneActive).not.toBe('false');
  });
});

// MOR-1067 verification F6: the manifest declared `primary-vfo` /
// `secondary-vfo` / `rx-tx` while the shell's DOM shared not one id with it,
// and nothing asserted the correspondence. These tests read the zone ids out
// of the REGISTERED manifest and require the rendered tree to expose exactly
// those, in declaration order — the two descriptions can no longer drift.
describe('F6 — manifest zone ids are bound to the rendered structure', () => {
  const declaredZoneIds = (): readonly string[] => dualReceiverCockpitLayout.zones.map((z) => z.id);

  // MOR-1336 (S4): `tx-aux` joined the declared set, and it is the FIRST zone
  // here whose existence in the DOM is plan-gated rather than hardcoded
  // (`vfo`/`rxTx` stay bespoke and render their box unconditionally) — so
  // proving the correspondence now needs the SAME resolved plan App actually
  // hands down, or the new zone's box would never appear regardless of what
  // the manifest declares (`useSurfacePlan()`'s documented standalone-mount
  // fallback). Both fixtures already carry `tuner` (MOR-1336 fixture note
  // above), so `view.txAux` is present and the zone is not an empty promise.
  it.each([
    ['2/main_sub', () => mainSubState('MAIN'), mainSubCaps],
    ['2/ab_shared', abSharedState, abSharedCaps],
  ] as const)('%s: renders every declared zone, once, in declaration order', (
    _topology, makeState, makeCaps,
  ) => {
    h.state = makeState();
    h.caps = makeCaps();
    render(defaultPlan());

    expect(qa('[data-zone-id]').map((el) => el.dataset.zoneId)).toEqual([...declaredZoneIds()]);
  });

  // Kills: a zone id invented in the DOM that no manifest zone declares (the
  // drift direction that made the shell's five ad-hoc ids invisible to the
  // registry). The inert placeholders are deliberately NOT manifest zones:
  // `LayoutZone` requires at least one semantic surface and MOR-1062/1065
  // ship none for scope/controls.
  it('no rendered zone id is undeclared, and the placeholders claim none', () => {
    h.state = mainSubState('MAIN');
    h.caps = mainSubCaps();
    render();

    for (const el of qa('[data-zone-id]')) {
      expect(declaredZoneIds()).toContain(el.dataset.zoneId);
    }
    for (const zone of ['scope', 'controls']) {
      expect(q(`[data-testid="cockpit-zone-${zone}"]`)!.hasAttribute('data-zone-id')).toBe(false);
    }
  });

  // Kills: binding zone ids to a fixed strip count. `primary-vfo` is the
  // FIRST rendered strip whatever the topology; `secondary-vfo` exists only
  // when a second receiver was actually observed.
  it.each([
    ['2/main_sub', () => mainSubState('MAIN'), mainSubCaps, 'MAIN', 'SUB'],
    ['2/ab_shared', abSharedState, abSharedCaps, 'MAIN', 'SUB'],
  ] as const)('%s: primary-vfo is the first strip, secondary-vfo the second', (
    _topology, makeState, makeCaps, primary, secondary,
  ) => {
    h.state = makeState();
    h.caps = makeCaps();
    render();

    expect(q('[data-zone-id="primary-vfo"]')!.dataset.stripReceiver).toBe(primary);
    expect(q('[data-zone-id="secondary-vfo"]')!.dataset.stripReceiver).toBe(secondary);
    // #5(b): strip ORDER, pinned. Kills a reversed/receiver-sorted render.
    expect(qa('[data-testid^="channel-strip-"]').map((el) => el.dataset.stripReceiver))
      .toEqual([primary, secondary]);
  });
});

describe('degrading to a single-receiver view model', () => {
  // "One compiled layout safely degrades across all pairs; no impossible
  // receiver/VFO labels." Kills: a shell that renders a second, empty strip
  // (or a fabricated SUB label) when only one receiver was observed.
  it('renders one strip, no SUB anywhere, and no secondary-vfo zone', () => {
    h.state = singleReceiverState();
    h.caps = singleReceiverCaps();
    render();

    expect(qa('[data-testid^="channel-strip-"]')).toHaveLength(1);
    expect(q('[data-zone-id="primary-vfo"]')!.dataset.stripReceiver).toBe('MAIN');
    expect(q('[data-zone-id="secondary-vfo"]')).toBeNull();
    expect(q('[data-testid="channel-strip-SUB"]')).toBeNull();
    expect(target.textContent).not.toContain('SUB');
  });

  // Kills: degrading by dropping the radio-wide row or the TX authority with
  // the second strip. Single TX authority is non-negotiable in EVERY topology.
  it('keeps exactly one radio-wide row and exactly one TX action surface', () => {
    h.state = singleReceiverState();
    h.caps = singleReceiverCaps();
    render();

    expect(qa('[data-zone-id="global"]')).toHaveLength(1);
    expect(qa('[data-vfo-split]')).toHaveLength(1);
    expect(qa('[data-vfo-dual-watch]')).toHaveLength(1);
    expect(qa('[data-testid="rx-tx-surface"]')).toHaveLength(1);
    expect(qa('[data-testid="rx-tx-key"]')).toHaveLength(1);
  });

  // The declared zone set is a superset here, and that is the honest reading:
  // a zone is rendered when its content exists, never as an empty promise.
  it('renders a strict, declared subset of the manifest zones', () => {
    h.state = singleReceiverState();
    h.caps = singleReceiverCaps();
    render();

    const rendered = qa('[data-zone-id]').map((el) => el.dataset.zoneId);
    expect(rendered).toEqual(['primary-vfo', 'global', 'rx-tx']);
    for (const id of rendered) {
      expect(dualReceiverCockpitLayout.zones.map((z) => z.id)).toContain(id);
    }
  });
});

describe('the radio-wide row lives in the cockpit\'s global zone', () => {
  // MOR-1067 verification #4: the row used to sit inside the FIRST strip, so
  // with SUB active "Active receiver: SUB" rendered in the column WITHOUT the
  // active border. It is radio-wide, so it belongs to no receiver's column.
  // #5(a): its PRESENCE is pinned here — deleting split/dual-watch fails.
  it.each([
    ['2/main_sub', () => mainSubState('SUB'), mainSubCaps],
    ['2/ab_shared', abSharedState, abSharedCaps],
  ] as const)('%s: split, dual-watch and active-receiver render once, outside every strip', (
    _topology, makeState, makeCaps,
  ) => {
    h.state = makeState();
    h.caps = makeCaps();
    render();

    const global = q('[data-zone-id="global"]')!;
    for (const sel of ['[data-vfo-split]', '[data-vfo-dual-watch]', '[data-testid="vfo-active-receiver"]']) {
      expect(qa(sel)).toHaveLength(1);
      expect(global.contains(q(sel)!)).toBe(true);
      // Kills: reintroducing the row inside a strip (the MOR-1067 placement).
      expect(qa('[data-testid^="channel-strip-"]').some((s) => s.contains(q(sel)!))).toBe(false);
    }
    // The global row is facts only — the VFO tiles stay in the strips.
    expect(global.querySelectorAll('[data-vfo-tile]')).toHaveLength(0);
  });

  // Kills: moving the row at the cost of its wiring — the switches must still
  // reach the radio-wide handlers from their new home.
  it('the relocated switches still reach the radio-wide handlers', () => {
    h.state = mainSubState('SUB');
    h.caps = mainSubCaps();
    render();

    q<HTMLButtonElement>('[data-vfo-split]')!.click();
    q<HTMLButtonElement>('[data-vfo-dual-watch]')!.click();
    flushSync();
    expect(h.splitToggle).toHaveBeenCalledTimes(1);
    expect(h.dualWatchToggle).toHaveBeenCalledTimes(1);
  });

  // #5(b): strip accessible NAMING. Three groups mount at once (two strips +
  // the radio-wide row); with one shared generic name assistive tech cannot
  // tell MAIN from SUB, and the ticket's "no impossible receiver/VFO labels"
  // has no a11y half at all.
  it('names each mounted surface distinctly for assistive tech', () => {
    h.state = mainSubState('MAIN');
    h.caps = mainSubCaps();
    render();

    const names = qa('[data-testid="vfo-surface"]').map((el) => el.getAttribute('aria-label'));
    expect(names).toHaveLength(3);
    expect(new Set(names).size).toBe(3);
    expect(names.every((n) => (n?.length ?? 0) > 0)).toBe(true);
    expect(names.every((n) => !n?.includes('core.vfo.'))).toBe(true);
    const named = (receiver: string) =>
      q(`[data-testid="channel-strip-${receiver}"] [data-testid="vfo-surface"]`)!
        .getAttribute('aria-label');
    expect(named('MAIN')).toContain('MAIN');
    expect(named('SUB')).toContain('SUB');
  });
});

describe('operational audio-scope availability (scope=false + audioFft=true)', () => {
  // The ticket's fifth, orthogonal condition. The cockpit mounts no scope
  // surface, so the honest behaviour is to make NO claim either way: the
  // rendered tree must be identical with and without a live audio-FFT scope,
  // and the inert scope placeholder must not turn into an "unavailable"
  // verdict about a capability that IS available. Kills: gating any strip,
  // switch or TX control on scope state.
  /**
   * The RX/TX surface's aria-describedby carries a per-instance counter.
   * MOR-1336 (S4): `mainSubCaps` now carries `tuner`, so the txAux surface
   * also mounts here and its own blocked-list id (`TxAuxSurface`'s module
   * counter) is exactly as per-instance — this test unmounts and remounts
   * within itself, so the second mount's id is never the first's.
   */
  const markup = (): string => target.innerHTML
    .replace(/rx-tx-\d+/g, 'rx-tx-N')
    .replace(/tx-aux-blocked-\d+/g, 'tx-aux-blocked-N');

  it('changes nothing in the cockpit, and denies nothing about the radio', () => {
    h.state = mainSubState('MAIN');
    h.caps = mainSubCaps();
    render();
    const withoutAudioFft = markup();

    if (component) unmount(component);
    document.body.innerHTML = '';
    h.state = mainSubState('MAIN');
    h.caps = audioOnlyScopeCaps();
    render();

    expect(markup()).toBe(withoutAudioFft);
    expect(qa('[data-testid="rx-tx-key"]')).toHaveLength(1);
    expect(q('[data-testid="cockpit-zone-scope"]')!.textContent).toBe('');
  });
});

// ── MOR-1069: the mounted half of the responsive-composition policy. The
// breakpoint/orientation rules themselves are CSS and jsdom does not evaluate
// media queries, so they are pinned textually in
// `presentation/layouts/__tests__/cockpit-responsive-composition.test.ts`.
// What IS observable here — and what the ticket's acceptance evidence is
// actually about — is that the composition is CSS-only: the same DOM, the
// same elements and the same TX ownership at every viewport and orientation.

describe('MOR-1069 — a viewport or orientation change recomposes nothing at runtime', () => {
  /** Drive the signals a JS-based responsive implementation would listen to. */
  function rotateToPortraitPhone(): void {
    for (const [axis, value] of [['innerWidth', 390], ['innerHeight', 844]] as const) {
      Object.defineProperty(window, axis, { value, configurable: true, writable: true });
    }
    window.dispatchEvent(new Event('resize'));
    window.dispatchEvent(new Event('orientationchange'));
    flushSync();
  }

  // The ticket's headline evidence: "portrait/landscape replacement preserves
  // TX/resource ownership" and "hidden secondary zones do not destroy active
  // runtime resources". Both hold trivially IF the arrangement change never
  // touches the DOM — so that is what is pinned, by ELEMENT IDENTITY rather
  // than by markup equality. Kills: re-implementing the reflow as JS state
  // (a matchMedia subscription, a resize listener, an `{#if isPortrait}`),
  // which remounts this subtree on rotation, destroys the RxTxSurface that
  // owns the operator's only unkey control, and re-runs the wiring's
  // `onDestroy` lease release under a fresh sourceId.
  it('keeps every zone element, the TX surface and the key control identical across a rotation', () => {
    h.state = mainSubState('MAIN');
    h.caps = mainSubCaps();
    render();

    const zonesBefore = qa('[data-zone-id]');
    const keyBefore = q('[data-testid="rx-tx-key"]')!;
    const surfaceBefore = q('[data-testid="rx-tx-surface"]')!;
    const markupBefore = target.innerHTML;
    expect(zonesBefore).toHaveLength(4);

    rotateToPortraitPhone();

    const zonesAfter = qa('[data-zone-id]');
    expect(zonesAfter).toHaveLength(zonesBefore.length);
    zonesAfter.forEach((el, i) => expect(el).toBe(zonesBefore[i]));
    expect(q('[data-testid="rx-tx-key"]')).toBe(keyBefore);
    expect(q('[data-testid="rx-tx-surface"]')).toBe(surfaceBefore);
    expect(target.innerHTML).toBe(markupBefore);
  });

  // The TX half, driven from a LIVE lease rather than from idle — idle has no
  // guard, so a remount's fail-closed `onDestroy` release is a no-op and the
  // interesting failure hides. Keyed, the same remount drops the operator's
  // transmission on rotation, or re-keys under a fresh `sourceId` that the
  // controller will refuse to release from. This is the ticket's "portrait/
  // landscape replacement preserves TX/resource ownership", stated where it
  // can actually fail: rotating while keyed is not a lease event.
  it('rotating while KEYED neither releases nor re-acquires the lease', () => {
    h.state = mainSubState('MAIN');
    h.caps = mainSubCaps();
    render();

    q<HTMLButtonElement>('[data-testid="rx-tx-key"]')!.click();
    flushSync();
    expect(h.start).toHaveBeenCalledTimes(1);
    push({ phase: 'transmitting', intent: 'latched', guard: { leaseId: 'L1' }, mayOwnKey: true });
    h.start.mockReset();
    h.release.mockReset();

    rotateToPortraitPhone();

    expect(h.start).not.toHaveBeenCalled();
    expect(h.release).not.toHaveBeenCalled();
    // ...and the only way out of transmit is still there, exactly once.
    expect(qa('[data-testid="rx-tx-surface"]')).toHaveLength(1);
    expect(qa('[data-testid="rx-tx-unkey"]')).toHaveLength(1);
  });
});

describe('MOR-1069 — focus order is DOM order, and the LAST declared zone comes last', () => {
  // "Keyboard and touch order remain logical." The CSS side (no `order`, no
  // reversed flow) is pinned textually; this is the DOM side that the CSS
  // must keep agreeing with. Kills: emitting the radio-wide row or the RX/TX
  // zone before the strips, or interleaving the strips' controls — either
  // would make the tab sequence disagree with the reading order in BOTH
  // arrangements at once.
  //
  // MOR-1336 (S4) flips the premise this test pinned: `tx-aux` is now the
  // LAST declared zone (after `rx-tx`), and its controls are real, focusable
  // members of it once a resolved plan is supplied (see `defaultPlan()` /
  // the F6 comment above) — both fixtures carry `tuner`, so the surface
  // mounts. The tab sequence must still never go backwards, and now ends in
  // `tx-aux`, not `rx-tx`: the key/unkey control sits SECOND-TO-LAST, ahead
  // of the txAux row, which is exactly where the manifest places the zone.
  it('every control appears in declared zone order, ending in tx-aux', () => {
    h.state = mainSubState('MAIN');
    h.caps = mainSubCaps();
    render(defaultPlan());

    const declared = dualReceiverCockpitLayout.zones.map((z) => z.id);
    const sequence = qa<HTMLElement>('button, input, select, a[href], [tabindex]')
      .map((el) => el.closest('[data-zone-id]') as HTMLElement | null)
      .map((zone) => declared.indexOf(zone?.dataset.zoneId ?? ''));

    expect(sequence.length).toBeGreaterThan(0);
    // No control sits outside a declared zone...
    expect(sequence).not.toContain(-1);
    // ...and the sequence never goes backwards through the declared order.
    expect(sequence).toEqual([...sequence].sort((a, b) => a - b));
    // MOR-1336: tx-aux is now the last declared zone, and its controls are
    // the last thing in the tab sequence — the key/unkey control precedes it.
    expect(sequence.at(-1)).toBe(declared.indexOf('tx-aux'));
    expect(declared.indexOf('tx-aux')).toBeGreaterThan(declared.indexOf('rx-tx'));
  });

  // MOR-1258. `tx-fault-reset` and the two ModInputTxWarning buttons only
  // exist in the DOM while their own conditional trigger is active (a
  // latched TX fault; a visible MOD-input guard). The base-case test above
  // never enters either state, so before MOR-1258 these three controls could
  // sit anywhere at all — including outside every declared zone — without
  // this assertion ever seeing them (the MOR-1069 verification finding this
  // ticket exists to close). Driving each conditional state in turn is what
  // makes the assertion actually SEE them.
  //
  // MOR-1336: these three alerts are rx-tx zone members (R6, pinned formally
  // below), not tx-aux members, so the sequence still ends in tx-aux even
  // while they are present — they land just BEFORE it, never after.
  it.each([
    ['a TX fault is latched', { phase: 'failed', fault: 'audio-failed' } as Partial<Snapshot>, false],
    ['the MOD-input guard is visible', {} as Partial<Snapshot>, true],
    ['both conditional alerts are active at once', { phase: 'failed', fault: 'audio-failed' } as Partial<Snapshot>, true],
  ] as const)('still ends in tx-aux with no control outside a declared zone — %s', (
    _label, snapshotOver, modInputVisible,
  ) => {
    h.state = mainSubState('MAIN');
    h.caps = mainSubCaps();
    h.modInputGuard = modInputVisible
      ? { visible: true, sourceLabel: 'MIC' }
      : { visible: false, sourceLabel: null };
    render(defaultPlan());
    push(snapshotOver);

    // Sanity: the conditional control(s) this case drives are actually present
    // — otherwise the assertions below would pass vacuously.
    if ('phase' in snapshotOver) expect(q('[data-testid="tx-fault-reset"]')).not.toBeNull();
    if (modInputVisible) expect(q('[data-testid="mod-input-tx-warning"]')).not.toBeNull();

    const declared = dualReceiverCockpitLayout.zones.map((z) => z.id);
    const sequence = qa<HTMLElement>('button, input, select, a[href], [tabindex]')
      .map((el) => el.closest('[data-zone-id]') as HTMLElement | null)
      .map((zone) => declared.indexOf(zone?.dataset.zoneId ?? ''));

    expect(sequence.length).toBeGreaterThan(0);
    expect(sequence).not.toContain(-1);
    expect(sequence).toEqual([...sequence].sort((a, b) => a - b));
    expect(sequence.at(-1)).toBe(declared.indexOf('tx-aux'));
  });
});

describe('MOR-1069 (N1) — rx-tx is a real bound zone element in the cockpit', () => {
  // The MOR-1068 verification accepted an inert `display: contents` wrapper on
  // every path. It cannot stay inert here: a `display: contents` box takes no
  // part in its parent's layout, so the zone could not be placed by the
  // responsive rules at all. It is now a real element, rendered only in this
  // composition. Kills: reverting the wrapper to a phantom, or losing the
  // zone binding while collapsing it.
  it('wraps the shared TX surface in a placeable element carrying the zone id', () => {
    h.state = mainSubState('MAIN');
    h.caps = mainSubCaps();
    render();

    const zone = q('[data-zone-id="rx-tx"]')!;
    expect(zone).not.toBeNull();
    expect(zone.tagName).toBe('DIV');
    expect(zone.classList.contains('rx-tx-zone')).toBe(true);
    // The zone IS the surface's box, not a sibling marker next to it.
    expect(q('[data-testid="rx-tx-surface"]')!.parentElement).toBe(zone);
    expect(zone.querySelectorAll('[data-testid="rx-tx-key"]')).toHaveLength(1);
  });
});

// MOR-1258 (owner decision, 2026-08-04, gate item (b)): `tx-fault-reset` and
// the two ModInputTxWarning buttons are formal rx-tx zone members. Each
// assertion is a direct containment check against the zone ELEMENT itself
// (`.contains` / `.closest`), independent of the focus-order sequence above —
// a mutant that re-homes any one of the three as a sibling of `.rx-tx-zone`
// fails here even if it still happened to land last in tab order.
describe('MOR-1258 — the three TX-adjacent alerts are formal rx-tx zone members', () => {
  it('contains tx-fault-reset inside the rx-tx zone while a fault is latched, and nowhere before it', () => {
    h.state = mainSubState('MAIN');
    h.caps = mainSubCaps();
    render();
    expect(q('[data-testid="tx-fault-reset"]')).toBeNull();

    push({ phase: 'failed', fault: 'audio-failed' });

    const zone = q('[data-zone-id="rx-tx"]')!;
    const reset = q('[data-testid="tx-fault-reset"]')!;
    expect(zone.contains(reset)).toBe(true);
    expect(reset.closest('[data-zone-id]')).toBe(zone);
  });

  it('contains both ModInputTxWarning buttons inside the rx-tx zone while the guard is visible', () => {
    h.state = mainSubState('MAIN');
    h.caps = mainSubCaps();
    h.modInputGuard = { visible: true, sourceLabel: 'MIC' };
    render();

    const zone = q('[data-zone-id="rx-tx"]')!;
    const setLan = q('[data-testid="mod-input-set-lan"]')!;
    const dismiss = q('[data-testid="mod-input-dismiss"]')!;
    expect(zone.contains(setLan)).toBe(true);
    expect(zone.contains(dismiss)).toBe(true);
    expect(setLan.closest('[data-zone-id]')).toBe(zone);
    expect(dismiss.closest('[data-zone-id]')).toBe(zone);
  });

  it('contains all three alerts inside the SAME zone box that owns RxTxSurface, active simultaneously', () => {
    h.state = mainSubState('MAIN');
    h.caps = mainSubCaps();
    h.modInputGuard = { visible: true, sourceLabel: 'MIC' };
    render();
    push({ phase: 'failed', fault: 'audio-failed' });

    const zone = q('[data-zone-id="rx-tx"]')!;
    expect(q('[data-testid="rx-tx-surface"]')!.parentElement).toBe(zone);
    for (const testid of ['tx-fault-reset', 'mod-input-set-lan', 'mod-input-dismiss']) {
      const el = q(`[data-testid="${testid}"]`);
      expect(el).not.toBeNull();
      expect(zone.contains(el)).toBe(true);
    }
  });

  // The alert's own behavior is untouched by the containment move: the
  // fault-reset handler still fires from its new DOM parent exactly as it
  // did from the old one. (The ModInputTxWarning buttons' own handler
  // wiring is unchanged — pinned in
  // `components-v2/wiring/__tests__/semantic-rx-tx-wiring.component.test.ts`
  // and `components-v2/panels/__tests__/ModInputTxWarning.isolated.test.ts` — moving
  // its DOM parent does not touch the component's own click handlers.)
  it('still reaches the fault-reset handler from inside the zone', () => {
    h.state = mainSubState('MAIN');
    h.caps = mainSubCaps();
    render();
    push({ phase: 'failed', fault: 'audio-failed' });

    q<HTMLButtonElement>('[data-testid="tx-fault-reset"]')!.click();
    flushSync();
    expect(h.resetFault).toHaveBeenCalledTimes(1);
  });

  // MOR-1336 (S4) restated (R6): the four tests above never supply a resolved
  // plan, so the new tx-aux zone never actually mounts alongside them — R6
  // holds, but only because there was nowhere for the alerts to have moved
  // TO. Restated with `defaultPlan()`, so the tx-aux zone is a real sibling
  // element and containment inside rx-tx — never tx-aux — is proven against
  // it, not merely against its absence.
  it('keeps all three TX-adjacent alerts inside rx-tx, never tx-aux, once the tx-aux zone actually mounts', () => {
    h.state = mainSubState('MAIN');
    h.caps = mainSubCaps();
    h.modInputGuard = { visible: true, sourceLabel: 'MIC' };
    render(defaultPlan());
    push({ phase: 'failed', fault: 'audio-failed' });

    const rxTxZone = q('[data-zone-id="rx-tx"]')!;
    const txAuxZone = q('[data-zone-id="tx-aux"]');
    expect(txAuxZone).not.toBeNull(); // sanity: the zone this pin restates for actually exists
    for (const testid of ['tx-fault-reset', 'mod-input-set-lan', 'mod-input-dismiss']) {
      const el = q(`[data-testid="${testid}"]`);
      expect(el).not.toBeNull();
      expect(rxTxZone.contains(el)).toBe(true);
      expect(txAuxZone!.contains(el)).toBe(false);
    }
  });
});

describe('MOR-1069 — the shell\'s responsive selectors still match the composed tree', () => {
  // The cockpit owns its responsive rules but the boxes they place are
  // composed by the SHARED wiring, so the rules reach in with `:global(...)`.
  // That coupling is one rename away from silently doing nothing — a CSS
  // selector that matches no element is not an error anywhere. Kills: a class
  // rename (or a restructure) in SemanticRadioSurfaces that leaves the
  // cockpit's stacking rules pointing at nothing.
  it('every :global class named by the cockpit CSS exists in the mounted DOM', () => {
    h.state = mainSubState('MAIN');
    h.caps = mainSubCaps();
    render();

    const source = readFileSync('src/skins/dual-receiver-cockpit/DualReceiverCockpit.svelte', 'utf8');
    const classes = [...source.matchAll(/:global\(\.([a-z0-9-]+)\)/g)].map((m) => m[1]);
    expect(classes.length).toBeGreaterThan(0);
    for (const className of new Set(classes)) {
      expect(qa(`.${className}`).length).toBeGreaterThan(0);
    }
  });
});

// MOR-1257 (N4) — MOR-1070 evidence run finding: a standalone mount of this
// shell never pulled in the code-split components-v2 theme layer
// (fonts/tokens/themes), so app.css's
// `outline: var(--v2-focus-ring, var(--focus-ring))` silently fell back to
// the legacy ring instead of the MOR-1232 --v2-focus-ring pair (pinned in
// src/__tests__/focus-ring-token-wiring.test.ts). RadioLayout.svelte and
// LcdLayout.svelte already import the theme layer as a side effect; this
// shell had no equivalent import.
describe('MOR-1257 (N4) — the components-v2 theme layer loads with this skin', () => {
  // Source-based, mirroring this file's own F5 check above (":global class
  // exists in the mounted DOM") rather than a DOM computed-style assertion —
  // vitest/jsdom does not reliably apply real CSS cascade from side-effect
  // `.css` imports, which is why MOR-1232's own wiring pins
  // (focus-ring-token-wiring.test.ts) read source text too.
  // Kills: dropping the import, or narrowing it to skip tokens.css.
  it('imports the v2 theme layer as a side effect, the same way RadioLayout/LcdLayout do', () => {
    const source = readFileSync('src/skins/dual-receiver-cockpit/DualReceiverCockpit.svelte', 'utf8');
    expect(source).toMatch(/import\s+['"]\.\.\/\.\.\/components-v2\/theme\/index['"]\s*;/);
  });
});

function push(next: Partial<Snapshot>): void {
  h.snapshot = { ...(h.snapshot as Snapshot), ...next };
  for (const listener of h.listeners) listener(h.snapshot);
  flushSync();
}
