/**
 * MOR-4383 seam: phone SCOPE -> SMTH through the REAL MobileRadioLayout and the
 * REAL toolbar-hidden SpectrumPanel, down to the waterfall row uploads.
 *
 * MobileRadioLayout.component.svelte.test.ts replaces SpectrumPanel with a stub
 * file-wide, so it can only pin the dataset the layout binds. This file does
 * NOT mock SpectrumPanel: wire frames enter through the scope WS channel, the
 * real runtime ScopeController delivers them, and the assertions read the
 * texSubImage2D uploads the real WaterfallRenderer makes.
 *
 * Removing `bind:enableSmooth={scopeSmooth}` (MobileRadioLayout.svelte) or
 * `temporalSmoothing: enableSmooth` (SpectrumPanel.svelte) fails the test.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { webglFixture } from '$lib/renderers/__tests__/webgl-fixture';
import { scopeWire } from '$lib/runtime/__tests__/scope-wire-fixture';
import type { ManagedAppTxController } from '$lib/runtime/tx-controller/managed-app-host';
import { ManagedAppTxHarness } from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';

// The scope WS channel is the transport seam: the real runtime ScopeController
// asks ws-client for `getChannel('scope')`, so a fake channel here is ingress.
const scopeChannel = vi.hoisted(() => {
  const receivers = new Set<(buffer: ArrayBuffer) => void>();
  const states = new Set<(state: string) => void>();
  const sessions = new Set<(transition: { state: string; epoch: number }) => void>();
  let state = 'disconnected';
  const channel = {
    sessionEpoch: 0,
    get state() { return state; },
    connect() {
      if (state === 'connected') return;
      state = 'connected';
      channel.sessionEpoch += 1;
      for (const update of states) update(state);
      for (const update of sessions) update({ state, epoch: channel.sessionEpoch });
    },
    disconnect() { state = 'disconnected'; for (const update of states) update(state); },
    sendRaw: () => true,
    onMessage: () => () => {},
    onBinary(handler: (buffer: ArrayBuffer) => void) { receivers.add(handler); return () => receivers.delete(handler); },
    onStateChange(handler: (value: string) => void) { states.add(handler); return () => states.delete(handler); },
    onSessionTransition(handler: (t: { state: string; epoch: number }) => void) {
      sessions.add(handler); return () => sessions.delete(handler);
    },
    fire(bytes: ArrayBuffer) { for (const handler of receivers) handler(bytes); },
  };
  return channel;
});
vi.mock('$lib/transport/ws-client', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/transport/ws-client')>();
  return {
    ...actual,
    sendCommand: vi.fn(),
    getChannel: (name: string) => (name === 'scope' ? scopeChannel : actual.getChannel(name)),
  };
});

// -- Child component stubs: everything EXCEPT SpectrumPanel and the layout --
// MOR-2511: echo fixtures render the header's bound props (frequency,
// S-meter value) as DOM text the active-receiver tests below can pin.
// Two-level specifiers, like the DockMeterPanel mock below: one level
// ('../display/…') would not resolve from __tests__/ and mock nothing.
vi.mock('../../display/FrequencyDisplay.svelte', async () => {
  const s = await import('./fixtures/FrequencyDisplayEcho.svelte');
  return { default: s.default };
});
vi.mock('../../meters/LinearSMeter.svelte', async () => {
  const s = await import('./fixtures/LinearSMeterEcho.svelte');
  return { default: s.default };
});
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
const meterBoundary = vi.hoisted(() => ({
  props: null as null | {
    meterSource: string;
    onMeterSourceChange: (source: string) => void;
  },
}));
vi.mock('../../panels/DockMeterPanel.svelte', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../panels/DockMeterPanel.svelte')>();
  return {
    default: function DockMeterPanelBoundary(
      anchor: Parameters<typeof actual.default>[0],
      props: Parameters<typeof actual.default>[1],
    ) {
      meterBoundary.props = props;
      return actual.default(anchor, props);
    },
  };
});
vi.mock('./KeyboardHandler.svelte', () => ({ default: function S() { return {}; } }));
// Note: ../panels/EssentialsPanel.svelte and ./mobile-chip-bar.svelte are intentionally
// NOT mocked — the chip-scroll IA contract (#839) is part of what these tests cover.
// ../controls/PttFab.svelte is intentionally NOT mocked either: its layered
// guards (50 ms hold, 8 px move-cancel, TX-permit two-step) are half of the
// mobile PTT contract under test.
// MOR-2987: $lib/Button stays REAL — the SCOPE ⋯ sheet's HardwareButton
// keys must render for the intent/grammar pins below (the All-modes sheet
// they copy never opens in this suite, so no other test notices).
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
const radioSubscriberTracker = vi.hoisted(() => ({ active: 0 }));
vi.mock('$lib/stores/radio.svelte', () => ({
  radio: { current: null as { active?: 'MAIN' | 'SUB' } | null },
  getActiveReceiver: vi.fn(),
  getRadioState: vi.fn(),
  subscribeRadioState: vi.fn((handler: (state: null) => void) => {
    radioSubscriberTracker.active += 1;
    handler(null);
    let active = true;
    return () => {
      if (!active) return;
      active = false;
      radioSubscriberTracker.active -= 1;
    };
  }),
}));
vi.mock('$lib/stores/connection.svelte', async (importOriginal) => ({
  // The real scope ingress records liveness through the real store.
  ...await importOriginal<typeof import('$lib/stores/connection.svelte')>(),
  getConnectionStatus: vi.fn(() => ({ connected: false })),
  getWsConnected: vi.fn(() => false),
  getRadioPowerOn: vi.fn(() => null),
  // MOR-1279 slice 3B: the RX-audio snapshot reports audio-WS link health.
  isAudioConnected: vi.fn(() => false),
  // The radio-link line's inputs; the defaults describe a healthy link.
  getRadioLinkState: vi.fn(() => 'connected'),
  getRigConnected: vi.fn(() => true),
  getRadioReady: vi.fn(() => true),
  getRadioHealth: vi.fn(() => null),
}));
vi.mock('$lib/stores/audio.svelte', () => ({
  getAudioState: vi.fn(() => ({ volume: 50, muted: false, rxEnabled: false, txEnabled: false, micEnabled: false, bridgeRunning: false })),
  getRxAudioTargetSnapshot: vi.fn(() => Object.freeze({ muted: false, rxEnabled: false })),
  subscribeRxAudioTarget: vi.fn((handler: (target: { muted: boolean; rxEnabled: boolean }) => void) => {
    handler(Object.freeze({ muted: false, rxEnabled: false }));
    return () => {};
  }),
}));
vi.mock('$lib/audio/audio-manager', () => ({
  audioManager: {
    onChange: () => () => {}, onTxAudioDied: () => () => {}, getAppliedAudioConfig: () => null, start: vi.fn(), stop: vi.fn(), setVolume: vi.fn(), toggleMute: vi.fn(), setOperatorNotifier: vi.fn() },
}));
vi.mock('$lib/utils/tx-permit', async (importOriginal) => ({
  ...await importOriginal<typeof import('$lib/utils/tx-permit')>(),
  getTxPermit: vi.fn(() => 'allowed'),
}));
vi.mock('$lib/stores/tuning.svelte', () => ({ applyModeDefault: vi.fn() }));
vi.mock('$lib/stores/capabilities.svelte', () => ({
  hasTx: vi.fn(() => true), hasDualReceiver: vi.fn(() => false), hasAnyScope: vi.fn(() => false),
  hasSpectrum: vi.fn(() => false), getCapabilities: vi.fn(() => ({ freqRanges: [], modes: [], filters: [] })),
  subscribeCapabilities: vi.fn((handler: (caps: unknown) => void) => {
    handler({ freqRanges: [], modes: [], filters: [] });
    return () => {};
  }),
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


const {
  onMainVfoClickSpy,
  onSubVfoClickSpy,
  radioIntentSpy,
  scopeIntents,
} = vi.hoisted(() => ({
  onMainVfoClickSpy: vi.fn(),
  onSubVfoClickSpy: vi.fn(),
  radioIntentSpy: vi.fn(),
  // MOR-2895: the scope-controls family gets DISTINCT spies — clicking two
  // different scope controls must be tellable apart, which the shared
  // radioIntentSpy cannot do. Same factory the desktop toolbar binds through
  // bindSemanticSurfaceHandlers().scopeControls.
  scopeIntents: {
    onModeChange: vi.fn(), onEdgeChange: vi.fn(), onSpanChange: vi.fn(),
    onSpeedChange: vi.fn(), onHoldChange: vi.fn(), onRefChange: vi.fn(),
    onDualChange: vi.fn(), onReceiverChange: vi.fn(), onDuringTxChange: vi.fn(),
    onCenterTypeChange: vi.fn(), onVbwChange: vi.fn(), onRbwChange: vi.fn(),
  },
}));
// MOR-1409 A13a: the layout now binds its handler families through
// `lib/runtime/adapters/panel-adapters`, so this fixture re-points one level
// down at the command module both the adapter and the retired shim re-export.
// Reference re-point only — every fake below is unchanged.
vi.mock('$lib/runtime/commands/panel-commands', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/runtime/commands/panel-commands')>();
  const n = radioIntentSpy;
  return {
    ...actual,
    makeVfoHandlers: () => ({
      onMainFreqChange: n, onSubFreqChange: n, onFreqChange: n, onVfoSwap: n, onVfoEqual: n, onReceiverSelect: n,
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
    // MOR-2895: distinct per-handler spies (see the hoisted block) so the
    // SCOPE-tab tests can pin WHICH intent each control dispatches.
    makeScopeControlsHandlers: () => ({ ...scopeIntents }),
  };
});

// The layout sees the same read-only App-root facade as production. The
// harness records transport intent and changes visible state only when a
// canonical server snapshot is emitted.
const txHost = vi.hoisted(() => ({ current: undefined as unknown as ManagedAppTxController }));

vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({
  getManagedAppTxController: () => txHost.current,
}));

// MOR-1409 A13a: the layout reads the canonical projections now. This fixture
// keeps its own fabricated values on purpose — the honesty behaviour of the
// real projections is pinned by `MobileRadioLayout.honesty.isolated.test.ts`;
// what this suite covers (chip IA, PTT, receiver pills, meter selection) needs
// a populated rig, not an unobserved one. Reference re-point only.
vi.mock('$lib/runtime/props/panel-props', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/runtime/props/panel-props')>();
  const vfo = { freq: 14074000, mode: 'USB', filter: 'FIL1', sValue: 0, badges: {}, receiver: 'main', isActive: true };
  return {
    ...actual,
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
import { hasTx, hasDualReceiver, hasSpectrum, getCapabilities, getScopeSource } from '$lib/stores/capabilities.svelte';
import type { Capabilities } from '$lib/types/capabilities';
import { radio } from '$lib/stores/radio.svelte';
import { isStackedTraceEnabled, setStackedTraceEnabled } from '$lib/stores/stacked-trace.svelte';
import { getTxPermit } from '$lib/utils/tx-permit';
import { presentationResources } from '$lib/runtime/frontend-runtime';
import { scopeController } from '$lib/runtime/scope-controller.svelte';
import { IC7300_CAPABILITIES, IC7300_STATE } from '$lib/runtime/adapters/__tests__/fixtures/ic7300-profile';
import { WaterfallRenderer } from '$lib/renderers/waterfall-renderer';

let tx: ManagedAppTxHarness;
let components: ReturnType<typeof mount>[] = [];

/** Every WebGL fixture the waterfall canvases create, so row uploads stay readable. */
function captureWaterfallFixtures(): {
  uploads: () => number; lastGroupBytes: () => number[][]; restore: () => void;
} {
  const created: ReturnType<typeof webglFixture>[] = [];
  const previous = HTMLCanvasElement.prototype.getContext;
  HTMLCanvasElement.prototype.getContext = vi.fn(function (this: HTMLCanvasElement, type: string) {
    if (type === 'webgl2') {
      const fixture = webglFixture(this.width, this.height);
      created.push(fixture);
      return fixture.gl;
    }
    return null; // jsdom has no 2d context; the scope canvases tolerate none.
  }) as unknown as typeof HTMLCanvasElement.prototype.getContext;
  const calls = () => created.flatMap((fixture) => fixture.named('texSubImage2D'));
  return {
    uploads: () => calls().length,
    // One pushRow writes one 3-row blended group; the buffer is reused, so
    // snapshot the bytes synchronously before the next frame.
    lastGroupBytes: () => calls().slice(-3).map((call) => {
      const src = call.args[8] as Uint8Array;
      const offset = call.args[9] as number;
      return Array.from(src.subarray(offset, offset + (call.args[4] as number)));
    }),
    restore: () => { HTMLCanvasElement.prototype.getContext = previous; },
  };
}

/** One radio scope frame through the WS ingress; the paint runs from a rAF. */
function deliverFrame(value: number, sequence: number): Promise<void> {
  scopeController.stream('hardware').setMode(0);
  scopeChannel.fire(scopeWire({
    source: 'radio', sourceId: 'radio', receiver: 0, seq: sequence,
    viewStartHz: 14_000_000, viewEndHz: 14_100_000,
    pixels: new Uint8Array(475).fill(value),
  }));
  flushSync();
  return new Promise((resolve) => { setTimeout(resolve, 0); });
}

beforeEach(() => {
  components = [];
  Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 390 });
  Object.defineProperty(window, 'innerHeight', { writable: true, configurable: true, value: 844 });
  globalThis.ResizeObserver = class {
    constructor(private readonly callback: ResizeObserverCallback) {}
    observe(target: Element) {
      this.callback([{ target, contentRect: { width: 200, height: 100 } } as ResizeObserverEntry],
        this as unknown as ResizeObserver);
    }
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver;
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) =>
    setTimeout(() => cb(performance.now()), 0) as unknown as number);
  vi.stubGlobal('cancelAnimationFrame', (id: number) => clearTimeout(id));
  tx = new ManagedAppTxHarness();
  txHost.current = tx.controller;
  vi.mocked(hasTx).mockReturnValue(true);
  vi.mocked(hasDualReceiver).mockReturnValue(false);
  vi.mocked(getTxPermit).mockReturnValue('allowed');
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  document.body.innerHTML = '';
  vi.unstubAllGlobals();
});

describe('MobileRadioLayout SCOPE -> SMTH reaches the waterfall rows (MOR-4383)', () => {
  it('the phone SMTH key toggles temporal smoothing of subsequently rendered rows', async () => {
    const oldCaps = getCapabilities();
    vi.mocked(hasSpectrum).mockReturnValue(true);
    vi.mocked(getScopeSource).mockReturnValue('hardware');
    vi.mocked(getCapabilities).mockReturnValue({
      ...IC7300_CAPABILITIES, vfoReadback: 'none',
    } as Capabilities);
    // Matching provider generations qualify the managed scope lease.
    (radio as unknown as { current: unknown }).current = structuredClone(IC7300_STATE);
    // What the started runtime derives from the scope capability.
    scopeController.configureSources(['radio'], 'radio');
    presentationResources.configure('hardware-scope', { available: true, selected: true });
    const capture = captureWaterfallFixtures();
    const update = vi.spyOn(WaterfallRenderer.prototype, 'updateOptions');
    try {
      const target = document.createElement('div');
      document.body.appendChild(target);
      components.push(mount(MobileRadioLayout, { target }));
      flushSync();
      // The portrait panorama is the REAL panel with its toolbar hidden.
      expect(target.querySelector('.spectrum-panel-stub')).toBeNull();
      expect(target.querySelector('.waterfall-content')).not.toBeNull();
      expect([...target.querySelectorAll('button')].some((b) => b.textContent?.trim() === 'SMTH')).toBe(false);

      await deliverFrame(0, 1);
      await deliverFrame(80, 2);
      expect(capture.uploads()).toBeGreaterThan(0);
      // IIR at alpha 0.5: state 40, spatial [1 2 1]/4 stays 40, blended
      // against the previous all-zero row as 1/3, 2/3, 1.
      const solid = (value: number): number[] => new Array(475).fill(value);
      expect(capture.lastGroupBytes()).toEqual([solid(13), solid(27), solid(40)]);
      expect(update.mock.calls.at(-1)?.[0]).toMatchObject({ temporalSmoothing: true });

      // The phone's SCOPE tab carries the SMTH key; the toolbar is hidden.
      const scopeChip = [...target.querySelectorAll<HTMLButtonElement>('.m-chip')]
        .find((chip) => chip.textContent?.trim() === 'SCOPE')!;
      scopeChip.click();
      flushSync();
      target.querySelector<HTMLButtonElement>('[data-testid="scope-key-smooth"]')!.click();
      flushSync();

      await deliverFrame(80, 3);
      // Disabling smoothing reaches the renderer through the layout's
      // bind:enableSmooth and the panel's enableSmooth -> temporalSmoothing:
      // raw 80s blended against the previous smoothed row (all 40) as
      // 53, 67, 80 -- never the smoothed 13, 27, 40 above.
      expect(update.mock.calls.at(-1)?.[0]).toMatchObject({ temporalSmoothing: false });
      expect(capture.lastGroupBytes()).toEqual([solid(53), solid(67), solid(80)]);
    } finally {
      update.mockRestore();
      capture.restore();
      vi.mocked(hasSpectrum).mockReturnValue(false);
      vi.mocked(getScopeSource).mockReturnValue(null);
      vi.mocked(getCapabilities).mockReturnValue(oldCaps);
      (radio as unknown as { current: unknown }).current = null;
    }
  });

  it('MOR-4463: the phone SCOPE tab carries the 3D key, and it switches the real pane to the 3D view and back', async () => {
    const oldCaps = getCapabilities();
    vi.mocked(hasSpectrum).mockReturnValue(true);
    vi.mocked(getScopeSource).mockReturnValue('hardware');
    vi.mocked(getCapabilities).mockReturnValue({
      ...IC7300_CAPABILITIES, vfoReadback: 'none',
    } as Capabilities);
    (radio as unknown as { current: unknown }).current = structuredClone(IC7300_STATE);
    scopeController.configureSources(['radio'], 'radio');
    presentationResources.configure('hardware-scope', { available: true, selected: true });
    const capture = captureWaterfallFixtures();
    setStackedTraceEnabled(false);
    try {
      const target = document.createElement('div');
      document.body.appendChild(target);
      components.push(mount(MobileRadioLayout, { target }));
      flushSync();
      // A live frame mounts the spectrum trace the 3D view replaces.
      await deliverFrame(40, 1);
      [...target.querySelectorAll<HTMLButtonElement>('.m-chip')]
        .find((chip) => chip.textContent?.trim() === 'SCOPE')!.click();
      flushSync();
      const key = target.querySelector<HTMLButtonElement>('[data-testid="scope-key-3d"]')!;
      expect(key.textContent?.trim()).toBe('3D');
      expect(key.getAttribute('aria-pressed')).toBe('false');
      expect(target.querySelector('[data-testid="stacked-trace"]')).toBeNull();

      // AVG and PEAK shape the flat trace only. The desktop toolbar hides them
      // while 3D is lit (stacked-trace-toggle.spec.ts); the phone follows the
      // same rule, and the stored values come back untouched on return.
      const avgKey = () => target.querySelector<HTMLButtonElement>('[data-testid="scope-key-avg"]');
      const peakKey = () => target.querySelector<HTMLButtonElement>('[data-testid="scope-key-peak"]');
      expect(avgKey()!.getAttribute('aria-pressed')).toBe('true');
      expect(peakKey()!.getAttribute('aria-pressed')).toBe('true');
      avgKey()!.click();
      flushSync();
      expect(avgKey()!.getAttribute('aria-pressed')).toBe('false');
      expect(peakKey()!.getAttribute('aria-pressed')).toBe('true');

      key.click();
      flushSync();
      expect(isStackedTraceEnabled()).toBe(true);
      expect(key.getAttribute('aria-pressed')).toBe('true');
      expect(target.querySelector('[data-testid="stacked-trace"]')).not.toBeNull();
      expect(avgKey()).toBeNull();
      expect(peakKey()).toBeNull();

      key.click();
      flushSync();
      expect(isStackedTraceEnabled()).toBe(false);
      expect(key.getAttribute('aria-pressed')).toBe('false');
      expect(target.querySelector('[data-testid="stacked-trace"]')).toBeNull();
      expect(avgKey()!.getAttribute('aria-pressed')).toBe('false');
      expect(peakKey()!.getAttribute('aria-pressed')).toBe('true');
    } finally {
      setStackedTraceEnabled(false);
      capture.restore();
      vi.mocked(hasSpectrum).mockReturnValue(false);
      vi.mocked(getScopeSource).mockReturnValue(null);
      vi.mocked(getCapabilities).mockReturnValue(oldCaps);
      (radio as unknown as { current: unknown }).current = null;
    }
  });
});
