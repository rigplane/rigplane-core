/**
 * MOR-1094 — the mobile presentation shell migrated to App-owned behavior and
 * a v1 layout manifest, mirroring the MOR-1092 LCD slice.
 *
 * SAFETY-ADJACENT. The mobile shell carries the operator's press-and-hold PTT,
 * and this slice adds a SECOND TX source to it (the semantic RX/TX surface,
 * which keys LATCHED). So four things must hold at once:
 *   1. the semantic VFO / RX-TX surfaces are mounted in the portrait deck via
 *      the unchanged `SemanticRadioSurfaces` wiring — no new TX code path;
 *   2. the press-and-hold path is still the MOR-1011/1012 gesture recognizer
 *      feeding the App TX controller under its own per-surface `sourceId`,
 *      byte-identical to before — pinned by identity, not by shape;
 *   3. the two sources cannot disturb each other: the semantic surface's
 *      fail-closed teardown release (which fires on every rotation, because
 *      the portrait deck is destroyed) must be INERT against a lease the PTT
 *      gesture owns, and vice versa;
 *   4. the App-global Toast / power overlay / TX lamp stay singular and global
 *      (MOR-1059), and the skin wrapper owns no resolution or runtime.
 *
 * The controller behind `getAppTxController` is the REAL `TxController` over
 * stub dependencies, not a double: claim 3 is a property of the production
 * state machine's owner check, and a recording spy would pass it vacuously.
 *
 * Each test's doc line names the mutation it exists to kill.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { mount, unmount, flushSync } from 'svelte';
import { TxController } from '$lib/runtime/tx-controller/controller';
import type { TxControllerDependencies } from '$lib/runtime/tx-controller/controller';

// -- Child components the shell mounts that are irrelevant here -------------
vi.mock('../../../components/spectrum/SpectrumPanel.svelte', async () => {
  const stub = await import('./SpectrumPanelStub.svelte');
  return { default: stub.default };
});
vi.mock('../panels/lcd/AmberLcdDisplay.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../display/FrequencyDisplay.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../meters/LinearSMeter.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../controls/CollapsiblePanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../controls/BottomSheet.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../controls/BandSelector.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/FilterPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/RxAudioPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/TxPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/DspPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/AgcPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/RfFrontEnd.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/RitXitPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/AntennaPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/ScanPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/CwPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/DockMeterPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/EssentialsPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('./KeyboardHandler.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('$lib/Button', () => ({ HardwareButton: function S() { return {}; } }));
vi.mock('lucide-svelte', () => {
  const S = function () { return {}; };
  return { Settings: S, ChevronLeft: S, ChevronRight: S, ChevronsLeft: S, ChevronsRight: S, Mic: S, MicOff: S, Sliders: S, Radio: S };
});
vi.mock('../controls/value-control', () => ({
  ValueControl: function S() { return {}; },
  normalizedPercentDisplay: (v: number) => `${Math.round(v * 100)}%`,
}));
vi.mock('./vfo-layout-tokens', () => ({
  resolveVfoLayoutProfile: vi.fn(() => 'standard'),
  vfoLayoutStyleVars: vi.fn(() => ''),
}));

// The MOR-617 banner's adapter reads persisted state; keep this suite hermetic
// (and free of the localStorage-shaped environment noise) by stubbing it flat.
vi.mock('$lib/runtime/adapters/mod-input-tx-guard.svelte', () => ({
  deriveModInputTxGuardProps: vi.fn(() => ({ visible: false, sourceLabel: null })),
  getModInputTxGuardHandlers: vi.fn(() => ({ onSetLan: vi.fn(), onDismiss: vi.fn() })),
}));

// A real, fully-populated view model so the semantic surfaces actually RENDER.
// Without it `SemanticRadioSurfaces` short-circuits on `{#if view}` and every
// assertion below about the surfaces would pass for the wrong reason.
vi.mock('$lib/runtime/adapters/radio-view-model-adapter', async () => {
  const { topologyFixtures } = await import('../../../semantic/fixtures/topologies');
  // `1/single` matches the mocked capabilities below (no dual receiver) and is
  // the fixture whose TX target is observed and whose permit is allowed — so
  // the RX/TX surface's key action is reachable rather than blocked.
  return { toRadioViewModel: vi.fn(() => topologyFixtures['1/single']) };
});

// -- Store mocks (kept in step with MobileRadioLayout.component.svelte.test.ts) --
vi.mock('$lib/stores/radio.svelte', () => ({
  radio: { current: null as { active?: 'MAIN' | 'SUB' } | null },
  getActiveReceiver: vi.fn(), getRadioState: vi.fn(), patchActiveReceiver: vi.fn(),
  patchRadioState: vi.fn(), patchReceiver: vi.fn(),
}));
vi.mock('$lib/stores/connection.svelte', () => ({
  getConnectionStatus: vi.fn(() => ({ connected: false })),
  getRadioPowerOn: vi.fn(() => null),
  // MOR-1279 slice 3B: the RX-audio snapshot reports audio-WS link health.
  isAudioConnected: vi.fn(() => false),
}));
vi.mock('$lib/stores/audio.svelte', () => ({
  getAudioState: vi.fn(() => ({ volume: 50, muted: false, rxEnabled: false, txEnabled: false, micEnabled: false, bridgeRunning: false })),
}));
vi.mock('$lib/audio/audio-manager', () => ({
  audioManager: { start: vi.fn(), stop: vi.fn(), setVolume: vi.fn(), toggleMute: vi.fn() },
}));
vi.mock('$lib/utils/tx-permit', () => ({ getTxPermit: vi.fn(() => 'allowed') }));
vi.mock('$lib/stores/tuning.svelte', () => ({ applyModeDefault: vi.fn() }));
vi.mock('$lib/stores/capabilities.svelte', () => ({
  hasTx: vi.fn(() => true), hasDualReceiver: vi.fn(() => false), hasAnyScope: vi.fn(() => false),
  hasSpectrum: vi.fn(() => true), getCapabilities: vi.fn(() => ({ freqRanges: [], modes: [], filters: [] })),
  getKeyboardConfig: vi.fn(() => null), setCapabilities: vi.fn(), hasCapability: vi.fn(() => false),
  vfoLabel: vi.fn((s: string) => s === 'A' ? 'MAIN' : 'SUB'),
  receiverLabel: vi.fn((id: 'MAIN' | 'SUB') => id), isAudioFftScope: vi.fn(() => false),
  hasAudioFft: vi.fn(() => false), getScopeSource: vi.fn(() => null), hasAudio: vi.fn(() => false),
  getSmeterCalibration: vi.fn(() => null), getSmeterRedline: vi.fn(() => null),
  getMeterCalibration: vi.fn(() => null), getMeterRedline: vi.fn(() => null),
  getControlRange: vi.fn(() => ({ min: 0, max: 255 })),
  getSupportedModes: vi.fn(() => ['USB', 'LSB', 'CW', 'AM', 'FM']),
  getSupportedFilters: vi.fn(() => ['FIL1', 'FIL2', 'FIL3']),
  getAttValues: vi.fn(() => [0, 10, 20]), getAttLabels: vi.fn(() => ({ 0: '0dB', 10: '10dB', 20: '20dB' })),
  getPreValues: vi.fn(() => [0, 1, 2]), getPreLabels: vi.fn(() => ({ 0: 'OFF', 1: 'PRE1', 2: 'PRE2' })),
  getAgcModes: vi.fn(() => [0, 1, 2, 3]),
  getAgcLabels: vi.fn(() => ({ 0: 'OFF', 1: 'FAST', 2: 'MID', 3: 'SLOW' })),
  getVfoScheme: vi.fn(() => 'ab'), getAntennaCount: vi.fn(() => 1),
}));
vi.mock('../../wiring/command-bus', () => {
  const n = vi.fn();
  return {
    makeVfoHandlers: () => ({
      onMainFreqChange: n, onSubFreqChange: n, onVfoSwap: n, onVfoEqual: n, onReceiverSelect: n,
      onMainVfoClick: n, onSubVfoClick: n, onSplitToggle: n, onSwap: n, onEqual: n,
      onVfoSelect: n, onDualWatchToggle: n,
    }),
    // MOR-1265 — the semantic wiring now also composes the txAux intents.
    makeVoxHandlers: () => ({
      onVoxToggle: n, onVoxGainChange: n, onAntiVoxGainChange: n, onVoxDelayChange: n,
    }),
    makeMeterHandlers: () => ({ onMeterSourceChange: n }), makeKeyboardHandlers: () => ({ dispatch: n }),
    // MOR-1279 slice 3B: the semantic RX-audio surface's routing intents and
    // its one-click MOD-input LAN remedy.
    makeModeHandlers: () => ({ onModeChange: n, onDataModeChange: n, onModInputChange: n }),
    makeAudioRoutingHandlers: () => ({ onFocusChange: n, onSplitStereoChange: n }),
    makeFilterHandlers: () => ({ onFilterChange: n, onFilterWidthChange: n }),
    makeBandHandlers: () => ({ onBandSelect: n }), makePresetHandlers: () => ({ onPresetSelect: n }),
    makeRxAudioHandlers: () => ({ onAfLevelChange: n, onMonitorModeChange: n }),
    makeTxHandlers: () => ({ onPttChange: n, onPowerChange: n, onTuneStart: n, onAtuToggle: n, onRfPowerChange: n, onMicGainChange: n, onAtuTune: n, onVoxToggle: n, onCompToggle: n, onCompLevelChange: n, onMonToggle: n, onMonLevelChange: n, onDriveGainChange: n }),
    makeRfFrontEndHandlers: () => ({ onAttChange: n, onPreChange: n, onRfGainChange: n }),
    makeAgcHandlers: () => ({ onAgcModeChange: n }),
    makeRitXitHandlers: () => ({ onRitToggle: n, onRitClear: n, onXitToggle: n, onXitClear: n }),
    makeDspHandlers: () => ({ onNrToggle: n, onNbToggle: n, onNotchToggle: n, onNrModeChange: n, onNotchModeChange: n }),
    // MOR-1310 slice 9B added the semantic CW surface's setting intents here.
    makeCwPanelHandlers: () => ({
      onSpeedChange: n, onKeySpeedChange: n, onCwPitchChange: n,
      onBreakInDelayChange: n, onBreakInModeChange: n, onApfChange: n,
      onTwinPeakToggle: n, onReversePaddleToggle: n,
    }),
    makeAntennaHandlers: () => ({ onAntennaSelect: n }),
    makeScanHandlers: () => ({ onScanStart: n, onScanStop: n, onDfSpanChange: n, onResumeChange: n }),
    // MOR-1311 slice 11B: the scope-toolbar/popover intent vocabulary.
    makeScopeControlsHandlers: () => ({
      onModeChange: n, onEdgeChange: n, onSpanChange: n, onSpeedChange: n, onHoldChange: n,
      onRefChange: n, onDualChange: n, onReceiverChange: n, onDuringTxChange: n,
      onCenterTypeChange: n, onVbwChange: n, onRbwChange: n,
    }),
  };
});
vi.mock('../../wiring/state-adapter', () => {
  const vfo = { freq: 14074000, mode: 'USB', filter: 'FIL1', sValue: 0, badges: {}, receiver: 'main', isActive: true };
  return {
    toVfoProps: vi.fn(() => vfo), toVfoOpsProps: vi.fn(() => ({ split: false, dualWatch: false })),
    toMeterProps: vi.fn(() => ({ signal: 0, rfPower: 0, swr: 0, alc: 0, txActive: false, meterSource: 'S' })),
    toModeProps: vi.fn(() => ({ currentMode: 'USB', modes: ['USB', 'LSB', 'CW', 'AM', 'FM'], dataMode: 0 })),
    toFilterProps: vi.fn(() => ({ currentFilter: 1, filterLabels: ['FIL1', 'FIL2', 'FIL3'] })),
    toBandSelectorProps: vi.fn(() => ({ currentFreq: 14074000 })),
    toRxAudioProps: vi.fn(() => ({ afLevel: 0.5, monitorMode: 'local' })),
    toTxProps: vi.fn(() => ({ rfPower: 0.5, txActive: false, atuActive: false, atuTuning: false })),
    toRfFrontEndProps: vi.fn(() => ({ att: 0, preamp: 0, rfGain: 1 })),
    toAgcProps: vi.fn(() => ({ agcMode: 3 })),
    toRitXitProps: vi.fn(() => ({ ritOn: false, ritOffset: 0, xitOn: false, xitOffset: 0 })),
    toDspProps: vi.fn(() => ({ nr: false, nb: false, notch: false })), toCwProps: vi.fn(() => ({ speed: 20 })),
    toAntennaProps: vi.fn(() => ({ selected: 1 })),
    toScanProps: vi.fn(() => ({ scanning: false, scanType: 'off', scanResumeMode: 'time' })),
  };
});

const txHost = vi.hoisted(() => ({ current: undefined as unknown as TxHostFacade }));
vi.mock('$lib/runtime/tx-controller/app-host', () => ({
  getAppTxController: () => txHost.current,
}));

import MobileRadioLayout from '../MobileRadioLayout.svelte';
import mobileLayoutSource from '../MobileRadioLayout.svelte?raw';
import mobileSkinSource from '../../../skins/mobile/MobileSkin.svelte?raw';
import { hasTx } from '$lib/stores/capabilities.svelte';

// ---------------------------------------------------------------------------
// Real-controller harness
//
// Copied from MobileRadioLayout.component.svelte.test.ts (itself copied from
// TxPanel.isolated.test.ts). Duplicated rather than shared because the suites live in
// different pools and a shared helper module would pin the controller in the
// ``isolate: false`` cache for siblings that mock it — see vite.config.ts /
// #771. Keep the copies in step.
// ---------------------------------------------------------------------------
type TxEvent = Parameters<TxController['dispatch']>[0];
type StartEvent = Extract<TxEvent, { type: 'start' }>;
type Eligibility = StartEvent['eligibility'];
type Observation = StartEvent['ptt'];
type Intent = StartEvent['intent'];
type Guard = Extract<TxEvent, { type: 'intent' }>['guard'];
type Command = Parameters<TxControllerDependencies['sendPtt']>[0];
type Report = Parameters<TxControllerDependencies['sendPtt']>[3];
type TxHostFacade = ReturnType<typeof createTxHarness>['facade'];

const marker = (seq: number) => ({
  authorityEpoch: 1, pttObservationSeq: seq, pttLastObservedMonotonic: seq,
});
const allowed: Eligibility = {
  catPtt: true, browserTxAudio: true, controlLive: true, permit: 'allowed',
  target: { receiver: 'MAIN', slot: 'A', frequencyHz: 14_074_000 },
};
const observe = (value: boolean, seq: number): Observation =>
  ({ value, observed: true, fresh: true, source: 'radio-readback', marker: marker(seq) });

function deepFreeze<T>(value: T): T {
  if (value === null || typeof value !== 'object') return value;
  const copy = Object.fromEntries(Object.entries(value as Record<string, unknown>)
    .map(([key, child]) => [key, deepFreeze(child)]));
  return Object.freeze(copy) as unknown as T;
}

function createTxHarness() {
  const sends: Array<{ command: Command; report: Report }> = [];
  /** Every `start` the App controller is asked for, with its source identity. */
  const starts: Array<{ sourceId: string; leaseId: string; intent: Intent }> = [];
  /** Every `release` request, whether or not the model honours it. */
  const releases: Array<{ sourceId: string }> = [];
  const audio: { next: Promise<string | null> } = { next: Promise.resolve(null) };
  const eligibility = { current: allowed };
  let id = 0;
  let seq = 0;
  const dependencies: TxControllerDependencies = {
    startAudio: vi.fn(() => audio.next),
    sendPtt: vi.fn((command, _commandId, _correlation, report) => { sends.push({ command, report }); }),
    stopLocalAudio: vi.fn(),
    restoreMod: vi.fn(),
    commandId: vi.fn((command) => `${command}-${++id}`),
    schedule: vi.fn((callback, delay) => setTimeout(callback, delay)),
    cancel: vi.fn((handle) => clearTimeout(handle as ReturnType<typeof setTimeout>)),
    timeoutMs: { 'audio-start': 60_000, 'on-confirmation': 60_000, 'off-confirmation': 60_000 },
  };
  const controller = new TxController(1, marker(0), dependencies);
  const facade = {
    snapshot: () => deepFreeze(controller.snapshot()),
    subscribe: (listener: (state: unknown) => void) =>
      controller.subscribe((state) => listener(deepFreeze(state))),
    start: (sourceId: string, leaseId: string, intent: Intent) => {
      starts.push({ sourceId, leaseId, intent });
      return controller.dispatch({
        type: 'start', sourceId, leaseId, intent,
        eligibility: eligibility.current, ptt: observe(false, ++seq),
      });
    },
    setIntent: (sourceId: string, guard: Guard, intent: Intent) =>
      controller.dispatch({ type: 'intent', sourceId, guard: { ...guard }, intent }),
    release: (sourceId: string, guard: Guard) => {
      releases.push({ sourceId });
      return controller.dispatch({
        type: 'release', sourceId, guard: { ...guard }, commandId: dependencies.commandId('off'),
      });
    },
    resetFault: () => controller.dispatch({ type: 'reset-fault' }),
  };
  return {
    controller, dependencies, sends, starts, releases, facade, audio, eligibility,
    /** Feed an authoritative PTT readback (what the App host does on session
     *  updates). Without one the controller reports `radioTx: 'unknown'` and
     *  the semantic surface correctly refuses to offer its key action. */
    authority: (value: boolean) => controller.dispatch({
      type: 'authority', epoch: 1, ptt: observe(value, ++seq),
      eligibility: eligibility.current, offCommandId: dependencies.commandId('off'),
    }),
    confirm: (command: Command) => {
      const send = [...sends].reverse().find((item) => item.command === command);
      expect(send).toBeDefined();
      send!.report({ outcome: 'sent', eventEpoch: 1, barrier: marker(++seq) });
    },
  };
}

let tx: ReturnType<typeof createTxHarness>;
let components: ReturnType<typeof mount>[] = [];

const HOLD_MS = 50;
const GESTURE_WINDOW_MS = 300;

function setViewport(landscape: boolean) {
  Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: landscape ? 844 : 390 });
  Object.defineProperty(window, 'innerHeight', { writable: true, configurable: true, value: landscape ? 390 : 844 });
}

function mountMobile(): HTMLElement {
  const target = document.createElement('div');
  document.body.appendChild(target);
  components.push(mount(MobileRadioLayout, { target }));
  flushSync();
  return target;
}

function rotate(landscape: boolean) {
  setViewport(landscape);
  window.dispatchEvent(new Event('resize'));
  flushSync();
}

function pointer(el: Element, type: string, init: PointerEventInit = {}) {
  el.dispatchEvent(new PointerEvent(type, { bubbles: true, clientX: 0, clientY: 0, pointerId: 1, ...init }));
  flushSync();
}

const fabEl = (t: HTMLElement) => t.querySelector<HTMLButtonElement>('.ptt-fab')!;
const flushAudio = async () => { await Promise.resolve(); await Promise.resolve(); flushSync(); };

/** A completed FAB press: PttFab only calls onDown() past its 50 ms guard. */
function fabPress(t: HTMLElement) {
  pointer(fabEl(t), 'pointerdown');
  vi.advanceTimersByTime(HOLD_MS);
  flushSync();
}

/** Press and hold the FAB until the radio has acknowledged the ON command. */
async function hold(t: HTMLElement) {
  fabPress(t);
  await flushAudio();
  tx.confirm('on');
  flushSync();
}

/** Count of `off` commands the controller has actually emitted. */
const offs = () => tx.sends.filter((s) => s.command === 'off').length;

/**
 * Key through the semantic RX/TX surface. An authoritative readback comes
 * first because the surface refuses its own key action while the RF state is
 * unobserved (`rf-state-unknown`) — asserting the button is live keeps a
 * silently-disabled button from making these tests pass vacuously.
 */
function semanticKey(t: HTMLElement) {
  tx.authority(false);
  flushSync();
  const key = t.querySelector<HTMLButtonElement>('[data-testid="rx-tx-key"]')!;
  expect(key.disabled).toBe(false);
  key.click();
  flushSync();
}

beforeEach(() => {
  vi.useFakeTimers();
  components = [];
  setViewport(false);
  tx = createTxHarness();
  txHost.current = tx.facade;
  vi.mocked(hasTx).mockReturnValue(true);
});

afterEach(() => {
  components.forEach((c) => {
    try { unmount(c); } catch { /* already unmounted by a test */ }
  });
  document.body.innerHTML = '';
  vi.useRealTimers();
});

// ---------------------------------------------------------------------------
// 1. Semantic surface adoption
// ---------------------------------------------------------------------------
describe('semantic VFO / RX-TX adoption in the mobile shell', () => {
  // Kills: the migration never landing — the shell keeping only its legacy
  // header facts and the FAB as its sole TX truth.
  it('mounts the semantic surfaces in the portrait deck', () => {
    const t = mountMobile();
    expect(t.querySelectorAll('[data-testid="semantic-radio-surfaces"]')).toHaveLength(1);
    expect(t.querySelectorAll('[data-testid="rx-tx-surface"]')).toHaveLength(1);
  });

  // Kills: mounting the surfaces inside a chip panel. Chip panels are
  // destroyed and recreated on every chip tap, and this subtree holds a TX
  // lease source — churning it would churn a TX identity (MOR-1086 doctrine).
  it('mounts them in the scrollable deck, not inside a chip panel', () => {
    const t = mountMobile();
    const surfaces = t.querySelector('[data-testid="semantic-radio-surfaces"]')!;
    expect(t.querySelector('.m-content')!.contains(surfaces)).toBe(true);
    expect(surfaces.closest('.m-section')).toBeNull();
  });

  // Kills: adding a second copy of the wiring (one per orientation, or one
  // per chip). Exactly one instance may exist — each is a distinct TX source.
  it('never mounts a second copy, in either orientation', () => {
    const t = mountMobile();
    rotate(true);
    expect(t.querySelectorAll('[data-testid="semantic-radio-surfaces"]')).toHaveLength(0);
    rotate(false);
    expect(t.querySelectorAll('[data-testid="semantic-radio-surfaces"]')).toHaveLength(1);
  });

  // Kills: hand-rolling a mobile-local copy of the surfaces instead of reusing
  // the shared wiring the LCD and desktop slices mount (MOR-1065).
  it('reuses the shared SemanticRadioSurfaces wiring, unchanged', () => {
    expect(mobileLayoutSource).toContain('SemanticRadioSurfaces');
    expect(mobileLayoutSource).toContain("from '../wiring/SemanticRadioSurfaces.svelte'");
    // No mobile-local RX/TX or VFO surface reimplementation.
    expect(mobileLayoutSource).not.toContain('RxTxSurface');
    expect(mobileLayoutSource).not.toContain('VfoSurface');
  });
});

// ---------------------------------------------------------------------------
// 2. The press-and-hold PTT path is UNCHANGED — identity, not shape
// ---------------------------------------------------------------------------
describe('mobile press-and-hold PTT identity (MOR-1011/1012, unchanged)', () => {
  // THE pin. Kills: rewiring the FAB to anything other than the per-surface
  // gesture recognizer — to the semantic surface's latched `requestKey`, to a
  // shared sourceId, to a raw command, or to a re-derived id. All of those
  // change the (sourceId, intent) pair the App controller is asked to start.
  it('keys through the gesture recognizer under its own momentary mobile source', async () => {
    const t = mountMobile();
    await hold(t);

    expect(tx.starts).toHaveLength(1);
    expect(tx.starts[0].sourceId).toMatch(/^mobile-ptt-portrait-\d+$/);
    expect(tx.starts[0].intent).toBe('momentary');
    // The lease id is derived from that same source identity, per-lease.
    expect(tx.starts[0].leaseId).toBe(`${tx.starts[0].sourceId}-1`);
    // The App controller — not a local machine — owns the resulting key.
    expect(tx.controller.snapshot().sourceId).toBe(tx.starts[0].sourceId);
    expect(tx.controller.snapshot().phase).toBe('key-confirm-pending');
  });

  // Kills: the semantic surface silently becoming the press-and-hold owner.
  // It keys LATCHED under its own id; if the FAB were rewired through it, the
  // press above would produce a `semantic-rx-tx-*` latched start instead.
  it('gives the semantic RX/TX key a different source and a different intent', () => {
    const t = mountMobile();
    semanticKey(t);

    expect(tx.starts).toHaveLength(1);
    expect(tx.starts[0].sourceId).toMatch(/^semantic-rx-tx-\d+$/);
    expect(tx.starts[0].intent).toBe('latched');
    expect(tx.starts[0].sourceId).not.toMatch(/^mobile-ptt/);
  });

  // Kills: reintroducing a local TX machine, raw PTT commands or bespoke
  // timers alongside the App controller (the MOR-1012 acceptance evidence,
  // re-asserted here because this slice edits the same file).
  it('still routes every mobile TX path through the App controller alone', () => {
    expect(mobileLayoutSource).toContain('getAppTxController');
    // MOR-1378: the recognizer wiring lives in wiring/mobile-ptt-surface.ts now.
    expect(mobileLayoutSource).toContain('createMobilePttSurface');
    // Deliberately NOT asserting on 'ptt_on'/'ptt_off' as bare substrings —
    // the retirement note in the layout's own comments names them.
    for (const retired of [
      'tx-adapter', 'getTxAudioControl', 'systemHandlers', 'engageTx', 'disengageTx',
      'pttSafetyTimer', 'PTT_SAFETY_TIMEOUT_MS', 'txStartToken', 'lastPttDown',
      'sendCommand',
    ]) {
      expect(mobileLayoutSource).not.toContain(retired);
    }
  });
});

// ---------------------------------------------------------------------------
// 3. The two TX sources cannot disturb each other
// ---------------------------------------------------------------------------
describe('two TX sources on one mobile shell', () => {
  // THE new-risk pin for this slice. The semantic subtree is destroyed on
  // every rotation, and its fail-closed teardown blind-releases the LIVE
  // guard. Kills: giving it the gesture's sourceId (or dropping the sourceId
  // argument), which would let a rotation-time teardown dekey — or double-off
  // — a lease the operator is holding on the FAB.
  it('the semantic teardown cannot dekey a lease the PTT gesture owns', async () => {
    const t = mountMobile();
    await hold(t);
    const owner = tx.controller.snapshot().sourceId;
    expect(owner).toMatch(/^mobile-ptt-portrait-\d+$/);

    rotate(true); // destroys BOTH the portrait recognizer and the semantic deck

    // The semantic surface did ask to release — with its own identity.
    const semanticReleases = tx.releases.filter((r) => /^semantic-rx-tx-/.test(r.sourceId));
    expect(semanticReleases.length).toBeGreaterThan(0);
    // ...and it was inert: the single ptt_off is the recognizer's own
    // documented rotation release (docs/guide/web-ui.md — rotating while
    // keyed or latched releases TX rather than stranding it), not a second.
    expect(offs()).toBe(1);
    expect(tx.releases.filter((r) => r.sourceId === owner)).toHaveLength(1);
    expect(tx.controller.snapshot().phase).toBe('releasing');
  });

  // The mirror image. Kills: the gesture recognizer releasing on a guard it
  // does not own — a latched semantic key must survive an unrelated FAB tap.
  it('the PTT gesture cannot dekey a lease the semantic surface owns', () => {
    const t = mountMobile();
    semanticKey(t);
    const owner = tx.controller.snapshot().sourceId;
    expect(owner).toMatch(/^semantic-rx-tx-\d+$/);

    fabPress(t);
    pointer(fabEl(t), 'pointerup');
    vi.advanceTimersByTime(GESTURE_WINDOW_MS * 2);
    flushSync();

    expect(offs()).toBe(0);
    expect(tx.controller.snapshot().sourceId).toBe(owner);
  });
});

// ---------------------------------------------------------------------------
// 4. Orientation preserves App-owned authority and resources
// ---------------------------------------------------------------------------
describe('orientation change preserves App authority (MOR-1086 doctrine)', () => {
  // Kills: the shell re-deriving, re-creating or re-hosting TX authority per
  // orientation. A rotation is an intra-layout surface swap; the App-owned
  // controller object must be the IDENTICAL instance on the other side.
  it('keeps the identical App TX controller object across a rotation', () => {
    const t = mountMobile();
    const before = txHost.current;
    rotate(true);
    rotate(false);
    expect(txHost.current).toBe(before);
    expect(txHost.current).toBe(tx.facade);
    // And the shell is still LIVE on it: a post-rotation key still lands.
    semanticKey(t);
    expect(tx.controller.snapshot().sourceId).toMatch(/^semantic-rx-tx-\d+$/);
  });

  // Kills: the semantic subtree taking its own App resource demand. It is
  // destroyed and rebuilt on every rotation, so any demand it held would
  // bounce the mobile hardware-scope stream (the MOR-1092 mutation, applied
  // to mobile). Demand belongs to SpectrumPanel via the skin resource plan.
  it('leaves App resource demand entirely outside the semantic subtree', () => {
    const wiringSource = readFileSync('src/components-v2/wiring/SemanticRadioSurfaces.svelte', 'utf8');
    for (const source of [mobileLayoutSource, wiringSource]) {
      expect(source).not.toContain('presentationResources');
      expect(source).not.toContain('resource-demand');
    }
    // The plan still names hardware-scope for mobile, owned by SpectrumPanel.
    const registrySource = readFileSync('src/skins/registry.ts', 'utf8');
    expect(registrySource).toContain("'mobile': ['hardware-scope']");
  });

  // Kills: silently flipping the recorded rotation behaviour. The guide says
  // rotating while keyed or latched RELEASES TX rather than stranding it;
  // this asserts the direction the code actually implements, both ways.
  it('still releases TX on rotation, in the direction the guide records', async () => {
    const guide = readFileSync('../docs/guide/web-ui.md', 'utf8');
    expect(guide).toContain('rotating the device while keyed or latched releases TX');

    const t = mountMobile();
    await hold(t);
    rotate(true);
    expect(offs()).toBe(1);
    expect(tx.controller.snapshot().phase).toBe('releasing');
  });
});

// ---------------------------------------------------------------------------
// 5. Global-host singletons stay global (MOR-1059)
// ---------------------------------------------------------------------------
describe('the App-global host stays singular on mobile (MOR-1059)', () => {
  // Kills: the migration reintroducing a layout-hosted Toast, power overlay
  // or TX lamp — the duplicates MOR-1059 pulled up to the composition root.
  it.each([['portrait', false], ['landscape', true]] as const)(
    'hosts no Toast, power overlay or global TX lamp in %s', (_label, landscape) => {
      setViewport(landscape);
      const t = mountMobile();
      for (const selector of [
        '.toast-container',
        '[data-testid="app-global-host"]',
        '[data-testid="global-power-off"]',
        '[data-testid="global-tx-indication"]',
        '[data-testid="global-tx-fault"]',
      ]) {
        expect(t.querySelectorAll(selector)).toHaveLength(0);
      }
    });

  // Source-level backstop, matching the app-global-host suite's idiom.
  it('imports neither the Toast nor the global host itself', () => {
    expect(mobileLayoutSource).not.toMatch(/shared\/Toast\.svelte/);
    expect(mobileLayoutSource).not.toMatch(/<Toast\b/);
    expect(mobileLayoutSource).not.toContain('power-off-overlay');
    expect(mobileLayoutSource).not.toMatch(/<AppGlobalHost\b/);
  });
});

// ---------------------------------------------------------------------------
// 6. The skin wrapper owns no resolution and no runtime
// ---------------------------------------------------------------------------
describe('the mobile skin wrapper is a pure delegator', () => {
  // Kills: the wrapper re-deriving which skin/layout to show. Selection,
  // loading and commit belong to App.svelte over skins/registry.ts; a wrapper
  // that resolves again can disagree with the presentation App committed.
  it('owns no resolver: it selects nothing and loads nothing', () => {
    for (const owned of [
      'resolveSkinId', 'loadSkin', 'resolvePersistedSkinId', 'normalizeLayoutMode',
      'presentationResourcePlan', 'getLayout', 'resolveLayoutForViewport',
    ]) {
      expect(mobileSkinSource).not.toContain(owned);
    }
  });

  // Kills: the wrapper reaching past the presentation boundary. eslint's
  // no-restricted-imports covers src/skins/**; this pins the same boundary
  // from the test side so a config drift cannot silently open it.
  it('owns no runtime: no transport, command, capability or TX-authority import', () => {
    for (const forbidden of [
      '$lib/transport', 'audio-manager', '$lib/stores/capabilities',
      'tx-controller/app-host', 'sendCommand', 'wiring/command-bus',
    ]) {
      expect(mobileSkinSource).not.toContain(forbidden);
    }
    // What it DOES do: delegate, and nothing else.
    expect(mobileSkinSource).toContain('MobileRadioLayout');
  });

  // Kills: a manifest that reaches for live state. Manifests are declarations
  // — the presentation/ zone bans runtime, transport and capability imports.
  it('keeps the mobile manifest a declaration over the layout contract alone', () => {
    const manifest = readFileSync('src/presentation/layouts/mobile-declarations.ts', 'utf8');
    const imports = [...manifest.matchAll(/from '([^']+)'/g)].map((m) => m[1]);
    expect(imports).toEqual(['./contract']);
  });
});
