/**
 * MOR-4690: the phone layout shows a connection (radio-link reason line,
 * link-fault veil) but offered no Connect/Disconnect action — the toggle
 * lived only in StatusBar (desktop/LCD/dual-SDR). This pins the mobile
 * SETUP-sheet toggle, wired to the same HostUi connect/disconnect command
 * the StatusBar toggle uses (in-page confirm, exactly-once dispatch):
 *   - labelled Disconnect while connected, Connect while disconnected;
 *   - click -> in-page dialog, confirm dispatches runtime.system.disconnect
 *     exactly once (UI-to-outcome), cancel never dispatches;
 *   - native window.confirm/alert are never called;
 *   - a health-owning embedding host (MOR-4306) hides Core's button.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync, tick } from 'svelte';

// -- Child component stubs (same set that lets the full layout mount) --
vi.mock('../../../components/spectrum/SpectrumPanel.svelte', async () => {
  const s = await import('./SpectrumPanelStub.svelte');
  return { default: s.default };
});
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
vi.mock('../panels/TxPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/DspPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/AgcPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/RfFrontEnd.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/RitXitPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/AntennaPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/ScanPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../panels/CwPanel.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('../KeyboardHandler.svelte', () => ({ default: function S() { return {}; } }));
vi.mock('lucide-svelte', () => {
  const S = function () { return {}; };
  return { Settings: S, ChevronLeft: S, ChevronRight: S, ChevronsLeft: S, ChevronsRight: S, Mic: S, MicOff: S, Sliders: S, Radio: S };
});
vi.mock('../../controls/value-control', () => ({
  ValueControl: function S() { return {}; },
  rawToPercentDisplay: vi.fn((v: number) => `${Math.round(v / 255 * 100)}%`),
}));
vi.mock('../vfo-layout-tokens', () => ({
  resolveVfoLayoutProfile: vi.fn(() => 'baseline'),
  vfoLayoutStyleVars: vi.fn(() => ''),
}));

// -- Store mocks --
const conn = vi.hoisted(() => ({ status: 'connected' as string }));
vi.mock('$lib/stores/connection.svelte', () => ({
  getConnectionStatus: vi.fn(() => conn.status),
  getWsConnected: vi.fn(() => conn.status === 'connected'),
  hasEverConnected: vi.fn(() => true),
  isStale: vi.fn(() => false),
  getRadioPowerOn: vi.fn(() => true),
  isAudioConnected: vi.fn(() => false),
  getRadioLinkState: vi.fn(() => 'connected'),
  getRigConnected: vi.fn(() => true),
  getRadioReady: vi.fn(() => true),
  getRadioHealth: vi.fn(() => null),
}));
const radioSubscriberTracker = vi.hoisted(() => ({ active: 0 }));
vi.mock('$lib/stores/radio.svelte', () => ({
  radio: { current: null as null | { active?: 'MAIN' | 'SUB' } | null },
  getActiveReceiver: vi.fn(),
  getRadioState: vi.fn(),
  subscribeRadioState: vi.fn((handler: (state: null) => void) => {
    handler(null);
    radioSubscriberTracker.active += 1;
    let active = true;
    return () => {
      if (!active) return;
      active = false;
      radioSubscriberTracker.active -= 1;
    };
  }),
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
  hasTx: vi.fn(() => false), hasDualReceiver: vi.fn(() => false), hasAnyScope: vi.fn(() => false),
  hasSpectrum: vi.fn(() => false), getCapabilities: vi.fn(() => ({ freqRanges: [], modes: [], filters: [] })),
  subscribeCapabilities: vi.fn((handler: (caps: unknown) => void) => {
    handler({ freqRanges: [], modes: [], filters: [] });
    return () => {};
  }),
  getKeyboardConfig: vi.fn(() => null), setCapabilities: vi.fn(), hasCapability: vi.fn(() => false),
  vfoLabel: vi.fn((s: string) => s === 'A' ? 'MAIN' : 'SUB'),
  receiverLabel: vi.fn((id: 'MAIN' | 'SUB') => id), isAudioFft: vi.fn(() => false),
  getSmeterCalibration: vi.fn(() => null), getSmeterRedline: vi.fn(() => null),
  getMeterCalibration: vi.fn(() => null), getMeterRedline: vi.fn(() => null),
  getControlRange: vi.fn(() => ({ min: 0, max: 255 })),
  getSupportedModes: vi.fn(() => ['USB']),
  getSupportedFilters: vi.fn(() => ['FIL1']),
  getAttValues: vi.fn(() => [0]), getAttLabels: vi.fn(() => ({ 0: '0dB' })),
  getPreValues: vi.fn(() => [0]), getPreLabels: vi.fn(() => ({ 0: 'OFF' })),
  getAgcModes: vi.fn(() => [0]), getAgcLabels: vi.fn(() => ({ 0: 'OFF' })),
  getVfoScheme: vi.fn(() => 'ab'), getAntennaCount: vi.fn(() => 1),
}));

// -- Wiring mocks (mount only binds handler families; spies stay quiet) --
const radioIntentSpy = vi.hoisted(() => vi.fn());
vi.mock('$lib/runtime/commands/panel-commands', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/runtime/commands/panel-commands')>();
  const n = radioIntentSpy;
  return {
    ...actual,
    makeVfoHandlers: () => ({
      onMainFreqChange: n, onSubFreqChange: n, onFreqChange: n, onVfoSwap: n, onVfoEqual: n, onReceiverSelect: n,
      onMainVfoClick: n, onSubVfoClick: n,
    }),
    makeVoxHandlers: () => ({ onVoxToggle: n, onVoxGainChange: n, onAntiVoxGainChange: n, onVoxDelayChange: n }),
    makeMeterHandlers: () => ({ onMeterSourceChange: n }), makeKeyboardHandlers: () => ({ dispatch: n }),
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
    makeCwPanelHandlers: () => ({
      onSpeedChange: n, onKeySpeedChange: n, onCwPitchChange: n,
      onBreakInDelayChange: n, onBreakInModeChange: n, onApfChange: n,
      onTwinPeakToggle: n, onReversePaddleToggle: n,
    }),
    makeAntennaHandlers: () => ({ onAntennaSelect: n }),
    makeScanHandlers: () => ({ onScanStart: n, onScanStop: n, onDfSpanChange: n, onResumeChange: n }),
    makeScopeControlsHandlers: () => ({}),
  };
});
const txHost = vi.hoisted(() => ({ current: undefined as unknown }));
vi.mock('$lib/runtime/tx-controller/managed-app-host', () => ({
  getManagedAppTxController: () => txHost.current,
}));
vi.mock('$lib/runtime/props/panel-props', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/runtime/props/panel-props')>();
  const vfo = { freq: 14074000, mode: 'USB', filter: 'FIL1', sValue: 0, badges: {}, receiver: 'main', isActive: true };
  return {
    ...actual,
    toVfoProps: vi.fn(() => vfo), toVfoOpsProps: vi.fn(() => ({ split: false, dualWatch: false })),
    toMeterProps: vi.fn(() => ({ signal: 0, rfPower: 0, swr: 0, alc: 0, txActive: false, meterSource: 'S' })),
    toModeProps: vi.fn(() => ({ currentMode: 'USB', modes: ['USB'], dataMode: 0 })),
    toFilterProps: vi.fn(() => ({ currentFilter: 1, filterLabels: ['FIL1'] })),
    toBandSelectorProps: vi.fn(() => ({ currentFreq: 14074000 })),
    toRxAudioProps: vi.fn(() => ({ afLevel: 0.5, monitorMode: 'local' })),
    toTxProps: vi.fn(() => ({ rfPower: 0.5, txActive: false, atuActive: false, atuTuning: false })),
    toRfFrontEndProps: vi.fn(() => ({ att: 0, preamp: 0, rfGain: 1 })),
    toAgcProps: vi.fn(() => ({ agcMode: 0 })), toRitXitProps: vi.fn(() => ({ ritOn: false, ritOffset: 0, xitOn: false, xitOffset: 0 })),
    toDspProps: vi.fn(() => ({ nr: false, nb: false, notch: false })), toCwProps: vi.fn(() => ({ speed: 20 })),
    toAntennaProps: vi.fn(() => ({ selected: 1 })),
    toScanProps: vi.fn(() => ({ scanning: false, scanType: 'off', scanResumeMode: 'time' })),
  };
});

import MobileRadioLayout from '../MobileRadioLayout.svelte';
import LocalExtensionsHost from '$lib/local-extensions/LocalExtensionsHost.svelte';
import { runtime } from '$lib/runtime';
import { ManagedAppTxHarness } from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';

describe('MobileRadioLayout connection toggle (MOR-4690)', () => {
  let targets: HTMLElement[] = [];
  let instances: object[] = [];
  let confirmSpy: ReturnType<typeof vi.fn>;
  let alertSpy: ReturnType<typeof vi.fn>;
  let disconnectSpy: ReturnType<typeof vi.spyOn>;
  let connectSpy: ReturnType<typeof vi.spyOn>;

  function render(): HTMLElement {
    const target = document.createElement('div');
    document.body.appendChild(target);
    targets.push(target);
    instances.push(mount(MobileRadioLayout, { target }) as object);
    instances.push(mount(LocalExtensionsHost, { target }) as object);
    flushSync();
    return target;
  }

  /** The SETUP sheet hosts power and (MOR-4690) the connection toggle. */
  function openSetup(host: HTMLElement): void {
    const gear = host.querySelector<HTMLButtonElement>('button[aria-label="Setup"]');
    expect(gear, 'expected the SETUP gear in the sticky header').not.toBeNull();
    gear!.click();
    flushSync();
  }

  function connectionButton(host: HTMLElement, label: string): HTMLButtonElement {
    const btn = [...host.querySelectorAll<HTMLButtonElement>('button')].find(
      (b) => b.textContent?.trim() === label,
    );
    expect(btn, `expected the connection toggle labelled ${label}`).toBeDefined();
    return btn!;
  }

  function dialog(host: HTMLElement): Element | null {
    return host.querySelector('[role="alertdialog"]');
  }

  function dialogButton(host: HTMLElement, testid: string): HTMLButtonElement {
    const btn = host.querySelector<HTMLButtonElement>(`[data-testid="${testid}"]`);
    expect(btn, `expected the dialog button ${testid} to render`).not.toBeNull();
    return btn!;
  }

  function setViewport() {
    Object.defineProperty(window, 'innerWidth', { writable: true, configurable: true, value: 390 });
    Object.defineProperty(window, 'innerHeight', { writable: true, configurable: true, value: 844 });
  }

  let tx: ManagedAppTxHarness;

  beforeEach(() => {
    setViewport();
    tx = new ManagedAppTxHarness();
    txHost.current = tx.controller;
    conn.status = 'connected';
    confirmSpy = vi.fn(() => true);
    alertSpy = vi.fn();
    vi.stubGlobal('confirm', confirmSpy);
    vi.stubGlobal('alert', alertSpy);
    disconnectSpy = vi.spyOn(runtime.system, 'disconnect').mockResolvedValue(undefined);
    connectSpy = vi.spyOn(runtime.system, 'connect').mockImplementation(() => {});
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    disconnectSpy.mockRestore();
    connectSpy.mockRestore();
    document.head.querySelector('meta[name="rigplane-local-supervisor"]')?.remove();
    for (const instance of instances) unmount(instance);
    instances = [];
    for (const target of targets) target.remove();
    targets = [];
    expect(tx.listenerCount()).toBe(0);
  });

  it('connected: SETUP shows Disconnect; confirm dispatches runtime.system.disconnect once', async () => {
    conn.status = 'connected';
    const host = render();
    openSetup(host);
    connectionButton(host, 'Disconnect').click();
    await tick();

    const dlg = dialog(host);
    expect(dlg, 'expected the in-page confirm dialog to open').not.toBeNull();
    expect(dlg!.textContent).toContain('Disconnect?');
    expect(disconnectSpy).not.toHaveBeenCalled();

    dialogButton(host, 'confirm-dialog-confirm').click();
    await vi.waitFor(() => expect(disconnectSpy).toHaveBeenCalledTimes(1));
    expect(connectSpy).not.toHaveBeenCalled();
    expect(confirmSpy).not.toHaveBeenCalled();
    expect(alertSpy).not.toHaveBeenCalled();
  });

  it('connected: cancel never disconnects', async () => {
    conn.status = 'connected';
    const host = render();
    openSetup(host);
    connectionButton(host, 'Disconnect').click();
    await tick();
    expect(dialog(host)).not.toBeNull();

    dialogButton(host, 'confirm-dialog-cancel').click();
    await tick();
    expect(dialog(host)).toBeNull();
    expect(disconnectSpy).not.toHaveBeenCalled();
    expect(connectSpy).not.toHaveBeenCalled();
  });

  it('disconnected: SETUP shows Connect; confirm dispatches runtime.system.connect once', async () => {
    conn.status = 'disconnected';
    const host = render();
    openSetup(host);
    connectionButton(host, 'Connect').click();
    await tick();

    expect(dialog(host)?.textContent).toContain('Connect?');
    expect(connectSpy).not.toHaveBeenCalled();

    dialogButton(host, 'confirm-dialog-confirm').click();
    await vi.waitFor(() => expect(connectSpy).toHaveBeenCalledTimes(1));
    expect(disconnectSpy).not.toHaveBeenCalled();
  });

  it('health-owning host: no Core connection button (MOR-4306 contract holds on mobile)', () => {
    conn.status = 'connected';
    const meta = document.createElement('meta');
    meta.name = 'rigplane-local-supervisor';
    meta.content = JSON.stringify({ version: 1, report: false, language: false, health: true });
    document.head.appendChild(meta);
    const host = render();
    openSetup(host);
    const labels = [...host.querySelectorAll('button')].map((b) => b.textContent?.trim());
    expect(labels).not.toContain('Disconnect');
    expect(labels).not.toContain('Connect');
  });
});
