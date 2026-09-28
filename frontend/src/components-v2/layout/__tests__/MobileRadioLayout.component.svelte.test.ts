import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { ManagedAppTxController } from '$lib/runtime/tx-controller/managed-app-host';
import { ManagedAppTxHarness } from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';

// -- Child component stubs --
vi.mock('../../../components/spectrum/SpectrumPanel.svelte', async () => {
  const s = await import('./SpectrumPanelStub.svelte');
  return { default: s.default };
});
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
vi.mock('$lib/stores/connection.svelte', () => ({
  getConnectionStatus: vi.fn(() => ({ connected: false })),
  getWsConnected: vi.fn(() => false),
  getRadioPowerOn: vi.fn(() => null),
  // MOR-1279 slice 3B: the RX-audio snapshot reports audio-WS link health.
  isAudioConnected: vi.fn(() => false),
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
    onChange: () => () => {}, getAppliedAudioConfig: () => null, start: vi.fn(), stop: vi.fn(), setVolume: vi.fn(), toggleMute: vi.fn(), setOperatorNotifier: vi.fn() },
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

// -- Wiring mocks --
const wsFrameSpy = vi.hoisted(() => vi.fn());
vi.mock('$lib/transport/ws-client', async (importOriginal) => ({
  ...await importOriginal<typeof import('$lib/transport/ws-client')>(),
  sendCommand: wsFrameSpy,
}));

const {
  onMainVfoClickSpy,
  onSubVfoClickSpy,
  radioIntentSpy,
} = vi.hoisted(() => ({
  onMainVfoClickSpy: vi.fn(),
  onSubVfoClickSpy: vi.fn(),
  radioIntentSpy: vi.fn(),
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
    makeScopeControlsHandlers: () => ({
      onModeChange: n, onEdgeChange: n, onSpanChange: n, onSpeedChange: n, onHoldChange: n,
      onRefChange: n, onDualChange: n, onReceiverChange: n, onDuringTxChange: n,
      onCenterTypeChange: n, onVbwChange: n, onRbwChange: n,
    }),
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
import mobileLayoutSource from '../MobileRadioLayout.svelte?raw';
import { hasTx, hasDualReceiver, hasSpectrum, getCapabilities, getScopeSource } from '$lib/stores/capabilities.svelte';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import { MOD_INPUT_SOURCES } from '$lib/radio/mod-input';
import {
  radio, subscribeRadioState,
} from '$lib/stores/radio.svelte';
import { acknowledgeCommand, beginCommand, confirmCommand, failCommand, getCommandLifecycles, resetCommandLifecycle } from '$lib/stores/commands.svelte';
import { getTxPermit } from '$lib/utils/tx-permit';
import { toVfoProps, type VfoStateProps } from '$lib/runtime/props/panel-props';

let tx: ManagedAppTxHarness;
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
  tx = new ManagedAppTxHarness();
  txHost.current = tx.controller;
  vi.mocked(hasTx).mockReturnValue(true);
  vi.mocked(hasDualReceiver).mockReturnValue(false);
  vi.mocked(getTxPermit).mockReturnValue('allowed');
  (radio as unknown as { current: { active?: 'MAIN' | 'SUB' } | null }).current = null;
  radioSubscriberTracker.active = 0;
  meterBoundary.props = null;
  wsFrameSpy.mockClear();
  radioIntentSpy.mockClear();
  resetCommandLifecycle();
  onMainVfoClickSpy.mockClear();
  onSubVfoClickSpy.mockClear();
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  expect(tx.listenerCount()).toBe(0);
  document.body.innerHTML = '';
});

describe('MobileRadioLayout structure', () => {
  // MOR-2366 offered the MOD-input selector through the BARE rx-audio
  // surface the zero-prop deck mount rendered below the deck. MOR-2816
  // (owner, 2026-09-27: «По вопросу один, согласен») removes that unstyled
  // block — the chip tabs are the one place for controls — so the selector
  // is gone with it; the fixed MOD-input banner's one-click "Set LAN"
  // remedy (MOR-617) remains the operator's path. This test now pins the
  // superseded contract: same radio facts, no selector, no navigation or
  // TX activity stirred by mounting.
  it('offers no in-deck MOD-input selector in portrait (MOR-2366 superseded by MOR-2816)', () => {
    const oldCaps = getCapabilities();
    vi.mocked(getCapabilities).mockReturnValue({
      ...oldCaps, model: 'fixture', receivers: 1, vfoScheme: 'ab',
      capabilities: ['mod_input_routing'],
      dataModeCount: 1,
      dataModeInputs: MOD_INPUT_SOURCES.map(({ value, label }) => ({ value, label })),
    } as Capabilities);
    const fresh = { storePath: 'fixture', observed: true, freshness: 'fresh', availability: 'available' };
    (radio as unknown as { current: ServerState | null }).current = {
      active: 'MAIN', main: { mode: 'USB', dataMode: 0 }, dataOffModInput: 5,
      fieldStatus: { dataOffModInput: fresh },
    } as unknown as ServerState;
    try {
      const t = mountMobile();
      // MOR-2816: the deck block is gone — there is no deck element at all;
      // the selector has no surface to ride on.
      expect(t.querySelector('.m-semantic-deck')).toBeNull();
      expect(t.querySelector('[data-testid="rx-audio-mod-select"]')).toBeNull();
      // The bare rx-audio surface that carried the selector is gone with it
      // — no optional surface renders below the deck at all.
      expect(t.querySelectorAll('[data-testid="rx-audio-surface"]')).toHaveLength(0);
      expect(t.querySelectorAll('[data-testid="semantic-radio-surfaces"]')).toHaveLength(1);
      const navigation = [...t.querySelectorAll('.m-chip')].map((chip) => chip.textContent);
      const listeners = tx.listenerCount();
      expect(t.querySelectorAll('[id^="m-chip-panel-"]')).toHaveLength(1);
      expect(tx.listenerCount()).toBe(listeners);
      expect(tx.trace()).toEqual([]);
      expect(wsFrameSpy).not.toHaveBeenCalled();
      expect([...t.querySelectorAll('.m-chip')].map((chip) => chip.textContent)).toEqual(navigation);
    } finally {
      vi.mocked(getCapabilities).mockReturnValue(oldCaps);
    }
  });

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

  it('keeps stale managed authority unknown despite legacy txActive false', () => {
    tx.emitStale();
    const indicator = mountMobile().querySelector<HTMLElement>('.m-tx-indicator')!;
    expect(indicator.dataset.rf).toBe('unknown');
    expect(indicator.style.background).toContain('#facc15');
  });

  // MOR-2705: an unread TX state carries NO title — never the placeholder
  // word 'TX status unknown'. Known states keep their catalog titles.
  it('carries no title while the TX state is unknown (MOR-2705)', () => {
    tx.emitStale();
    const indicator = mountMobile().querySelector<HTMLElement>('.m-tx-indicator')!;
    expect(indicator.dataset.rf).toBe('unknown');
    expect(indicator.getAttribute('title')).toBeNull();
  });

  it('keeps its catalog title for a known TX state (MOR-2705)', () => {
    const indicator = mountMobile().querySelector<HTMLElement>('.m-tx-indicator')!;
    expect(indicator.dataset.rf).toBe('off');
    expect(indicator.getAttribute('title')).toBe('TX allowed');
  });

  it('renders settings button', () => {
    expect(mountMobile().querySelector('.m-settings-btn')).not.toBeNull();
  });

  // MOR-2852 — the meta row carries the receiver facts (BW/AGC/NB/NR) for the
  // receiver the header shows, reached through the hosted instruments.
  describe('meta row receiver facts (MOR-2852)', () => {
    const capsFacts = (receivers = 2): Capabilities => ({
      freqRanges: [], modes: ['USB', 'LSB'], filters: ['FIL1', 'FIL2'], stateContractVersion: 1,
      providerGeneration: 1,
      receivers, vfoScheme: receivers === 2 ? 'main_sub' : 'single',
      capabilities: ['dual_rx', 'filter_width', 'agc', 'nb', 'nr'],
      agcModes: [0, 1, 2],
      agcLabels: { 0: 'OFF', 1: 'FAST', 2: 'SLOW' },
    } as unknown as Capabilities);
    const seen = () => ({ observed: true, freshness: 'fresh', availability: 'available', lastObservedMonotonic: 1 });
    function receiverFacts(active: 'MAIN' | 'SUB', caps: Capabilities) {
      vi.mocked(hasDualReceiver).mockReturnValue(caps.receivers === 2);
      (radio as unknown as { current: ServerState | null }).current = {
        active: active, providerGeneration: 1,
        main: {
          freqHz: 14_250_000, mode: 'USB', filter: 0, dataMode: 0, activeSlot: 'A', sMeter: -12,
          vfoA: {}, vfoB: {}, filterWidth: 2400, agc: 1, nb: false, nr: false,
        } as unknown,
        ...(caps.receivers === 2 ? {
          sub: {
            freqHz: 7_100_000, mode: 'LSB', filter: 1, dataMode: 0, activeSlot: 'A', sMeter: -12,
            vfoA: {}, vfoB: {}, filterWidth: 500, agc: 2, nb: true, nr: true,
          } as unknown,
        } : {}),
        connection: {} as ServerState['connection'],
        fieldStatus: {
          'main.filterWidth': seen(), 'main.agc': seen(), 'main.nb': seen(), 'main.nr': seen(),
          ...('SUB' === active || caps.receivers === 2 ? {
            'sub.filterWidth': seen(), 'sub.agc': seen(), 'sub.nb': seen(), 'sub.nr': seen(),
          } : {}),
        } as unknown as ServerState['fieldStatus'],
      } as unknown as ServerState;
      vi.mocked(getCapabilities).mockReturnValue(caps);
    }

    it('shows the MAIN receiver facts after mode and filter in the meta row', () => {
      const previous = getCapabilities(); const caps = capsFacts();
      receiverFacts('MAIN', caps);
      try {
        const t = mountMobile();
        const meta = t.querySelector('.m-vfo-meta')!;
        expect(meta).not.toBeNull();
        expect(meta.querySelector('.m-vfo-mode')?.textContent).toBe('USB');
        expect(meta.querySelector('.m-vfo-filter')?.textContent).toBe('FIL1');
        const facts = [...meta.querySelectorAll('[data-indicator-fact]')];
        expect(facts.map((node) => node.getAttribute('data-indicator-fact')))
          .toEqual(['bandwidth', 'agc', 'nb', 'nr']);
        expect(facts.map((node) => node.textContent?.trim()))
          .toEqual(['BW 2400 Hz', 'AGC FAST', 'NB', 'NR']);
        expect(meta.querySelector('[data-indicator-fact="nb"]')?.getAttribute('data-state')).toBe('off');
      } finally {
        vi.mocked(getCapabilities).mockReturnValue(previous);
        vi.mocked(hasDualReceiver).mockReturnValue(false);
      }
    });

    it('switches the facts with the receiver the header shows', () => {
      const previous = getCapabilities(); const caps = capsFacts();
      receiverFacts('SUB', caps);
      try {
        const t = mountMobile();
        const meta = t.querySelector('.m-vfo-meta')!;
        const facts = [...meta.querySelectorAll('[data-indicator-fact]')];
        expect(facts.map((node) => node.textContent?.trim())).toEqual(['BW 500 Hz', 'AGC SLOW', 'NB', 'NR']);
        expect(meta.querySelector('[data-indicator-fact="nb"]')?.getAttribute('data-state')).toBe('on');
        expect(t.querySelector('.m-smeter-bar')?.getAttribute('data-receiver')).toBe('SUB');
      } finally {
        vi.mocked(getCapabilities).mockReturnValue(previous);
        vi.mocked(hasDualReceiver).mockReturnValue(false);
      }
    });

    it('the facts are display-only — bare spans, no clickable control', () => {
      const previous = getCapabilities(); const caps = capsFacts();
      receiverFacts('MAIN', caps);
      try {
        const t = mountMobile();
        const meta = t.querySelector('.m-vfo-meta')!;
        const nodes = [...meta.querySelectorAll<HTMLElement>('[data-indicator-fact]')];
        expect(nodes).toHaveLength(4);
        expect(nodes.every((node) => node.tagName === 'SPAN')).toBe(true);
        expect(nodes[0].closest('button')).toBeNull();
      } finally {
        vi.mocked(getCapabilities).mockReturnValue(previous);
        vi.mocked(hasDualReceiver).mockReturnValue(false);
      }
    });
  });

  // MOR-2511 — this suite's module-level `hasSpectrum` mock returns false, so
  // both orientations here are the no-spectrum case: no panorama slot renders
  // at all, and nothing substitutes the amber LCD cockpit in its place.
  it('renders no spectrum section in portrait when the radio has no spectrum', () => {
    const t = mountMobile();
    expect(t.querySelector('.m-spectrum')).toBeNull();
    expect(t.querySelector('.spectrum-panel-stub')).toBeNull();
    expect(t.querySelector('.amber-lcd')).toBeNull();
  });

  it('renders no spectrum stage in landscape when the radio has no spectrum', () => {
    const t = mountLandscape();
    expect(t.querySelector('.m-ls-spectrum')).toBeNull();
    expect(t.querySelector('.spectrum-panel-stub')).toBeNull();
    expect(t.querySelector('.amber-lcd')).toBeNull();
    // The VFO overlay survives without the stage element.
    expect(t.querySelector('.m-ls-overlay')).not.toBeNull();
  });
});

// MOR-2240 — phone-landscape must consume the full viewport width, and every
// scalar the mobile path mounts must render through a formatter (percent/dB),
// never the raw normalized float the bench caught on the AF slider.
describe('MobileRadioLayout landscape spans the viewport (MOR-2240)', () => {
  it('mounts the full-width landscape root, never the portrait column', () => {
    const t = mountLandscape();
    expect(t.querySelector('.m-landscape')).not.toBeNull();
    expect(t.querySelector('.m-layout')).toBeNull();
    expect(t.querySelector('.m-ls-overlay')).not.toBeNull();
  });

  // jsdom computes no CSS, so the full-width contract is pinned in the
  // component source — the same raw-source idiom "hides the AUTO toggle"
  // below applies to the SpectrumPanel branch's `hideAutoStepToggle` prop.
  it('declares the landscape surface at full viewport width', () => {
    const block = mobileLayoutSource.match(/\.m-landscape\s*\{[^}]*\}/)?.[0] ?? '';
    expect(block).toContain('width: 100dvw');
    expect(block).toContain('left: 0');
    expect(block).not.toContain('max-width');
  });

  // The RF Power modal is the only inline scalar this layout mounts on its
  // own; child panels' levels (e.g. the AF control in EssentialsPanel)
  // carry their own formatter pins. A conditional-less grep over the raw
  // source keeps the contract against any future bare float.
  it('routes every inline ValueControl through a display formatter', () => {
    const tags = [...mobileLayoutSource.matchAll(/<ValueControl[\s\S]*?\/>/g)].map((m) => m[0]);
    expect(tags.length).toBeGreaterThan(0);
    for (const tag of tags) expect(tag).toContain('displayFn=');
  });
});

// MOR-1486 ruling B (owner, session 19) — mobile has no `applyModeDefault()`
// mode-follow driver (only RadioLayout and, per ruling A, LcdLayout have
// one) and its own STEP picker is disconnected local state that can
// disagree with the shared tuning-step store on the same screen (MOR-1509).
// Both `<SpectrumPanel>` mounts (landscape + portrait) must pass
// `hideAutoStepToggle` so the toolbar never shows a toggle promising
// continuous mode-follow this layout can't provide. `hasSpectrum()` is a
// module-level `vi.fn(() => false)` mock here (not wired to a per-test
// harness), so this is proven at the source level rather than by mounting
// the spectrum branch — same idiom LcdLayout's lifecycle suite uses for its
// import-absence assertion.
describe('MobileRadioLayout hides the AUTO toggle (MOR-1486 ruling B)', () => {
  it('passes hideAutoStepToggle={true} on both SpectrumPanel mounts', () => {
    const matches = mobileLayoutSource.match(/<SpectrumPanel\b[^/]*\/>/g) ?? [];
    expect(matches.length).toBeGreaterThan(0);
    for (const tag of matches) {
      expect(tag).toContain('hideAutoStepToggle={true}');
    }
  });
});

describe('MOR-1409 local mobile meter selection', () => {
  it('changes only component-local presentation state', () => {
    tx.emitServerSnapshot({ intent: 'transmit', observedPtt: 'on', releaseRequired: true });
    const unsubscribe = subscribeRadioState(() => {});
    const t = mountMobile();
    const txChip = Array.from(t.querySelectorAll<HTMLButtonElement>('.m-chip'))
      .find((button) => button.textContent?.trim() === 'TX');
    expect(txChip).toBeDefined();
    txChip!.click();
    flushSync();

    const buttons = Array.from(t.querySelectorAll<HTMLButtonElement>('.meter-source-btn'));
    expect(buttons.map((button) => button.textContent?.trim())).toEqual(['S', 'SWR', 'Po']);
    expect(buttons[2].classList.contains('active')).toBe(true);

    const before = {
      radio: structuredClone(radio.current),
      subscribers: radioSubscriberTracker.active,
      wsFrames: wsFrameSpy.mock.calls.length,
      intents: radioIntentSpy.mock.calls.length,
      commands: tx.trace().length,
      lifecycle: JSON.parse(JSON.stringify(getCommandLifecycles())),
      txLifecycle: tx.controller.snapshot(),
    };

    for (const [index, source] of ['S', 'SWR', 'POWER'].entries()) {
      buttons[index].click();
      flushSync();
      expect(t.querySelector('.status-tag.source')?.getAttribute('data-source')).toBe(source);
      expect(buttons[index].classList.contains('active')).toBe(true);
    }

    buttons[0].click();
    flushSync();
    expect(meterBoundary.props?.meterSource).toBe('S');

    for (const forged of ['po', 'UNKNOWN'] as const) {
      // Deliberately bypass the child control's canonical buttons at the test
      // boundary: `po` is the legacy type residue and UNKNOWN is out of type.
      meterBoundary.props?.onMeterSourceChange(forged as never);
      flushSync();
      expect(meterBoundary.props?.meterSource).toBe('S');
      expect(t.querySelector('.status-tag.source')?.getAttribute('data-source')).toBe('S');
      expect(buttons[0].classList.contains('active')).toBe(true);
    }

    expect(radio.current).toEqual(before.radio);
    expect(radioSubscriberTracker.active).toBe(before.subscribers);
    expect(wsFrameSpy.mock.calls).toHaveLength(before.wsFrames);
    expect(radioIntentSpy.mock.calls).toHaveLength(before.intents);
    expect(tx.trace()).toHaveLength(before.commands);
    expect(JSON.parse(JSON.stringify(getCommandLifecycles()))).toEqual(before.lifecycle);
    expect(tx.controller.snapshot()).toEqual(before.txLifecycle);
    unsubscribe();
  });

  it('has no meter factory, Store writer, transport, intent, or lifecycle route', () => {
    expect(mobileLayoutSource).not.toContain('makeMeterHandlers');
    expect(mobileLayoutSource).not.toContain('patchRadioState');
    expect(mobileLayoutSource).not.toContain('sendCommand');
    expect(mobileLayoutSource).not.toContain('dispatchRadioIntent');
    expect(mobileLayoutSource).not.toContain('commandLifecycle');
    expect(mobileLayoutSource).toContain('meterSource={mobileMeterSource}');
    expect(mobileLayoutSource).toContain('onMeterSourceChange={selectMobileMeterSource}');
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

// MOR-2851 — the portrait phone draws no toolbar above the panorama. The
// panorama's view controls move to a SCOPE chip tab that exists exactly when
// the radio has a spectrum (the same `hasSpectrum()` gate as the panorama
// itself, MOR-2511). SpectrumPanel is stubbed here, so the panel-level
// contract (toolbar hidden, values bound) is pinned through the stub's
// recorded props; the toolbar's own rendering is pinned in
// SpectrumPanel.component.test.ts.
describe('MobileRadioLayout SCOPE chip tab (MOR-2851)', () => {
  /** Back the capability mocks with a spectrum radio for one test. */
  function withSpectrumRadio(): () => void {
    const oldCaps = getCapabilities();
    vi.mocked(hasSpectrum).mockReturnValue(true);
    // The managed scope region (VIEW's demand) exists only for a hardware
    // scope source — the same gate SemanticRadioSurfaces applies.
    vi.mocked(getScopeSource).mockReturnValue('hardware');
    vi.mocked(getCapabilities).mockReturnValue({
      ...oldCaps, model: 'fixture', receivers: 1, vfoScheme: 'ab', capabilities: ['scope'],
    } as Capabilities);
    (radio as unknown as { current: { active?: 'MAIN' | 'SUB' } | null }).current = { active: 'MAIN' };
    return () => {
      vi.mocked(hasSpectrum).mockReturnValue(false);
      vi.mocked(getScopeSource).mockReturnValue(null);
      vi.mocked(getCapabilities).mockReturnValue(oldCaps);
      (radio as unknown as { current: { active?: 'MAIN' | 'SUB' } | null }).current = null;
    };
  }

  function scopeChip(t: HTMLElement): HTMLButtonElement | undefined {
    return Array.from(t.querySelectorAll<HTMLButtonElement>('.m-chip'))
      .find((chip) => chip.textContent?.trim() === 'SCOPE');
  }

  it('renders the SCOPE chip right after BAND only when the radio has a spectrum', () => {
    const restore = withSpectrumRadio();
    try {
      const t = mountMobile();
      const labels = Array.from(t.querySelectorAll('.m-chip')).map((chip) => chip.textContent?.trim());
      expect(labels).toContain('SCOPE');
      expect(labels.indexOf('BAND') + 1).toBe(labels.indexOf('SCOPE'));
    } finally {
      restore();
    }
    // MOR-2511 no-spectrum fixture: the chip is gone with the panorama.
    const bare = mountMobile();
    expect(bare.querySelector('.m-spectrum')).toBeNull();
    expect(scopeChip(bare)).toBeUndefined();
  });

  it('hides the toolbar on the portrait panorama only (MOR-2851)', () => {
    const restore = withSpectrumRadio();
    try {
      const t = mountMobile();
      const portrait = t.querySelector<HTMLElement>('.spectrum-panel-stub')!;
      expect(portrait.dataset.hideToolbar).toBe('true');
      rotate(true);
      const landscape = t.querySelector<HTMLElement>('.spectrum-panel-stub')!;
      expect(landscape.dataset.hideToolbar).toBe('false');
    } finally {
      restore();
      rotate(false);
    }
  });

  it('opening SCOPE shows the four screen keys and the semantic scope-controls surface', () => {
    const restore = withSpectrumRadio();
    try {
      const t = mountMobile();
      scopeChip(t)!.click();
      flushSync();
      const panel = t.querySelector('#m-chip-panel-scope');
      expect(panel).not.toBeNull();
      for (const key of ['view', 'avg', 'peak', 'bands']) {
        expect(panel!.querySelector(`[data-testid="scope-key-${key}"]`)).not.toBeNull();
      }
      // The radio's scope controls: the existing semantic surface, not a
      // second implementation.
      expect(panel!.querySelector('[data-testid="scope-controls-surface"]')).not.toBeNull();
    } finally {
      restore();
    }
  });

  it('AVG / PEAK / BANDS clicks flip the values bound into SpectrumPanel', () => {
    const restore = withSpectrumRadio();
    try {
      const t = mountMobile();
      const stub = () => t.querySelector<HTMLElement>('.spectrum-panel-stub')!;
      expect(stub().dataset.enableAvg).toBe('true');
      expect(stub().dataset.enablePeakHold).toBe('true');
      expect(stub().dataset.showBandPlan).toBe('true');
      scopeChip(t)!.click();
      flushSync();
      for (const [key, attr] of [
        ['avg', 'enableAvg'], ['peak', 'enablePeakHold'], ['bands', 'showBandPlan'],
      ] as const) {
        t.querySelector<HTMLButtonElement>(`[data-testid="scope-key-${key}"]`)!.click();
        flushSync();
        expect(stub().dataset[attr]).toBe('false');
      }
    } finally {
      restore();
    }
  });

  it('renders no VIEW key when no managed scope region exists', () => {
    const restore = withSpectrumRadio();
    try {
      // A spectrum without a hardware scope source leaves
      // `managedScopeRegion` undefined — VIEW would be a dead control.
      vi.mocked(getScopeSource).mockReturnValue(null);
      const t = mountMobile();
      scopeChip(t)!.click();
      flushSync();
      const panel = t.querySelector('#m-chip-panel-scope')!;
      expect(panel.querySelector('[data-testid="scope-key-view"]')).toBeNull();
      // AVG / PEAK / BANDS are layout-owned state and stay.
      for (const key of ['avg', 'peak', 'bands']) {
        expect(panel.querySelector(`[data-testid="scope-key-${key}"]`)).not.toBeNull();
      }
    } finally {
      restore();
    }
  });

  it('VIEW drives the managed scope demand with the negation', () => {
    const restore = withSpectrumRadio();
    try {
      const t = mountMobile();
      const stub = () => t.querySelector<HTMLElement>('.spectrum-panel-stub')!;
      expect(stub().dataset.scopeDemanded).toBe('true');
      scopeChip(t)!.click();
      flushSync();
      t.querySelector<HTMLButtonElement>('[data-testid="scope-key-view"]')!.click();
      flushSync();
      expect(stub().dataset.scopeDemanded).toBe('false');
      t.querySelector<HTMLButtonElement>('[data-testid="scope-key-view"]')!.click();
      flushSync();
      expect(stub().dataset.scopeDemanded).toBe('true');
    } finally {
      restore();
    }
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

describe('mobile header follows the active receiver (MOR-2511)', () => {
  // Owner-measured state (MOR-2511, 2026-09-18): dual-RX rig with MAIN on
  // 14.074.000 USB FIL1 and SUB on 14.200.400 USB.
  const MAIN_RX: VfoStateProps = { receiver: 'main', freq: 14074000, mode: 'USB', filter: 'FIL1', sValue: 3, isActive: true, badges: {} };
  const SUB_RX: VfoStateProps = { receiver: 'sub', freq: 14200400, mode: 'USB', filter: 'FIL1', sValue: 7, isActive: false, badges: {} };
  let defaultVfo: VfoStateProps;

  beforeEach(() => {
    vi.mocked(hasDualReceiver).mockReturnValue(true);
    // The module mock returns one shared object for both receivers; these
    // tests need per-receiver projections, so capture the shared object and
    // re-point the projection for the duration of this block only.
    defaultVfo = toVfoProps(null, 'main');
    vi.mocked(toVfoProps).mockImplementation((_state, receiver) => (receiver === 'sub' ? SUB_RX : MAIN_RX));
  });

  afterEach(() => {
    vi.mocked(toVfoProps).mockImplementation(() => defaultVfo);
  });

  function setActiveReceiver(active: 'MAIN' | 'SUB') {
    (radio as unknown as { current: { active?: 'MAIN' | 'SUB' } | null }).current = { active };
  }

  it('renders SUB values in the header when SUB is the active receiver', () => {
    setActiveReceiver('SUB');
    const t = mountMobile();
    expect(t.querySelector('.m-vfo-freq')?.textContent).toBe('14200400');
    expect(t.querySelector('.m-vfo-mode')?.textContent).toBe('USB');
    // MOR-2662 (owner ruling 2026-09-26): the phone shows only ONE VFO — the
    // active one — so the other receiver's frequency is gone from the header.
    expect(t.querySelector('.m-vfo-sub')).toBeNull();
    // MOR-2816: the hoisted bar's active-receiver switch is pinned in the
    // migration suite against a dual-receiver view model — this suite's
    // runtime fabricates no semantic view here, so the bar honestly reads
    // the one mounted receiver.
    rotate(true);
    expect(t.querySelector('[data-testid="freq-echo"]')?.textContent).toBe('14200400');
  });

  it('renders MAIN values in the header when MAIN is the active receiver', () => {
    setActiveReceiver('MAIN');
    const t = mountMobile();
    expect(t.querySelector('.m-vfo-freq')?.textContent).toBe('14074000');
    expect(t.querySelector('.m-vfo-mode')?.textContent).toBe('USB');
    // MOR-2662: same ruling, the mirrored direction — no SUB frequency either.
    expect(t.querySelector('.m-vfo-sub')).toBeNull();
  });

  it('tunes the active receiver: a +1 step with SUB active dispatches receiver 1', () => {
    setActiveReceiver('SUB');
    const t = mountMobile();
    const callsBefore = radioIntentSpy.mock.calls.length;
    // Tuning strip order: [-10, -1, +1, +10]; USB keeps the 1000 Hz default
    // step (SSB_STEPS contains it), so +1 tunes SUB to 14.200.400 + 1000.
    t.querySelectorAll<HTMLButtonElement>('.m-tune-btn')[2].click();
    flushSync();
    expect(radioIntentSpy.mock.calls.length).toBe(callsBefore + 1);
    expect(radioIntentSpy.mock.lastCall).toEqual([14201400, 1, 'step']);
  });
});

// MOR-2911 (MOR-2215 audit F5) — while a set_freq is in flight the phone
// header's passive readout shows the pending target, sourced from the SAME
// accessor the desktop's SemanticRadioSurfaces seam reads
// (`getPendingFrequencyHz`), for the ACTIVE receiver only. Confirmed digits
// return when the command resolves (radio confirms or refuses — both clear
// the accessor). Driven through the real command-lifecycle store, not a
// mocked accessor, so the reactive propagation is the thing under test.
describe('mobile header shows the in-flight tune target (MOR-2911)', () => {
  function pendingEcho(t: HTMLElement): string {
    return t.querySelector('[data-testid="freq-pending-echo"]')?.textContent ?? '';
  }

  function beginSetFreq(id: string, freq: number, receiver: 0 | 1) {
    return beginCommand({
      id, name: 'set_freq', params: { freq, receiver }, originalEpoch: 9_291,
    });
  }

  it('binds the pending set_freq target for the active MAIN receiver into the header readout', () => {
    const t = mountMobile();
    expect(pendingEcho(t)).toBe('');
    expect(t.querySelector('[data-testid="freq-echo"]')?.textContent).toBe('14074000');

    const command = beginSetFreq('mor-2911-main', 14_260_100, 0);
    flushSync();

    expect(pendingEcho(t)).toBe('14260100');
    // The confirmed frequency stays the sole arithmetic/display base.
    expect(t.querySelector('[data-testid="freq-echo"]')?.textContent).toBe('14074000');

    // The real wire sequence: a transport ack first — which deliberately
    // STAYS pending (MOR-1478: an ack is not a confirming observation) —
    // then the radio's own confirmation clears the target.
    acknowledgeCommand(command.id, command.originalEpoch, command.originalEpoch);
    flushSync();
    expect(pendingEcho(t)).toBe('14260100');

    confirmCommand(command.id, command.originalEpoch, command.originalEpoch);
    flushSync();
    expect(pendingEcho(t)).toBe('');
  });

  it('tracks the SUB receiver pending target when SUB is the active receiver', () => {
    (radio as unknown as { current: { active?: 'MAIN' | 'SUB' } | null }).current = { active: 'SUB' };
    beginSetFreq('mor-2911-sub', 14_250_000, 1);
    beginSetFreq('mor-2911-other-rx', 14_111_000, 0);
    const t = mountMobile();

    expect(pendingEcho(t)).toBe('14250000');
  });

  it('returns to confirmed when the radio refuses the command', () => {
    const t = mountMobile();
    const command = beginSetFreq('mor-2911-refused', 14_260_100, 0);
    flushSync();
    expect(pendingEcho(t)).toBe('14260100');

    failCommand(command.id, command.originalEpoch, command.originalEpoch, 'radio refused');
    flushSync();
    expect(pendingEcho(t)).toBe('');
    expect(t.querySelector('[data-testid="freq-echo"]')?.textContent).toBe('14074000');
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
  const landscapeUnkeyEl = (t: HTMLElement) => t.querySelector<HTMLButtonElement>('.m-ls-unkey')!;
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

  it('ignores a press released before the FAB 50 ms hold guard closes', () => {
    const t = mountMobile();
    pointer(fabEl(t), 'pointerdown');
    vi.advanceTimersByTime(HOLD_MS - 20);
    pointer(fabEl(t), 'pointerup');
    vi.advanceTimersByTime(HOLD_MS);
    flushSync();
    expect(tx.trace()).toEqual([]);
  });

  it('short press emits WS ptt_on then one deferred WS ptt_off', () => {
    const t = mountMobile();
    fabPress(t);
    expect(tx.trace()).toEqual([{ transport: 'ws', operation: 'ptt_on' }]);
    fabRelease(t);
    vi.advanceTimersByTime(GESTURE_WINDOW_MS - 1);
    expect(tx.trace()).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(tx.trace()).toEqual([
      { transport: 'ws', operation: 'ptt_on' },
      { transport: 'ws', operation: 'ptt_off' },
    ]);
  });

  it('routes keyboard hold/release through the same WS recognizer', () => {
    const t = mountMobile();
    fabEl(t).dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    fabEl(t).dispatchEvent(new KeyboardEvent('keyup', { key: 'Enter', bubbles: true }));
    vi.advanceTimersByTime(GESTURE_WINDOW_MS);
    expect(tx.trace()).toEqual([
      { transport: 'ws', operation: 'ptt_on' },
      { transport: 'ws', operation: 'ptt_off' },
    ]);
  });

  it('double tap emits one HTTP transmit_on without a second WS lease', () => {
    const t = mountMobile();
    fabPress(t);
    fabRelease(t);
    vi.advanceTimersByTime(100);
    fabPress(t);
    expect(tx.trace()).toEqual([
      { transport: 'ws', operation: 'ptt_on' },
      { transport: 'http', operation: 'transmit_on' },
    ]);
    expect(tx.controller.snapshot().intent).toBeNull();
  });

  it('canonical latched tap emits HTTP force_off', () => {
    tx.emitServerSnapshot({ intent: 'transmit', observedPtt: 'on', releaseRequired: true });
    const t = mountMobile();
    pointer(fabEl(t), 'pointerdown');
    expect(tx.trace()).toEqual([{ transport: 'http', operation: 'force_off' }]);
  });

  it.each([
    ['RX', { intent: 'rx', observedPtt: 'off', releaseRequired: false }],
    ['PTT', { intent: 'ptt', observedPtt: 'on', releaseRequired: true }],
    ['TRANSMIT', { intent: 'transmit', observedPtt: 'on', releaseRequired: true }],
    ['failed', { intent: 'rx', observedPtt: 'unknown', releaseRequired: true, lastError: 'release failed' }],
    ['stale', { intent: 'rx', observedPtt: 'unknown', releaseRequired: true, stale: true }],
  ] as const)('landscape exposes unconditional HTTP ForceOFF while canonical state is %s', (_name, snapshot) => {
    tx.emitServerSnapshot(snapshot);
    const t = mountLandscape();
    const unkey = landscapeUnkeyEl(t);

    expect(unkey).not.toBeNull();
    expect(unkey.disabled).toBe(false);
    expect(unkey.tabIndex).toBe(0);
    unkey.focus();
    expect(document.activeElement).toBe(unkey);

    unkey.click();
    expect(tx.trace()).toEqual([{ transport: 'http', operation: 'force_off' }]);
  });

  it('canonical landscape TRANSMIT exposes exactly one actionable ForceOFF control', () => {
    tx.emitServerSnapshot({ intent: 'transmit', observedPtt: 'on', releaseRequired: true });
    const t = mountLandscape();
    const candidates = Array.from(
      t.querySelectorAll<HTMLButtonElement>('.m-ls-unkey, .m-ls-ptt'),
    );

    expect(candidates).toHaveLength(1);
    const [unkey] = candidates;
    expect(unkey).toBe(landscapeUnkeyEl(t));
    expect(unkey.type).toBe('button');
    expect(unkey.disabled).toBe(false);
    expect(unkey.tabIndex).toBe(0);
    unkey.focus();
    expect(document.activeElement).toBe(unkey);

    for (const candidate of candidates) candidate.click();
    expect(tx.trace()).toEqual([{ transport: 'http', operation: 'force_off' }]);
  });

  it('keeps the landscape-only Unkey control out of portrait presentation', () => {
    const t = mountMobile();
    expect(t.querySelector('.m-ls-unkey')).toBeNull();
    // MOR-2816: the portrait deck is gone, so neither landscape Unkey nor the
    // deck's own KEY/UNKEY pair renders in portrait.
    expect(t.querySelector('[data-testid="rx-tx-unkey"]')).toBeNull();
    expect(t.querySelector('[data-testid="rx-tx-key"]')).toBeNull();
    expect(t.querySelector('.m-semantic-deck')).toBeNull();
  });

  it('unavailable double tap emits no transmit_on and releases the WS PTT', () => {
    tx.emitStale();
    const t = mountMobile();
    fabPress(t);
    fabRelease(t);
    vi.advanceTimersByTime(100);
    fabPress(t);
    expect(tx.trace()).toEqual([
      { transport: 'ws', operation: 'ptt_on' },
      { transport: 'ws', operation: 'ptt_off' },
    ]);
  });

  it('cancel/lostcapture/up release a landscape momentary exactly once', () => {
    const t = mountLandscape();
    pointer(stripEl(t), 'pointerdown');
    pointer(stripEl(t), 'pointercancel');
    pointer(stripEl(t), 'lostpointercapture');
    pointer(stripEl(t), 'pointerup');
    vi.advanceTimersByTime(GESTURE_WINDOW_MS * 2);
    expect(tx.trace()).toEqual([
      { transport: 'ws', operation: 'ptt_on' },
      { transport: 'ws', operation: 'ptt_off' },
    ]);
  });

  it('presentation rotation preserves canonical TRANSMIT and sends nothing', () => {
    tx.emitServerSnapshot({ intent: 'transmit', observedPtt: 'on', releaseRequired: true });
    mountLandscape();
    rotate(false);
    expect(tx.trace()).toEqual([]);
    expect(tx.controller.snapshot().intent).toBe('latched');
  });

  it('destroy releases pending momentary exactly once', () => {
    const t = mountMobile();
    fabPress(t);
    fabRelease(t);
    unmount(components.pop()!);
    vi.advanceTimersByTime(GESTURE_WINDOW_MS * 2);
    expect(tx.trace()).toEqual([
      { transport: 'ws', operation: 'ptt_on' },
      { transport: 'ws', operation: 'ptt_off' },
    ]);
  });

  it('destroyed orientation generations stay inert while the survivor remains usable', () => {
    const t = mountMobile();
    pointer(fabEl(t), 'pointerdown');
    vi.advanceTimersByTime(HOLD_MS - 30);
    rotate(true);
    rotate(false);
    vi.advanceTimersByTime(HOLD_MS * 4);
    expect(tx.trace()).toEqual([]);
    fabPress(t);
    expect(tx.trace()).toEqual([{ transport: 'ws', operation: 'ptt_on' }]);
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
    expect(mobileLayoutSource).toContain('getManagedAppTxController');
    expect(mobileLayoutSource).toContain('createManagedMobilePttSurface');
    expect(mobileLayoutSource).not.toContain('createPttGesture');
  });

  it('mounts the managed TOT control only through the TxPanel fallback', () => {
    // MOR-1245: the sheet's TxPanel also suppresses its inline MOD-input
    // warning — the shell's fixed overlay is the one banner on mobile.
    expect(mobileLayoutSource).toContain('<TxPanel showManagedTotControl={true} suppressModInputTxWarning />');
    expect(mobileLayoutSource).not.toContain('import ManagedTotControl');
    expect(mobileLayoutSource).not.toContain('<ManagedTotControl');
  });
});
