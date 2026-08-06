import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { TxController } from '$lib/runtime/tx-controller/controller';
import type { TxControllerDependencies } from '$lib/runtime/tx-controller/controller';

// -- Child component stubs --
vi.mock('../../../components/spectrum/SpectrumPanel.svelte', async () => {
  const s = await import('./SpectrumPanelStub.svelte');
  return { default: s.default };
});
vi.mock('../panels/lcd/AmberLcdDisplay.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../display/FrequencyDisplay.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../meters/LinearSMeter.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../controls/CollapsiblePanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../controls/BottomSheet.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../controls/BandSelector.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/FilterPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/RxAudioPanel.svelte', () => ({ default: function S() { return {}; } }));
// TxPanel stays stubbed: it is a lease source in its own right (MOR-1011) and
// mounting the real one inside the TX-settings sheet would put a second
// recognizer on the same controller, which is not what these tests are about.
vi.mock('../panels/TxPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/DspPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/AgcPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/RfFrontEnd.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/RitXitPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/AntennaPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/ScanPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/CwPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/DockMeterPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('./KeyboardHandler.svelte', () => ({ default: function S() { return {}; } }));
// Note: ../panels/EssentialsPanel.svelte and ./mobile-chip-bar.svelte are intentionally
// NOT mocked — the chip-scroll IA contract (#839) is part of what these tests cover.
// ../controls/PttFab.svelte is intentionally NOT mocked either: its layered
// guards (50 ms hold, 8 px move-cancel, TX-permit two-step) are half of the
// mobile PTT contract under test.
vi.mock('$lib/Button', () => ({ HardwareButton: function S() { return {}; } }));
vi.mock('lucide-svelte', () => {
  const S = function () { return {}; };
  return { Settings: S, ChevronLeft: S, ChevronRight: S, ChevronsLeft: S, ChevronsRight: S, Mic: S, MicOff: S, Sliders: S, Radio: S };
});
vi.mock('../controls/value-control', () => ({
  ValueControl: function S() { return {}; },
  rawToPercentDisplay: vi.fn((v: number) => `${Math.round(v / 255 * 100)}%`),
}));
vi.mock('./vfo-layout-tokens', () => ({
  resolveVfoLayoutProfile: vi.fn(() => 'baseline'),
  vfoLayoutStyleVars: vi.fn(() => ''),
}));

// -- Store mocks --
vi.mock('$lib/stores/radio.svelte', () => ({
  radio: { current: null as { active?: 'MAIN' | 'SUB' } | null },
  getActiveReceiver: vi.fn(),
  getRadioState: vi.fn(),
  patchActiveReceiver: vi.fn(),
  patchRadioState: vi.fn(),
  patchReceiver: vi.fn(),
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
  hasSpectrum: vi.fn(() => false), getCapabilities: vi.fn(() => ({ freqRanges: [], modes: [], filters: [] })),
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

// -- Wiring mocks --
const {
  onMainVfoClickSpy,
  onSubVfoClickSpy,
} = vi.hoisted(() => ({
  onMainVfoClickSpy: vi.fn(),
  onSubVfoClickSpy: vi.fn(),
}));
vi.mock('../../wiring/command-bus', () => {
  const n = vi.fn();
  return {
    makeVfoHandlers: () => ({
      onMainFreqChange: n, onSubFreqChange: n, onVfoSwap: n, onVfoEqual: n, onReceiverSelect: n,
      onMainVfoClick: onMainVfoClickSpy, onSubVfoClick: onSubVfoClickSpy,
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
    makeDspHandlers: () => ({ onNrToggle: n, onNbToggle: n, onNotchToggle: n }),
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

// MOR-1012: the layout owns no PTT state any more — it renders the App TX
// controller and feeds it gesture intent. Only the *host* (the context lookup)
// is mocked; behind it sits a REAL TxController over stub dependencies, so
// these tests exercise the production state machine instead of a double.
const txHost = vi.hoisted(() => ({ current: undefined as unknown as TxHostFacade }));

vi.mock('$lib/runtime/tx-controller/app-host', () => ({
  getAppTxController: () => txHost.current,
}));

vi.mock('../wiring/state-adapter', () => {
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
    toAgcProps: vi.fn(() => ({ agcMode: 3 })), toRitXitProps: vi.fn(() => ({ ritOn: false, ritOffset: 0, xitOn: false, xitOffset: 0 })),
    toDspProps: vi.fn(() => ({ nr: false, nb: false, notch: false })), toCwProps: vi.fn(() => ({ speed: 20 })),
    toAntennaProps: vi.fn(() => ({ selected: 1 })),
    toScanProps: vi.fn(() => ({ scanning: false, scanType: 'off', scanResumeMode: 'time' })),
  };
});

import MobileRadioLayout from '../MobileRadioLayout.svelte';
import mobileLayoutSource from '../MobileRadioLayout.svelte?raw';
import { hasTx, hasDualReceiver } from '$lib/stores/capabilities.svelte';
import { radio } from '$lib/stores/radio.svelte';
import { getTxPermit } from '$lib/utils/tx-permit';

// ---------------------------------------------------------------------------
// Real-controller harness
//
// Copied verbatim (bar this note) from
// src/components-v2/panels/__tests__/TxPanel.isolated.test.ts, which in turn follows
// tx-controller/__tests__/controller-contract. Duplicated rather than shared
// because the two suites live in different pools and a shared helper module
// would pin the controller in the ``isolate: false`` cache for siblings that
// mock it — see vite.config.ts / #771. Keep the two copies in step.
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

/** Mirrors the deep-frozen snapshot/subscribe payloads app-host hands out. */
function deepFreeze<T>(value: T): T {
  if (value === null || typeof value !== 'object') return value;
  const copy = Object.fromEntries(Object.entries(value as Record<string, unknown>)
    .map(([key, child]) => [key, deepFreeze(child)]));
  return Object.freeze(copy) as unknown as T;
}

function createTxHarness() {
  const sends: Array<{ command: Command; report: Report }> = [];
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
    // Far beyond the 300 ms gesture window so controller deadlines never race it.
    timeoutMs: { 'audio-start': 60_000, 'on-confirmation': 60_000, 'off-confirmation': 60_000 },
  };
  const controller = new TxController(1, marker(0), dependencies);
  const facade = {
    snapshot: () => deepFreeze(controller.snapshot()),
    subscribe: (listener: (state: unknown) => void) =>
      controller.subscribe((state) => listener(deepFreeze(state))),
    start: (sourceId: string, leaseId: string, intent: Intent) => controller.dispatch({
      type: 'start', sourceId, leaseId, intent,
      eligibility: eligibility.current, ptt: observe(false, ++seq),
    }),
    setIntent: (sourceId: string, guard: Guard, intent: Intent) =>
      controller.dispatch({ type: 'intent', sourceId, guard: { ...guard }, intent }),
    release: (sourceId: string, guard: Guard) => controller.dispatch({
      type: 'release', sourceId, guard: { ...guard }, commandId: dependencies.commandId('off'),
    }),
    resetFault: () => controller.dispatch({ type: 'reset-fault' }),
  };
  return {
    controller, dependencies, sends, facade, audio, eligibility,
    /** Feed an authoritative PTT readback (what the App host does on session updates). */
    authority: (value: boolean) => controller.dispatch({
      type: 'authority', epoch: 1, ptt: observe(value, ++seq),
      eligibility: eligibility.current, offCommandId: dependencies.commandId('off'),
    }),
    /** Report the most recent command of `command` as delivered. */
    confirm: (command: Command) => {
      const send = [...sends].reverse().find((item) => item.command === command);
      expect(send).toBeDefined();
      send!.report({ outcome: 'sent', eventEpoch: 1, barrier: marker(++seq) });
    },
    /** A competing lease source (e.g. a desktop TxPanel on the same session). */
    startOther: (leaseId: string) => controller.dispatch({
      type: 'start', sourceId: 'other-panel', leaseId, intent: 'momentary',
      eligibility: eligibility.current, ptt: observe(false, ++seq),
    }),
  };
}

let tx: ReturnType<typeof createTxHarness>;
let components: ReturnType<typeof mount>[] = [];

/** PttFab's press-and-hold guard, and the recognizer's double-tap window. */
const HOLD_MS = 50;
const GESTURE_WINDOW_MS = 300;

function setViewport(landscape: boolean) {
  Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: landscape ? 844 : 390 });
  Object.defineProperty(window, 'innerHeight', { writable: true, configurable: true, value: landscape ? 390 : 844 });
}

function mountMobile(): HTMLElement {
  const t = document.createElement('div');
  document.body.appendChild(t);
  components.push(mount(MobileRadioLayout, { target: t }));
  flushSync();
  return t;
}

function mountLandscape(): HTMLElement {
  setViewport(true);
  return mountMobile();
}

/** Rotate the device. jsdom has no fullscreen API; the layout guards for it. */
function rotate(landscape: boolean) {
  setViewport(landscape);
  window.dispatchEvent(new Event('resize'));
  flushSync();
}

beforeEach(() => {
  components = [];
  setViewport(false);
  tx = createTxHarness();
  txHost.current = tx.facade;
  vi.mocked(hasTx).mockReturnValue(true);
  vi.mocked(hasDualReceiver).mockReturnValue(false);
  vi.mocked(getTxPermit).mockReturnValue('allowed');
  (radio as unknown as { current: { active?: 'MAIN' | 'SUB' } | null }).current = null;
  onMainVfoClickSpy.mockClear();
  onSubVfoClickSpy.mockClear();
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  document.body.innerHTML = '';
});

describe('MobileRadioLayout structure', () => {
  it('mounts without errors', () => {
    expect(mountMobile().children.length).toBeGreaterThan(0);
  });

  it('renders .m-layout root in portrait mode', () => {
    expect(mountMobile().querySelector('.m-layout')).not.toBeNull();
  });

  it('renders VFO header bar', () => {
    expect(mountMobile().querySelector('.m-vfo-bar')).not.toBeNull();
  });

  it('renders VFO frequency row', () => {
    expect(mountMobile().querySelector('.m-vfo-row')).not.toBeNull();
  });

  it('renders S-meter bar', () => {
    expect(mountMobile().querySelector('.m-smeter-bar')).not.toBeNull();
  });

  it('renders scrollable main content area', () => {
    expect(mountMobile().querySelector('.m-content')).not.toBeNull();
  });

  it('renders tuning strip', () => {
    expect(mountMobile().querySelector('.m-tuning-strip')).not.toBeNull();
  });

  it('renders section panels inside m-content', () => {
    expect(mountMobile().querySelectorAll('.m-content .m-section').length).toBeGreaterThan(0);
  });

  it('renders TX indicator', () => {
    expect(mountMobile().querySelector('.m-tx-indicator')).not.toBeNull();
  });

  it('renders settings button', () => {
    expect(mountMobile().querySelector('.m-settings-btn')).not.toBeNull();
  });
});

describe('MobileRadioLayout TX gating', () => {
  it('renders TX chip when hasTx is true (#839)', () => {
    vi.mocked(hasTx).mockReturnValue(true);
    const t = mountMobile();
    const chipBar = t.querySelector('.m-chip-bar');
    expect(chipBar).not.toBeNull();
    const labels = Array.from(chipBar?.querySelectorAll('.m-chip') ?? []).map(
      (b) => b.textContent?.trim(),
    );
    expect(labels).toContain('TX');
  });

  it('omits TX chip when hasTx is false (#839)', () => {
    vi.mocked(hasTx).mockReturnValue(false);
    const t = mountMobile();
    const chipBar = t.querySelector('.m-chip-bar');
    expect(chipBar).not.toBeNull();
    const labels = Array.from(chipBar?.querySelectorAll('.m-chip') ?? []).map(
      (b) => b.textContent?.trim(),
    );
    expect(labels).not.toContain('TX');
    // And the TX-only PTT is not mounted on cold-open (ESSENTIALS active by default).
    expect(t.querySelector('.m-ptt-btn')).toBeNull();
  });

  it('auto-resets active chip to ESSENTIALS when TX capability drops at runtime (#839)', () => {
    // Back the hasTx mock with a reactive $state so the component's $derived
    // txCapable re-evaluates when we flip capability mid-session.
    const txState = $state({ on: true });
    vi.mocked(hasTx).mockImplementation(() => txState.on);

    const t = mountMobile();
    // Select TX chip while TX-capable.
    const txChip = Array.from(t.querySelectorAll<HTMLButtonElement>('.m-chip')).find(
      (b) => b.textContent?.trim() === 'TX',
    );
    expect(txChip, 'TX chip should be present when hasTx=true').toBeDefined();
    txChip!.click();
    flushSync();
    expect(t.querySelector('#m-chip-panel-tx')).not.toBeNull();

    // Simulate capability refresh: TX disappears.
    txState.on = false;
    flushSync();

    // Guard $effect must reset activeChipId back to ESSENTIALS, so no panel goes blank.
    expect(t.querySelector('#m-chip-panel-tx')).toBeNull();
    expect(t.querySelector('#m-chip-panel-essentials')).not.toBeNull();
    // ESSENTIALS chip should now be the active one in the chip bar.
    const active = t.querySelector('.m-chip-bar .m-chip-active');
    expect(active?.textContent?.trim()).toBe('ESSENTIALS');
  });
});

describe('MobileRadioLayout chip-scroll IA (#839)', () => {
  it('renders chip bar inside m-content with ESSENTIALS default-active', () => {
    const t = mountMobile();
    const bar = t.querySelector('.m-content .m-chip-bar');
    expect(bar).not.toBeNull();
    const active = bar?.querySelector('.m-chip-active');
    expect(active?.textContent?.trim()).toBe('ESSENTIALS');
    expect(t.querySelector('#m-chip-panel-essentials')).not.toBeNull();
  });

  it('renders exactly one active chip panel at a time', () => {
    const t = mountMobile();
    const panels = t.querySelectorAll('[id^="m-chip-panel-"]');
    expect(panels.length).toBe(1);
  });
});

describe('MobileRadioLayout unmount', () => {
  it('unmounts cleanly without errors', () => {
    const t = mountMobile();
    expect(t.querySelector('.m-layout')).not.toBeNull();
    expect(() => unmount(components.pop()!)).not.toThrow();
  });
});

describe('MobileRadioLayout receiver selector (#719)', () => {
  it('does not render selector on single-receiver radios', () => {
    vi.mocked(hasDualReceiver).mockReturnValue(false);
    expect(mountMobile().querySelector('.m-receiver-selector')).toBeNull();
  });

  it('renders MAIN/SUB pills when dual-RX', () => {
    vi.mocked(hasDualReceiver).mockReturnValue(true);
    const t = mountMobile();
    const group = t.querySelector('.m-receiver-selector');
    expect(group).not.toBeNull();
    expect(group?.getAttribute('role')).toBe('group');
    expect(group?.getAttribute('aria-label')).toBe('Receiver selector');
    const pills = t.querySelectorAll<HTMLButtonElement>('.m-receiver-pill');
    expect(pills.length).toBe(2);
    expect(pills[0].textContent?.trim()).toBe('MAIN');
    expect(pills[1].textContent?.trim()).toBe('SUB');
  });

  it('marks the active receiver with aria-pressed=true (MAIN default)', () => {
    vi.mocked(hasDualReceiver).mockReturnValue(true);
    const t = mountMobile();
    const [mainPill, subPill] = Array.from(
      t.querySelectorAll<HTMLButtonElement>('.m-receiver-pill'),
    );
    expect(mainPill.getAttribute('aria-pressed')).toBe('true');
    expect(subPill.getAttribute('aria-pressed')).toBe('false');
    expect(mainPill.classList.contains('m-receiver-pill-active')).toBe(true);
  });

  it('reflects SUB as active when radioState.active === SUB', () => {
    vi.mocked(hasDualReceiver).mockReturnValue(true);
    (radio as unknown as { current: { active?: 'MAIN' | 'SUB' } | null }).current = { active: 'SUB' };
    const t = mountMobile();
    const [mainPill, subPill] = Array.from(
      t.querySelectorAll<HTMLButtonElement>('.m-receiver-pill'),
    );
    expect(mainPill.getAttribute('aria-pressed')).toBe('false');
    expect(subPill.getAttribute('aria-pressed')).toBe('true');
    expect(subPill.classList.contains('m-receiver-pill-active')).toBe(true);
  });

  it('pills are focusable buttons (keyboard a11y)', () => {
    vi.mocked(hasDualReceiver).mockReturnValue(true);
    const t = mountMobile();
    const pills = t.querySelectorAll<HTMLButtonElement>('.m-receiver-pill');
    pills.forEach((p) => {
      expect(p.tagName).toBe('BUTTON');
      expect(p.getAttribute('type')).toBe('button');
    });
    pills[0].focus();
    expect(document.activeElement).toBe(pills[0]);
    pills[1].focus();
    expect(document.activeElement).toBe(pills[1]);
  });

  it('tapping MAIN pill dispatches onMainVfoClick', () => {
    vi.mocked(hasDualReceiver).mockReturnValue(true);
    const t = mountMobile();
    const [mainPill] = Array.from(
      t.querySelectorAll<HTMLButtonElement>('.m-receiver-pill'),
    );
    mainPill.click();
    expect(onMainVfoClickSpy).toHaveBeenCalledTimes(1);
    expect(onSubVfoClickSpy).not.toHaveBeenCalled();
  });

  it('tapping SUB pill dispatches onSubVfoClick and scrolls VFO display', () => {
    vi.mocked(hasDualReceiver).mockReturnValue(true);
    const scrollSpy = vi.fn();
    // Patch Element.prototype.scrollIntoView for this test
    const origScroll = Element.prototype.scrollIntoView;
    Element.prototype.scrollIntoView = scrollSpy as unknown as typeof Element.prototype.scrollIntoView;
    try {
      const t = mountMobile();
      const pills = Array.from(t.querySelectorAll<HTMLButtonElement>('.m-receiver-pill'));
      pills[1].click();
      expect(onSubVfoClickSpy).toHaveBeenCalledTimes(1);
      expect(onMainVfoClickSpy).not.toHaveBeenCalled();
      expect(scrollSpy).toHaveBeenCalled();
      const arg = scrollSpy.mock.calls[0]?.[0];
      expect(arg).toMatchObject({ behavior: 'smooth', block: 'center' });
    } finally {
      Element.prototype.scrollIntoView = origScroll;
    }
  });
});

// ---------------------------------------------------------------------------
// PTT — driven entirely by the App TX controller (MOR-1012)
// ---------------------------------------------------------------------------

describe('mobile PTT via the App TX controller (MOR-1012)', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  const fabEl = (t: HTMLElement) => t.querySelector<HTMLButtonElement>('.ptt-fab')!;
  const stripEl = (t: HTMLElement) => t.querySelector<HTMLButtonElement>('.m-ls-ptt')!;
  const offs = () => tx.sends.filter((item) => item.command === 'off').length;
  const flushAudio = async () => { await Promise.resolve(); await Promise.resolve(); flushSync(); };

  function pointer(el: Element, type: string, init: PointerEventInit = {}) {
    el.dispatchEvent(new PointerEvent(type, {
      bubbles: true, pointerId: 1, clientX: 0, clientY: 0, ...init,
    }));
    flushSync();
  }

  /** A completed FAB press: PttFab only calls onDown() past its 50 ms guard. */
  function fabPress(t: HTMLElement) {
    pointer(fabEl(t), 'pointerdown');
    vi.advanceTimersByTime(HOLD_MS);
    flushSync();
  }
  const fabRelease = (t: HTMLElement) => pointer(fabEl(t), 'pointerup');

  /** Press and hold the FAB until the radio has acknowledged the ON command. */
  async function hold(t: HTMLElement) {
    fabPress(t);
    await flushAudio();
    tx.confirm('on');
    flushSync();
  }

  /** Hold, then double-tap the FAB into the latched lock. */
  async function latch(t: HTMLElement) {
    await hold(t);
    fabRelease(t);
    vi.advanceTimersByTime(100);
    fabPress(t);
  }

  /** Same two gestures, on the landscape strip (no 50 ms hold guard there). */
  async function holdStrip(t: HTMLElement) {
    pointer(stripEl(t), 'pointerdown');
    await flushAudio();
    tx.confirm('on');
    flushSync();
  }

  async function latchStrip(t: HTMLElement) {
    await holdStrip(t);
    pointer(stripEl(t), 'pointerup');
    vi.advanceTimersByTime(100);
    pointer(stripEl(t), 'pointerdown');
  }

  function selectChip(t: HTMLElement, label: string) {
    const chip = Array.from(t.querySelectorAll<HTMLButtonElement>('.m-chip'))
      .find((b) => b.textContent?.trim() === label);
    expect(chip, `chip ${label} should exist`).toBeDefined();
    chip!.click();
    flushSync();
  }

  // -- A: portrait FAB ------------------------------------------------------

  it('keys a FAB hold through audio start and the ON confirmation', async () => {
    const t = mountMobile();
    fabPress(t);
    expect(tx.dependencies.startAudio).toHaveBeenCalledOnce();
    expect(tx.controller.snapshot().phase).toBe('audio-start-pending');
    await flushAudio();
    tx.confirm('on');
    flushSync();
    expect(tx.controller.snapshot().phase).toBe('key-confirm-pending');
    expect(fabEl(t).classList.contains('ptt-fab-held')).toBe(true);
    tx.authority(true);
    flushSync();
    expect(tx.controller.snapshot().phase).toBe('active');
    // No local safety timer any more — the controller owns every deadline.
    vi.advanceTimersByTime(5 * 60 * 1000);
    expect(offs()).toBe(0);
  });

  it('ignores a press released before the FAB 50 ms hold guard closes', () => {
    const t = mountMobile();
    pointer(fabEl(t), 'pointerdown');
    vi.advanceTimersByTime(HOLD_MS - 20);
    pointer(fabEl(t), 'pointerup');
    vi.advanceTimersByTime(HOLD_MS);
    flushSync();
    expect(tx.dependencies.startAudio).not.toHaveBeenCalled();
    expect(tx.controller.snapshot().phase).toBe('idle');
  });

  it('holds the lease for 299 ms after release and drops it at 300 ms', async () => {
    const t = mountMobile();
    await hold(t);
    fabRelease(t);
    vi.advanceTimersByTime(GESTURE_WINDOW_MS - 1);
    flushSync();
    expect(offs()).toBe(0);
    expect(tx.controller.snapshot().phase).toBe('key-confirm-pending');
    vi.advanceTimersByTime(1);
    flushSync();
    expect(offs()).toBe(1);
    expect(tx.controller.snapshot().phase).toBe('releasing');
  });

  it('latches on a double tap without starting a second lease', async () => {
    const t = mountMobile();
    await latch(t);
    expect(tx.controller.snapshot().intent).toBe('latched');
    expect(tx.dependencies.startAudio).toHaveBeenCalledOnce();
    expect(fabEl(t).classList.contains('ptt-fab-latched')).toBe(true);
    // Confirm the key on the radio, then sit on it far longer than the retired
    // 3-minute local safety timer: a latched lease must stay up.
    tx.authority(true);
    flushSync();
    vi.advanceTimersByTime(5 * 60 * 1000);
    expect(offs()).toBe(0);
  });

  it('unlatches on the next tap instead of starting a new lease', async () => {
    const t = mountMobile();
    await latch(t);
    fabRelease(t);
    pointer(fabEl(t), 'pointerdown'); // latched → PttFab delegates immediately
    expect(offs()).toBe(1);
    expect(tx.controller.snapshot().phase).toBe('releasing');
    expect(tx.dependencies.startAudio).toHaveBeenCalledOnce();
  });

  it('drops a quick tap taken while TX audio is still pending, and never keys late', async () => {
    let resolveAudio!: (value: string | null) => void;
    tx.audio.next = new Promise((resolve) => { resolveAudio = resolve; });
    const t = mountMobile();
    fabPress(t);
    expect(tx.controller.snapshot().phase).toBe('audio-start-pending');
    fabRelease(t);
    vi.advanceTimersByTime(GESTURE_WINDOW_MS);
    flushSync();
    expect(tx.controller.snapshot().phase).toBe('releasing');
    resolveAudio(null);
    await flushAudio();
    expect(tx.sends.filter((item) => item.command === 'on')).toHaveLength(0);
  });

  it('needs the documented two-step press when the UI TX permit is denied', () => {
    vi.mocked(getTxPermit).mockReturnValue('denied');
    const t = mountMobile();
    fabPress(t);
    expect(tx.dependencies.startAudio).not.toHaveBeenCalled();
    expect(fabEl(t).classList.contains('ptt-fab-armed')).toBe(true);
    fabPress(t); // second press inside the 2 s arm window engages
    expect(tx.dependencies.startAudio).toHaveBeenCalledOnce();
  });

  // A.6b — without resetFault() in the recognizer's start command, one denied
  // press leaves the model in 'failed' and every later press is swallowed:
  // mobile TX would be bricked for the rest of the session.
  it('clears a stale controller fault on the next press instead of bricking TX', async () => {
    tx.eligibility.current = { ...allowed, permit: 'denied' };
    const t = mountMobile();
    fabPress(t);
    expect(tx.controller.snapshot()).toMatchObject({ phase: 'failed', fault: 'not-eligible' });
    fabRelease(t);

    tx.eligibility.current = allowed;
    fabPress(t);
    await flushAudio();
    expect(tx.controller.snapshot()).toMatchObject({ fault: null, phase: 'audio-start-pending' });
  });

  // -- B: landscape strip ---------------------------------------------------

  it('keys and releases from the landscape strip', async () => {
    const t = mountLandscape();
    await holdStrip(t);
    expect(tx.controller.snapshot().phase).toBe('key-confirm-pending');
    expect(stripEl(t).classList.contains('m-ptt-held')).toBe(true);
    pointer(stripEl(t), 'pointerup');
    vi.advanceTimersByTime(GESTURE_WINDOW_MS);
    flushSync();
    expect(offs()).toBe(1);
    expect(tx.controller.snapshot().phase).toBe('releasing');
  });

  it('cancels the landscape press only once the finger slides past 8 px', async () => {
    const t = mountLandscape();
    await holdStrip(t);
    pointer(stripEl(t), 'pointermove', { clientX: 5, clientY: 0 });
    vi.advanceTimersByTime(GESTURE_WINDOW_MS);
    flushSync();
    expect(offs()).toBe(0);
    pointer(stripEl(t), 'pointermove', { clientX: 20, clientY: 0 });
    vi.advanceTimersByTime(GESTURE_WINDOW_MS);
    flushSync();
    expect(offs()).toBe(1);
  });

  it('treats pointercancel, lostpointercapture and pointerup as one release', async () => {
    const t = mountLandscape();
    await holdStrip(t);
    pointer(stripEl(t), 'pointercancel');
    pointer(stripEl(t), 'lostpointercapture');
    pointer(stripEl(t), 'pointerup');
    vi.advanceTimersByTime(GESTURE_WINDOW_MS * 2);
    flushSync();
    expect(offs()).toBe(1);
  });

  // -- C: surface lifecycle -------------------------------------------------

  // C.10 — the portrait recognizer is destroyed by the rotation, and destroy()
  // releases the live lease. Before MOR-1012 this depended on a bespoke
  // orientation $effect that only covered portrait → landscape while 'held'.
  it('releases TX when a rotation to landscape tears the portrait recognizer down', async () => {
    const t = mountMobile();
    await hold(t);
    expect(tx.controller.snapshot().phase).toBe('key-confirm-pending');
    rotate(true);
    expect(offs()).toBe(1);
    expect(tx.controller.snapshot().phase).toBe('releasing');
  });

  // C.11 — the direction and the state the old orientation guard did NOT cover.
  // Documented behaviour change: rotating while latched drops TX.
  it('drops a LATCHED TX when the operator rotates back to portrait', async () => {
    const t = mountLandscape();
    await latchStrip(t);
    expect(tx.controller.snapshot().intent).toBe('latched');
    rotate(false);
    expect(offs()).toBe(1);
    expect(tx.controller.snapshot().phase).toBe('releasing');
  });

  it('releases TX when the radio stops reporting TX capability', async () => {
    const capable = $state({ on: true });
    vi.mocked(hasTx).mockImplementation(() => capable.on);
    const t = mountMobile();
    await hold(t);
    capable.on = false;
    flushSync();
    expect(offs()).toBe(1);
    expect(tx.controller.snapshot().phase).toBe('releasing');
  });

  // C.13 — the layout imported onDestroy but never used it, so an unmount left
  // the rig keyed and the safety timer armed.
  it('releases TX when the layout unmounts', async () => {
    const t = mountMobile();
    await hold(t);
    expect(tx.controller.snapshot().phase).toBe('key-confirm-pending');
    unmount(components.pop()!);
    expect(offs()).toBe(1);
    expect(tx.controller.snapshot().phase).toBe('releasing');
  });

  // C.14 — PttFab does not clear its 50 ms hold timer on unmount (R3), so the
  // orphaned timer still calls onDown() after the rotation.
  it('ignores a stale FAB hold timer that fires after a rotation to landscape', () => {
    const t = mountMobile();
    pointer(fabEl(t), 'pointerdown');
    rotate(true);
    vi.advanceTimersByTime(HOLD_MS);
    flushSync();
    expect(tx.dependencies.startAudio).not.toHaveBeenCalled();
    expect(tx.controller.snapshot().phase).toBe('idle');
  });

  // -- D: other lease sources on the same controller -------------------------

  // D.15 — the guard alone always matches (it is the single live lease), so the
  // per-surface sourceId is the only thing stopping mobile from releasing or
  // latching a lease a desktop panel owns.
  it('cannot release or latch a lease owned by another source', async () => {
    const t = mountMobile();
    tx.startOther('desktop-panel-lease');
    flushSync();
    const owner = tx.controller.snapshot().sourceId;

    fabPress(t);   // start() is a silent no-op — the model is not idle
    fabRelease(t); // arms a window against the DESKTOP guard
    vi.advanceTimersByTime(100);
    fabPress(t);   // a double tap that would latch if the sourceId were shared
    vi.advanceTimersByTime(GESTURE_WINDOW_MS * 2);
    flushSync();

    expect(offs()).toBe(0);
    expect(tx.dependencies.startAudio).toHaveBeenCalledOnce();
    expect(tx.controller.snapshot()).toMatchObject({
      sourceId: owner, intent: 'momentary', phase: 'audio-start-pending',
    });
  });

  // D.16 — the two failures the old machine had here: the orphaned FAB timer
  // engaged TX unconditionally, and its release path issued a raw ptt_off that
  // bypassed the controller and could dekey whoever actually owned the key.
  it('cannot key or dekey a desktop owner when a stale FAB timer fires after a rotation', () => {
    const t = mountMobile();
    pointer(fabEl(t), 'pointerdown'); // press starts, hold timer armed
    rotate(true);                     // PttFab unmounts; its timer is NOT cleared
    tx.startOther('desktop-panel-lease');
    flushSync();
    const owner = tx.controller.snapshot().sourceId;

    vi.advanceTimersByTime(HOLD_MS);  // the orphaned FAB timer fires here
    flushSync();
    vi.advanceTimersByTime(GESTURE_WINDOW_MS * 2);
    flushSync();

    expect(tx.dependencies.startAudio).toHaveBeenCalledOnce(); // only the desktop start
    expect(offs()).toBe(0);
    expect(tx.controller.snapshot()).toMatchObject({
      sourceId: owner, phase: 'audio-start-pending',
    });
  });

  // D.17 — the recognizer effect is keyed on the surface alone, so unrelated
  // sheet/chip churn must neither recreate it nor disturb the live lease.
  it('keeps one live lease across TX sheet and chip churn', async () => {
    const t = mountMobile();
    await hold(t);
    const guard = tx.controller.snapshot().guard;

    selectChip(t, 'TX');
    t.querySelector<HTMLButtonElement>('.m-tx-settings-btn')!.click();
    flushSync();
    selectChip(t, 'BAND');
    selectChip(t, 'ESSENTIALS');

    expect(offs()).toBe(0);
    expect(tx.controller.snapshot().guard).toEqual(guard);

    fabRelease(t);
    vi.advanceTimersByTime(GESTURE_WINDOW_MS);
    flushSync();
    expect(offs()).toBe(1);
  });

  // -- E: source-level proof the legacy machine is gone ----------------------

  it('no longer references the retired local PTT machinery', () => {
    expect(mobileLayoutSource).not.toContain('tx-adapter');
    expect(mobileLayoutSource).not.toContain('getTxAudioControl');
    expect(mobileLayoutSource).not.toContain('systemHandlers');
    expect(mobileLayoutSource).not.toContain('engageTx');
    expect(mobileLayoutSource).not.toContain('disengageTx');
    expect(mobileLayoutSource).not.toContain('pttSafetyTimer');
    expect(mobileLayoutSource).not.toContain('PTT_SAFETY_TIMEOUT_MS');
    expect(mobileLayoutSource).not.toContain('txStartToken');
    expect(mobileLayoutSource).not.toContain('lastPttDown');
    // Deliberately NOT asserting on 'ptt' as a bare substring — PttFab and the
    // landscape lsPtt* handlers legitimately keep it.
    expect(mobileLayoutSource).toContain('getAppTxController');
    expect(mobileLayoutSource).toContain('createPttGesture');
  });
});
