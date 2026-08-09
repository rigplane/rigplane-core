import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ServerState } from '$lib/types/state';

const h = vi.hoisted(() => ({
  state: null as ServerState | null,
  unavailable: new Set<string>(),
  caps: {
    capabilities: ['pbt'],
    controls: {
      pbt_inner: { raw_min: 0, raw_max: 255, raw_center: 128, display_min: -1200, display_max: 1200 },
    },
  } as Record<string, unknown> | null,
  sendCommand: vi.fn((
    _name: string,
    _params: Record<string, unknown>,
    _id?: string,
    _options?: { optimistic: boolean },
  ) => true),
  patchActiveReceiver: vi.fn(),
  patchRadioState: vi.fn(),
  patchReceiver: vi.fn(),
  rxEnabled: false,
  setMuted: vi.fn(),
  setRxLive: vi.fn(),
  setRxVolume: vi.fn(),
  setVolume: vi.fn(),
  setAudioConfig: vi.fn(),
}));

vi.mock('$lib/transport/ws-client', () => ({
  getControlSession: vi.fn(() => ({ state: 'connected', epoch: 31 })),
  onCommandDelivery: vi.fn(() => () => undefined),
  onControlSessionTransition: vi.fn(() => () => undefined),
  sendCommand: h.sendCommand,
}));

vi.mock('$lib/stores/radio.svelte', () => ({
  getActiveReceiver: vi.fn(() => {
    if (!h.state) return null;
    return h.state.active === 'SUB' ? h.state.sub ?? null : h.state.main ?? null;
  }),
  getRadioState: vi.fn(() => h.state),
  isRadioFieldAvailable: vi.fn((path: string) => h.state !== null && !h.unavailable.has(path)),
  patchActiveReceiver: h.patchActiveReceiver,
  patchRadioState: h.patchRadioState,
  patchReceiver: h.patchReceiver,
}));

vi.mock('$lib/state/field-status', () => ({
  isFieldAvailable: vi.fn((_state: ServerState | null, path: string) =>
    h.state !== null && !h.unavailable.has(path)),
}));

vi.mock('$lib/stores/capabilities.svelte', () => ({
  getCapabilities: vi.fn(() => h.caps),
  capabilitiesMatchGeneration: vi.fn((providerGeneration: unknown) =>
    Number.isSafeInteger(providerGeneration)
    && h.caps?.stateContractVersion === 1
    && h.caps?.providerGeneration === providerGeneration),
  getControlRange: vi.fn((name: string) =>
    (h.caps?.controls as Record<string, unknown> | undefined)?.[name] ?? null),
}));

vi.mock('$lib/runtime/frontend-runtime', () => ({
  runtime: {
    get rxEnabled() { return h.rxEnabled; },
    setMuted: h.setMuted,
    setRxLive: h.setRxLive,
    setRxVolume: h.setRxVolume,
    setVolume: h.setVolume,
  },
}));

vi.mock('$lib/audio/audio-manager', () => ({
  audioManager: { setAudioConfig: h.setAudioConfig },
}));

vi.mock('$lib/stores/tuning.svelte', () => ({
  getTuningStep: vi.fn(() => 1_000),
}));

import {
  makeAgcHandlers,
  makeAntennaHandlers,
  makeAudioRoutingHandlers,
  makeBandHandlers,
  makeCwPanelHandlers,
  makeDspHandlers,
  makeFilterHandlers,
  makeModeHandlers,
  makePresetHandlers,
  makeRfFrontEndHandlers,
  makeRitXitHandlers,
  makeRxAudioHandlers,
  makeScanHandlers,
  makeTxHandlers,
  makeVfoHandlers,
  makeVoxHandlers,
  dispatchKeyboardRadioAction,
} from '../panel-commands';
import * as compatibilityBus from '$lib/../components-v2/wiring/command-bus';
import { getCommandLifecycles, resetCommandLifecycle } from '$lib/stores/commands.svelte';
import { setPendingFocus } from '$lib/radio/pending-focus';

const freshStatus = { storePath: 'x', observed: true, freshness: 'fresh', availability: 'available' };

function state(active: 'MAIN' | 'SUB' = 'MAIN'): ServerState {
  const receiver = {
    freqHz: 14_074_000,
    mode: 'USB',
    filter: 2,
    dataMode: 1,
    filterWidth: 2400,
    filterShape: 1,
    pbtInner: 128,
    pbtOuter: 128,
    ifShift: 0,
    att: 0,
    preamp: 0,
    agc: 2,
    agcTimeConstant: 4,
    digisel: false,
    ipplus: false,
    afLevel: 50 / 255,
    rfGain: 128,
    squelch: 0,
    nb: false,
    nbLevel: 3,
    nr: false,
    nrLevel: 68,
    autoNotch: false,
    manualNotch: false,
    manualNotchWidth: 1,
    apfTypeLevel: 1,
    twinPeakFilter: false,
    sMeter: 0,
  };
  return {
    stateContractVersion: 1,
    providerGeneration: 31,
    active,
    main: { ...receiver },
    sub: { ...receiver, freqHz: 7_100_000 },
    ritOn: false,
    ritTx: true,
    ritFreq: 300,
    nbDepth: 4,
    nbWidth: 2,
    notchFilter: 64,
    powerLevel: 0.5,
    split: false,
    dualWatch: false,
    mainSubTracking: false,
    micGain: 128,
    tunerStatus: 1,
    voxOn: false,
    voxGain: 50,
    antiVoxGain: 25,
    voxDelay: 4,
    compressorOn: true,
    compressorLevel: 44,
    monitorOn: false,
    monitorGain: 72,
    driveGain: 80,
    cwPitch: 600,
    keySpeed: 24,
    breakIn: 2,
    breakInDelay: 64,
    dashRatio: 0,
    txAntenna: 1,
    rxAntenna1: false,
    rxAntenna2: true,
    scanning: false,
    scanType: 0x22,
    scanResumeMode: 2,
    fieldStatus: {
      active: freshStatus,
      'main.dataMode': freshStatus,
      'sub.dataMode': freshStatus,
      data1ModInput: freshStatus,
    },
  } as unknown as ServerState;
}

function oneReceiverAbState(): ServerState {
  const current = state();
  const main = {
    ...current.main,
    vfoA: { freqHz: 14_074_000, mode: 'USB', filterNum: 2, dataMode: 1 },
    vfoB: { freqHz: 7_074_000, mode: 'CW', filterNum: 1, dataMode: 0 },
    activeSlot: 'A',
    unselectedVfo: { freqHz: 7_074_000, mode: 'CW', filterNum: 1, dataMode: 0 },
  };
  return { ...current, active: 'MAIN', main, sub: main } as ServerState;
}

function exactCalls(): Array<[string, Record<string, unknown>]> {
  return h.sendCommand.mock.calls.map(([name, params]) => [name, params]);
}

function expectIntentTransport(): void {
  for (const call of h.sendCommand.mock.calls) {
    expect(call[2]).toEqual(expect.any(String));
    expect(call[3]).toEqual({ optimistic: false });
  }
  expect(getCommandLifecycles()).toHaveLength(h.sendCommand.mock.calls.length);
}

describe('MOR-1409 A03a/A03b1 canonical receive-control intent handlers', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    h.state = state();
    h.unavailable.clear();
    h.caps = {
      capabilities: [
        'pbt', 'agc', 'rit', 'xit', 'nr', 'nb', 'notch', 'af_level',
        'tx', 'tuner', 'vox', 'compressor', 'monitor', 'drive_gain',
        'cw', 'break_in', 'apf', 'twin_peak', 'rx_antenna',
        'split', 'dual_rx', 'dual_watch', 'main_sub_tracking',
        'bsr', 'preamp', 'attenuator', 'ip_plus',
      ],
      stateContractVersion: 1,
      providerGeneration: 31,
      receivers: 2,
      antennas: 2,
      vfoScheme: 'main_sub',
      modes: ['USB', 'CW'],
      filters: ['FIL1', 'FIL2', 'FIL3'],
      dataModeCount: 3,
      preValues: [0, 1, 2],
      attValues: [0, 6, 12],
      agcModes: [1, 2, 3],
      controls: {
        pbt_inner: { raw_min: 0, raw_max: 255, raw_center: 128, display_min: -1200, display_max: 1200 },
        nb_depth: { raw_min: 0, raw_max: 9, raw_center: 0, display_min: 1, display_max: 10 },
      },
    };
    h.sendCommand.mockClear();
    h.patchActiveReceiver.mockClear();
    h.patchRadioState.mockClear();
    h.patchReceiver.mockClear();
    h.rxEnabled = false;
    h.setMuted.mockClear();
    h.setRxLive.mockClear();
    h.setRxVolume.mockClear();
    h.setVolume.mockClear();
    h.setAudioConfig.mockClear();
  });

  afterEach(() => {
    resetCommandLifecycle();
    localStorage.clear();
    vi.useRealTimers();
  });

  it('exports every landed A03a and A03b1 canonical factory from the compatibility bus', () => {
    expect(compatibilityBus.makeModeHandlers).toBe(makeModeHandlers);
    expect(compatibilityBus.makeFilterHandlers).toBe(makeFilterHandlers);
    expect(compatibilityBus.makeRfFrontEndHandlers).toBe(makeRfFrontEndHandlers);
    expect(compatibilityBus.makeAgcHandlers).toBe(makeAgcHandlers);
    expect(compatibilityBus.makeRitXitHandlers).toBe(makeRitXitHandlers);
    expect(compatibilityBus.makeDspHandlers).toBe(makeDspHandlers);
    expect(compatibilityBus.makeBandHandlers).toBe(makeBandHandlers);
    expect(compatibilityBus.makePresetHandlers).toBe(makePresetHandlers);
    expect(compatibilityBus.makeRxAudioHandlers).toBe(makeRxAudioHandlers);
    expect(compatibilityBus.makeCwPanelHandlers).toBe(makeCwPanelHandlers);
    expect(compatibilityBus.makeTxHandlers).toBe(makeTxHandlers);
    expect(compatibilityBus.makeAntennaHandlers).toBe(makeAntennaHandlers);
    expect(compatibilityBus.makeScanHandlers).toBe(makeScanHandlers);
    expect(compatibilityBus.makeAudioRoutingHandlers).toBe(makeAudioRoutingHandlers);
  });

  it('keeps audio routing local with exact storage, finite-gain, and restore semantics', () => {
    localStorage.clear();
    const handlers = makeAudioRoutingHandlers();

    expect(handlers.restoreFromStorage()).toEqual({
      focus: 'both',
      split_stereo: false,
      main_gain_db: 0,
      sub_gain_db: 0,
    });
    expect(h.setAudioConfig).not.toHaveBeenCalled();

    handlers.onFocusChange('sub');
    handlers.onSplitStereoChange(true);
    handlers.onChannelGainChange('main', Number.NaN);
    handlers.onChannelGainChange('sub', -3);

    expect(h.setAudioConfig.mock.calls).toEqual([
      [{ focus: 'sub' }],
      [{ split_stereo: true }],
      [{ main_gain_db: 0 }],
      [{ sub_gain_db: -3 }],
    ]);
    expect(localStorage.getItem('icom.audio.focus')).toBe('sub');
    expect(localStorage.getItem('icom.audio.split_stereo')).toBe('1');
    expect(localStorage.getItem('icom.audio.main_gain_db')).toBe('0');
    expect(localStorage.getItem('icom.audio.sub_gain_db')).toBe('-3');
    expect(h.sendCommand).not.toHaveBeenCalled();
    expect(h.patchActiveReceiver).not.toHaveBeenCalled();
    expect(h.patchRadioState).not.toHaveBeenCalled();
    expect(h.patchReceiver).not.toHaveBeenCalled();
    expect(getCommandLifecycles()).toHaveLength(0);

    localStorage.setItem('icom.audio.focus', 'invalid');
    localStorage.setItem('icom.audio.split_stereo', '0');
    localStorage.setItem('icom.audio.main_gain_db', 'not-finite');
    localStorage.setItem('icom.audio.sub_gain_db', '2');
    h.setAudioConfig.mockClear();

    expect(handlers.restoreFromStorage()).toEqual({
      focus: 'both',
      split_stereo: false,
      main_gain_db: 0,
      sub_gain_db: 2,
    });
    expect(h.setAudioConfig).toHaveBeenCalledExactlyOnceWith({
      split_stereo: false,
      sub_gain_db: 2,
    });
    expect(h.sendCommand).not.toHaveBeenCalled();
    expect(getCommandLifecycles()).toHaveLength(0);
  });

  it('routes mode, DATA mode, and MOD input through exact lifecycle envelopes without Store writes', () => {
    makeModeHandlers().onModeChange('CW');
    makeModeHandlers().onDataModeChange(2);
    makeModeHandlers().onModInputChange(5);

    expect(exactCalls()).toEqual([
      ['set_mode', { mode: 'CW', receiver: 0 }],
      ['set_data_mode', { mode: 2, receiver: 0 }],
      ['set_data1_mod_input', { source: 5 }],
    ]);
    expectIntentTransport();
    expect(h.patchActiveReceiver).not.toHaveBeenCalled();
    expect(h.patchRadioState).not.toHaveBeenCalled();
  });

  it('routes both preset callbacks through exact frequency-then-mode lifecycle records', () => {
    const preset = makePresetHandlers();
    preset.onPresetSelect(14_074_000, 'USB', 2);
    preset.onFreqPreset(7_074_000, 'CW');

    expect(exactCalls()).toEqual([
      ['set_freq', { freq: 14_074_000, receiver: 0 }],
      ['set_mode', { mode: 'USB', filter: 2, receiver: 0 }],
      ['set_freq', { freq: 7_074_000, receiver: 0 }],
      ['set_mode', { mode: 'CW', filter: 1, receiver: 0 }],
    ]);
    expectIntentTransport();
  });

  it('routes AGC and the complete shared RIT/XIT family through typed lifecycle without Store writes', () => {
    makeAgcHandlers().onAgcModeChange(3);
    const rit = makeRitXitHandlers();
    rit.onRitToggle();
    rit.onXitToggle();
    rit.onRitOffsetChange(450);
    rit.onXitOffsetChange(-325);
    rit.onClear();

    expect(exactCalls()).toEqual([
      ['set_agc', { mode: 3, receiver: 0 }],
      ['set_rit_status', { on: true }],
      ['set_rit_tx_status', { on: false }],
      ['set_rit_frequency', { freq: 450 }],
      ['set_rit_frequency', { freq: -325 }],
      ['set_rit_frequency', { freq: 0 }],
    ]);
    expectIntentTransport();
    expect(h.patchActiveReceiver).not.toHaveBeenCalled();
    expect(h.patchRadioState).not.toHaveBeenCalled();
  });

  it('preserves the complete DSP conversions and notch command order on typed lifecycle', () => {
    const dsp = makeDspHandlers();
    dsp.onNrModeChange(1);
    dsp.onNrLevelChange(8);
    dsp.onNbToggle(true);
    dsp.onNbLevelChange(7);
    dsp.onNotchModeChange('off');
    dsp.onNotchFreqChange(96);
    dsp.onNbDepthChange(6);
    dsp.onNbWidthChange(4);
    dsp.onManualNotchWidthChange(2);
    dsp.onAgcTimeChange(5);

    expect(exactCalls()).toEqual([
      ['set_nr', { on: true, receiver: 0 }],
      ['set_nr_level', { level: 136, receiver: 0 }],
      ['set_nb', { on: true, receiver: 0 }],
      ['set_nb_level', { level: 7, receiver: 0 }],
      ['set_auto_notch', { on: false, receiver: 0 }],
      ['set_manual_notch', { on: false, receiver: 0 }],
      ['set_notch_filter', { value: 96, receiver: 0 }],
      ['set_nb_depth', { level: 5 }],
      ['set_nb_width', { level: 4 }],
      ['set_manual_notch_width', { value: 2, receiver: 0 }],
      ['set_agc_time_constant', { value: 5, receiver: 0 }],
    ]);
    expectIntentTransport();
    expect(h.patchActiveReceiver).not.toHaveBeenCalled();
    expect(h.patchRadioState).not.toHaveBeenCalled();
  });

  it('keeps band/preset on the known physical receiver without inspecting A/B frequency topology', () => {
    h.state = state('SUB');
    makeBandHandlers().onBandSelect('60m', 5_357_000);
    makeBandHandlers().onBandSelect('20m', 14_225_000, 5);
    makePresetHandlers().onPresetSelect(7_074_000, 'CW', 1);

    expect(exactCalls()).toEqual([
      ['set_freq', { freq: 5_357_000, receiver: 1 }],
      ['set_band', { band: 5 }],
      ['set_freq', { freq: 7_074_000, receiver: 1 }],
      ['set_mode', { mode: 'CW', filter: 1, receiver: 1 }],
    ]);
    expectIntentTransport();
  });

  it('preserves browser-local RX effects while radio AF commands use observed truth and typed lifecycle', () => {
    const rxAudio = makeRxAudioHandlers();
    rxAudio.onMonitorModeChange('mute');
    rxAudio.onMonitorModeChange('local');
    rxAudio.onAfLevelChange(0.42);

    expect(exactCalls()).toEqual([
      ['set_af_level', { level: 0, receiver: 0 }],
      ['set_af_level', { level: 50 / 255, receiver: 0 }],
      ['set_af_level', { level: 0.42, receiver: 0 }],
    ]);
    expectIntentTransport();
    expect(h.setMuted).toHaveBeenNthCalledWith(1, true);
    expect(h.setMuted).toHaveBeenNthCalledWith(2, false);
    expect(h.setRxLive).toHaveBeenNthCalledWith(1, false);
    expect(h.setRxLive).toHaveBeenNthCalledWith(2, false);
    expect(h.patchActiveReceiver).not.toHaveBeenCalled();

    h.sendCommand.mockClear();
    resetCommandLifecycle();
    h.rxEnabled = true;
    rxAudio.onAfLevelChange(0.37);
    expect(h.setRxVolume).toHaveBeenCalledWith(0.37);
    expect(h.setVolume).toHaveBeenCalledWith(37);
    expect(h.sendCommand).not.toHaveBeenCalled();
  });

  it('rejects invalid normalized AF input before radio lifecycle or transport', () => {
    const rxAudio = makeRxAudioHandlers();
    for (const level of [-0.01, 1.01, 10, Number.NaN, Number.POSITIVE_INFINITY]) {
      rxAudio.onAfLevelChange(level);
    }

    expect(h.sendCommand).not.toHaveBeenCalled();
    expect(getCommandLifecycles()).toHaveLength(0);
  });

  it('rejects invalid browser-local AF input before any local or radio effect', () => {
    h.rxEnabled = true;
    const rxAudio = makeRxAudioHandlers();
    for (const level of [-0.01, 1.01, 10, Number.NaN, Number.POSITIVE_INFINITY]) {
      rxAudio.onAfLevelChange(level);
    }

    expect(h.setRxVolume).not.toHaveBeenCalled();
    expect(h.setVolume).not.toHaveBeenCalled();
    expect(h.sendCommand).not.toHaveBeenCalled();
    expect(getCommandLifecycles()).toHaveLength(0);
  });

  it('accepts exact normalized AF endpoints unchanged on radio and browser-local paths', () => {
    const rxAudio = makeRxAudioHandlers();
    rxAudio.onAfLevelChange(0);
    rxAudio.onAfLevelChange(1);

    expect(exactCalls()).toEqual([
      ['set_af_level', { level: 0, receiver: 0 }],
      ['set_af_level', { level: 1, receiver: 0 }],
    ]);
    expectIntentTransport();

    h.sendCommand.mockClear();
    resetCommandLifecycle();
    h.rxEnabled = true;
    rxAudio.onAfLevelChange(0);
    rxAudio.onAfLevelChange(1);
    expect(h.setRxVolume).toHaveBeenNthCalledWith(1, 0);
    expect(h.setRxVolume).toHaveBeenNthCalledWith(2, 1);
    expect(h.setVolume).toHaveBeenNthCalledWith(1, 0);
    expect(h.setVolume).toHaveBeenNthCalledWith(2, 100);
    expect(h.sendCommand).not.toHaveBeenCalled();
    expect(getCommandLifecycles()).toHaveLength(0);
  });

  it('never turns an invalid observed AF level into a mute or restore command', () => {
    const rxAudio = makeRxAudioHandlers();
    for (const level of [-0.01, 1.01, 10, Number.NaN, Number.POSITIVE_INFINITY]) {
      h.state = state();
      h.state!.main!.afLevel = level;
      rxAudio.onMonitorModeChange('mute');
      rxAudio.onMonitorModeChange('local');
    }

    expect(h.setMuted).toHaveBeenCalled();
    expect(h.sendCommand).not.toHaveBeenCalled();
    expect(getCommandLifecycles()).toHaveLength(0);
  });

  it('routes the complete CW factory through exact non-PTT lifecycle without optimistic truth', () => {
    const cw = makeCwPanelHandlers();
    cw.onCwPitchChange(700);
    cw.onKeySpeedChange(28);
    cw.onBreakInToggle();
    cw.onBreakInModeChange(1);
    cw.onApfChange(2);
    cw.onTwinPeakToggle();
    cw.onAutoTune();
    cw.onWpmChange(32);
    cw.onBreakInDelayChange(75);
    cw.onSidetonePitchChange(650);
    cw.onSidetoneLevelChange(44);
    cw.onReversePaddleToggle();

    expect(exactCalls()).toEqual([
      ['set_cw_pitch', { value: 700 }],
      ['set_key_speed', { speed: 28 }],
      ['set_break_in', { mode: 0 }],
      ['set_break_in', { mode: 1 }],
      ['set_apf', { mode: 2, receiver: 0 }],
      ['set_twin_peak', { on: true, receiver: 0 }],
      ['cw_auto_tune', {}],
      ['set_key_speed', { speed: 32 }],
      ['set_break_in_delay', { level: 75 }],
      ['set_cw_pitch', { value: 650 }],
      ['set_monitor_gain', { level: 44 }],
      ['set_dash_ratio', { ratio: -1 }],
    ]);
    expectIntentTransport();
    expect(h.patchActiveReceiver).not.toHaveBeenCalled();
    expect(h.patchRadioState).not.toHaveBeenCalled();
  });

  it('routes complete TX auxiliaries without PTT coupling or optimistic truth', () => {
    const tx = makeTxHandlers();
    tx.onRfPowerChange(0.42);
    tx.onMicGainChange(200);
    tx.onAtuToggle();
    tx.onAtuTune();
    tx.onVoxToggle();
    tx.onCompToggle();
    tx.onCompLevelChange(33);
    tx.onMonToggle();
    tx.onMonLevelChange(99);
    tx.onDriveGainChange(100);

    expect(exactCalls()).toEqual([
      ['set_rf_power', { level: 0.42 }],
      ['set_mic_gain', { level: 200 }],
      ['set_tuner_status', { value: 0 }],
      ['set_tuner_status', { value: 2 }],
      ['set_vox', { on: true }],
      ['set_compressor', { on: false }],
      ['set_compressor_level', { level: 33 }],
      ['set_monitor', { on: true }],
      ['set_monitor_gain', { level: 99 }],
      ['set_drive_gain', { level: 100 }],
    ]);
    expectIntentTransport();
    expect(exactCalls().map(([name]) => name)).not.toContain('ptt');
    expect(h.patchRadioState).not.toHaveBeenCalled();
  });

  it('preserves exact antenna and scan command names and params without Store truth', () => {
    const antenna = makeAntennaHandlers();
    antenna.onSelectAnt1();
    antenna.onSelectAnt2();
    antenna.onToggleRxAnt();
    const scan = makeScanHandlers();
    scan.onScanStart(0x22);
    scan.onScanStop();
    scan.onDfSpanChange(25_000);
    scan.onResumeChange(0xd2);

    expect(exactCalls()).toEqual([
      ['set_antenna_1', { on: false }],
      ['set_antenna_2', { on: true }],
      ['set_rx_antenna_ant1', { on: true }],
      ['scan_start', { type: 0x22 }],
      ['scan_stop', {}],
      ['scan_set_df_span', { span: 25_000 }],
      ['scan_set_resume', { mode: 0xd2 }],
    ]);
    expectIntentTransport();
    expect(h.patchRadioState).not.toHaveBeenCalled();
  });

  it('fails closed for stale or malformed CW, TX, and antenna-derived inputs', () => {
    h.unavailable.add('breakIn');
    makeCwPanelHandlers().onBreakInToggle();
    h.unavailable.add('main.twinPeakFilter');
    makeCwPanelHandlers().onTwinPeakToggle();
    h.unavailable.add('tunerStatus');
    makeTxHandlers().onAtuToggle();
    h.unavailable.add('voxOn');
    makeTxHandlers().onVoxToggle();
    h.unavailable.add('txAntenna');
    makeAntennaHandlers().onToggleRxAnt();
    makeTxHandlers().onRfPowerChange(Number.NaN);
    makeScanHandlers().onScanStart(1.5);

    expect(h.sendCommand).not.toHaveBeenCalled();
    expect(getCommandLifecycles()).toHaveLength(0);
  });

  it('accepts one-RX MAIN CW facts but rejects impossible physical SUB without reading VFO slots', () => {
    h.state = oneReceiverAbState();
    h.caps = {
      capabilities: ['cw', 'apf', 'twin_peak'], receivers: 1, antennas: 1, vfoScheme: 'ab',
    };
    makeCwPanelHandlers().onApfChange(2);
    expect(exactCalls()).toEqual([['set_apf', { mode: 2, receiver: 0 }]]);
    expectIntentTransport();

    h.sendCommand.mockClear();
    resetCommandLifecycle();
    h.state = { ...oneReceiverAbState(), active: 'SUB' } as ServerState;
    makeCwPanelHandlers().onApfChange(2);
    makeCwPanelHandlers().onAutoTune();
    expect(h.sendCommand).not.toHaveBeenCalled();
    expect(getCommandLifecycles()).toHaveLength(0);
  });

  it('fails closed for stale derived inputs, unsupported DSP, and impossible one-RX physical SUB', () => {
    h.unavailable.add('ritOn');
    h.unavailable.add('main.autoNotch');
    makeRitXitHandlers().onRitToggle();
    makeDspHandlers().onNotchModeChange('auto');

    h.state = { ...oneReceiverAbState(), active: 'SUB' } as ServerState;
    h.caps = { capabilities: ['agc', 'rit', 'xit', 'nr', 'nb', 'notch', 'af_level'], receivers: 1, vfoScheme: 'ab' };
    makeAgcHandlers().onAgcModeChange(2);
    makeBandHandlers().onBandSelect('60m', 5_357_000);
    makeRxAudioHandlers().onAfLevelChange(0.5);

    h.state = state();
    h.unavailable.clear();
    h.caps = { capabilities: [], receivers: 2, vfoScheme: 'main_sub' };
    makeDspHandlers().onNrModeChange(1);

    expect(h.sendCommand).not.toHaveBeenCalled();
    expect(getCommandLifecycles()).toHaveLength(0);
  });

  it('rejects pending physical SUB on one-RX A/B Selected/Unselected topology', () => {
    h.caps = { capabilities: ['pbt'], receivers: 1, vfoScheme: 'ab' };
    h.state = oneReceiverAbState();
    setPendingFocus('SUB');

    makeModeHandlers().onModeChange('CW');

    expect(h.sendCommand).not.toHaveBeenCalled();
    expect(getCommandLifecycles()).toHaveLength(0);
  });

  it('accepts pending MAIN without interpreting one-RX A/B Selected/Unselected as SUB', () => {
    h.caps = { capabilities: ['pbt'], receivers: 1, vfoScheme: 'ab' };
    h.state = oneReceiverAbState();
    setPendingFocus('MAIN');

    makeModeHandlers().onModeChange('CW');

    expect(exactCalls()).toEqual([['set_mode', { mode: 'CW', receiver: 0 }]]);
    expectIntentTransport();
  });

  it('accepts pending physical SUB only with dual-RX capabilities and fresh target mode', () => {
    h.caps = { capabilities: ['pbt'], receivers: 2, vfoScheme: 'main_sub' };
    h.state = state();
    setPendingFocus('SUB');

    makeModeHandlers().onModeChange('CW');

    expect(exactCalls()).toEqual([['set_mode', { mode: 'CW', receiver: 1 }]]);
    expectIntentTransport();
  });

  it('rejects pending physical SUB when its state or fresh mode field is absent', () => {
    h.caps = { capabilities: ['pbt'], receivers: 2, vfoScheme: 'main_sub' };
    h.state = { ...state(), sub: null } as unknown as ServerState;
    setPendingFocus('SUB');
    makeModeHandlers().onModeChange('CW');

    h.state = state();
    h.unavailable.add('sub.mode');
    setPendingFocus('SUB');
    makeModeHandlers().onModeChange('CW');

    expect(h.sendCommand).not.toHaveBeenCalled();
    expect(getCommandLifecycles()).toHaveLength(0);
  });

  it('routes every RF front-end family through exact lifecycle envelopes without Store writes', () => {
    const rf = makeRfFrontEndHandlers();
    rf.onAttChange(12);
    rf.onPreChange(2);
    rf.onRfGainChange(111);
    rf.onSquelchChange(23);
    rf.onDigiSelToggle(true);
    rf.onIpPlusToggle(false);

    expect(exactCalls()).toEqual([
      ['set_attenuator', { db: 12, receiver: 0 }],
      ['set_preamp', { level: 2, receiver: 0 }],
      ['set_rf_gain', { level: 111, receiver: 0 }],
      ['set_squelch', { level: 23, receiver: 0 }],
      ['set_digisel', { on: true, receiver: 0 }],
      ['set_ip_plus', { on: false, receiver: 0 }],
    ]);
    expectIntentTransport();
    expect(h.patchActiveReceiver).not.toHaveBeenCalled();
    expect(h.patchRadioState).not.toHaveBeenCalled();
  });

  it('accepts one-RX MAIN with A/B Selected/Unselected without inferring physical SUB', () => {
    h.caps = { capabilities: ['pbt'], receivers: 1, vfoScheme: 'ab' };
    h.state = oneReceiverAbState();

    makeRfFrontEndHandlers().onPreChange(1);

    expect(exactCalls()).toEqual([['set_preamp', { level: 1, receiver: 0 }]]);
    expectIntentTransport();
  });

  it('rejects impossible one-RX physical SUB even when a normalized sub alias exists', () => {
    h.caps = { capabilities: ['pbt'], receivers: 1, vfoScheme: 'ab' };
    h.state = { ...oneReceiverAbState(), active: 'SUB' } as ServerState;

    makeRfFrontEndHandlers().onPreChange(1);

    expect(h.sendCommand).not.toHaveBeenCalled();
    expect(getCommandLifecycles()).toHaveLength(0);
  });

  it('accepts fresh physical SUB only on a dual-receiver topology', () => {
    h.caps = { capabilities: ['pbt'], receivers: 2, vfoScheme: 'main_sub' };
    h.state = state('SUB');

    makeRfFrontEndHandlers().onIpPlusToggle(true);

    expect(exactCalls()).toEqual([['set_ip_plus', { on: true, receiver: 1 }]]);
    expectIntentTransport();
  });

  it('fails closed for invalid receiver identity and every stale RF target field', () => {
    const rf = makeRfFrontEndHandlers();
    h.state = { ...state(), active: 'B' } as unknown as ServerState;
    rf.onAttChange(6);

    h.state = state();
    h.unavailable.add('main.att');
    rf.onAttChange(6);
    h.unavailable.add('main.preamp');
    rf.onPreChange(1);
    h.unavailable.add('main.rfGain');
    rf.onRfGainChange(100);
    h.unavailable.add('main.squelch');
    rf.onSquelchChange(20);
    h.unavailable.add('main.digisel');
    rf.onDigiSelToggle(true);
    h.unavailable.add('main.ipplus');
    rf.onIpPlusToggle(true);

    expect(h.sendCommand).not.toHaveBeenCalled();
    expect(getCommandLifecycles()).toHaveLength(0);
  });

  it('fails closed instead of constructing malformed RF intent envelopes', () => {
    const rf = makeRfFrontEndHandlers();
    rf.onAttChange(1.5);
    rf.onPreChange(Number.NaN);
    rf.onRfGainChange(Number.POSITIVE_INFINITY);
    rf.onSquelchChange(2.25);
    rf.onDigiSelToggle('true' as unknown as boolean);
    rf.onIpPlusToggle(1 as unknown as boolean);

    expect(h.sendCommand).not.toHaveBeenCalled();
    expect(getCommandLifecycles()).toHaveLength(0);
  });

  it('preserves debounced filter command order without optimistic filter values', () => {
    const filter = makeFilterHandlers();
    filter.onFilterChange(3);
    filter.onFilterWidthChange(1800);
    filter.onFilterShapeChange(2);
    filter.onFilterPresetChange(1, 2800);
    vi.advanceTimersByTime(200);
    filter.onFilterDefaults([3000, 2400, 1800]);
    filter.onPbtInnerChange(100);
    filter.onPbtOuterChange(-100);
    filter.onPbtReset();

    expect(exactCalls()).toEqual([
      ['set_filter', { filter: 3, receiver: 0 }],
      ['set_filter_shape', { shape: 2, receiver: 0 }],
      ['set_filter_width', { width: 1800, receiver: 0 }],
      ['set_filter', { filter: 1, receiver: 0 }],
      ['set_filter_width', { width: 2800, receiver: 0 }],
      ['set_filter', { filter: 2, receiver: 0 }],
      ['set_filter', { filter: 1, receiver: 0 }],
      ['set_filter_width', { width: 3000, receiver: 0 }],
      ['set_filter_width', { width: 2400, receiver: 0 }],
      ['set_filter', { filter: 3, receiver: 0 }],
      ['set_filter_width', { width: 1800, receiver: 0 }],
      ['set_filter', { filter: 2, receiver: 0 }],
      ['set_pbt_inner', { value: 139, receiver: 0 }],
      ['set_pbt_outer', { value: 117, receiver: 0 }],
      ['set_pbt_inner', { value: 128, receiver: 0 }],
      ['set_pbt_outer', { value: 128, receiver: 0 }],
    ]);
    expectIntentTransport();
    expect(h.patchActiveReceiver).not.toHaveBeenCalled();
  });

  it('fails closed when receiver identity or required observed values are unavailable', () => {
    h.state = null;
    makeModeHandlers().onModeChange('AM');
    makeFilterHandlers().onFilterChange(2);
    expect(h.sendCommand).not.toHaveBeenCalled();

    h.state = state('SUB');
    h.unavailable.add('active');
    makeModeHandlers().onDataModeChange(1);
    expect(h.sendCommand).not.toHaveBeenCalled();
  });

  it('does not manufacture MOD-input groups from a stale DATA-mode reading', () => {
    h.unavailable.add('main.dataMode');
    makeModeHandlers().onModInputChange(5);

    expect(h.sendCommand).not.toHaveBeenCalled();
  });

  it('exports canonical VFO and VOX factories through the compatibility bus', () => {
    expect(compatibilityBus.makeVfoHandlers).toBe(makeVfoHandlers);
    expect(compatibilityBus.makeVoxHandlers).toBe(makeVoxHandlers);
  });

  it('routes the complete VFO family through exact typed lifecycle without Store truth', () => {
    const vfo = makeVfoHandlers();
    vfo.onSwap();
    vfo.onEqual();
    vfo.onSplitToggle();
    vfo.onMainVfoClick();
    vfo.onSubVfoClick();
    vfo.onVfoSelect('SUB', 'B');
    vfo.onMainFreqChange(14_100_000);
    vfo.onSubFreqChange(7_100_000);
    vfo.onFreqChange(18_100_000, 0);
    vfo.onModeChange('CW', 1);
    vfo.onFilterChange(3, 0);
    vfo.onDualWatchToggle(true);
    vfo.onQuickDw();
    vfo.onQuickSplit();
    vfo.onTrackingToggle(true);

    expect(exactCalls()).toEqual([
      ['vfo_swap', {}],
      ['vfo_equalize', {}],
      ['set_split', { on: true }],
      ['set_vfo', { vfo: 'MAIN' }],
      ['set_vfo', { vfo: 'SUB' }],
      ['set_vfo', { vfo: 'SUB' }],
      ['set_vfo', { vfo: 'B' }],
      ['set_freq', { freq: 14_100_000, receiver: 0 }],
      ['set_freq', { freq: 7_100_000, receiver: 1 }],
      ['set_freq', { freq: 18_100_000, receiver: 0 }],
      ['set_mode', { mode: 'CW', receiver: 1 }],
      ['set_filter', { filter: 3, receiver: 0 }],
      ['set_dual_watch', { on: true }],
      ['quick_dualwatch', {}],
      ['quick_split', {}],
      ['set_main_sub_tracking', { on: true }],
    ]);
    expectIntentTransport();
    expect(h.patchActiveReceiver).not.toHaveBeenCalled();
    expect(h.patchRadioState).not.toHaveBeenCalled();
    expect(h.patchReceiver).not.toHaveBeenCalled();
    expect(h.setAudioConfig).toHaveBeenNthCalledWith(1, { focus: 'main' });
    expect(h.setAudioConfig).toHaveBeenNthCalledWith(2, { focus: 'sub' });
    expect(h.setAudioConfig).toHaveBeenNthCalledWith(3, { focus: 'sub' });
  });

  it('preserves pending mode focus and local audio focus without patching radio truth', () => {
    const panel = document.createElement('div');
    panel.scrollIntoView = vi.fn();
    const query = vi.spyOn(document, 'querySelector').mockReturnValue(panel);

    makeVfoHandlers().onSubModeClick();
    makeModeHandlers().onModeChange('CW');

    expect(exactCalls()).toEqual([
      ['set_vfo', { vfo: 'SUB' }],
      ['set_mode', { mode: 'CW', receiver: 1 }],
    ]);
    expectIntentTransport();
    expect(h.setAudioConfig).toHaveBeenCalledExactlyOnceWith({ focus: 'sub' });
    expect(h.patchRadioState).not.toHaveBeenCalled();
    expect(panel.scrollIntoView).toHaveBeenCalled();
    query.mockRestore();
  });

  it('keeps physical MAIN orthogonal to one-RX A/B Selected/Unselected topology', () => {
    h.state = oneReceiverAbState();
    h.caps = {
      capabilities: ['split', 'tx', 'vox'], receivers: 1, vfoScheme: 'ab',
      stateContractVersion: 1, providerGeneration: 31,
    };
    const vfo = makeVfoHandlers();
    vfo.onVfoSelect('MAIN', 'B');
    vfo.onSwap();
    vfo.onEqual();
    vfo.onQuickSplit();
    vfo.onMainFreqChange(14_101_000);

    expect(exactCalls()).toEqual([
      ['set_vfo', { vfo: 'B' }],
      ['vfo_swap', {}],
      ['vfo_equalize', {}],
      ['quick_split', {}],
      ['set_freq', { freq: 14_101_000, receiver: 0 }],
    ]);
    expectIntentTransport();

    h.sendCommand.mockClear();
    resetCommandLifecycle();
    vfo.onSubVfoClick();
    vfo.onVfoSelect('SUB', 'A');
    vfo.onSubFreqChange(7_101_000);
    expect(h.sendCommand).not.toHaveBeenCalled();
    expect(h.setAudioConfig).not.toHaveBeenCalled();
    expect(getCommandLifecycles()).toHaveLength(0);
  });

  it('shares one fail-closed VOX toggle and routes standalone VOX controls through lifecycle', () => {
    const vox = makeVoxHandlers();
    vox.onVoxToggle();
    makeTxHandlers().onVoxToggle();
    vox.onVoxGainChange(77);
    vox.onAntiVoxGainChange(12);
    vox.onVoxDelayChange(5);

    expect(exactCalls()).toEqual([
      ['set_vox', { on: true }],
      ['set_vox', { on: true }],
      ['set_vox_gain', { level: 77 }],
      ['set_anti_vox_gain', { level: 12 }],
      ['set_vox_delay', { level: 5 }],
    ]);
    expectIntentTransport();
    expect(h.patchRadioState).not.toHaveBeenCalled();
  });

  it('fails closed for mismatched generation, stale toggles, invalid topology and malformed VFO/VOX inputs', () => {
    const vfo = makeVfoHandlers();
    const vox = makeVoxHandlers();
    h.caps = { ...h.caps!, providerGeneration: 99 };
    vfo.onSplitToggle();
    vfo.onMainVfoClick();
    vox.onVoxToggle();

    h.caps = { ...h.caps!, providerGeneration: 31 };
    h.unavailable.add('split');
    h.unavailable.add('voxOn');
    h.unavailable.add('voxGain');
    h.unavailable.add('antiVoxGain');
    h.unavailable.add('voxDelay');
    vfo.onSplitToggle();
    vox.onVoxToggle();
    vfo.onVfoSelect('B' as unknown as 'MAIN', 'A');
    vfo.onVfoSelect('MAIN', 'C' as unknown as 'A');
    vfo.onFreqChange(14_100_000, undefined as unknown as 0);
    vfo.onMainFreqChange(Number.NaN);
    vfo.onModeChange('', 0);
    vfo.onFilterChange(1.5, 0);
    vox.onVoxGainChange(1.5);
    vox.onAntiVoxGainChange(Number.NaN);
    vox.onVoxDelayChange(Number.POSITIVE_INFINITY);
    vox.onVoxGainChange(10);
    vox.onAntiVoxGainChange(10);
    vox.onVoxDelayChange(10);

    expect(h.sendCommand).not.toHaveBeenCalled();
    expect(h.setAudioConfig).not.toHaveBeenCalled();
    expect(getCommandLifecycles()).toHaveLength(0);
  });

  it('delegates exactly the A03d1a keyboard radio families through typed non-optimistic intent', () => {
    const actions = [
      { action: 'tune', params: { direction: 'up' } },
      { action: 'band_select', params: { index: 3 } },
      { action: 'mode_select', params: { mode: 'CW' } },
      { action: 'cycle_data_mode' },
      { action: 'cycle_filter', params: { direction: 'narrower' } },
      { action: 'cycle_preamp' }, { action: 'cycle_att' }, { action: 'cycle_agc' },
      { action: 'toggle_nr' }, { action: 'toggle_nb' },
      { action: 'toggle_auto_notch' }, { action: 'toggle_ip_plus' },
    ];

    for (const action of actions) expect(dispatchKeyboardRadioAction(action)).toBe(true);

    expect(exactCalls()).toEqual([
      ['set_freq', { freq: 14_075_000, receiver: 0 }],
      ['set_band', { band: 3 }],
      ['set_mode', { mode: 'CW', receiver: 0 }],
      ['set_data_mode', { mode: 2, receiver: 0 }],
      ['set_filter', { filter: 3, receiver: 0 }],
      ['set_preamp', { level: 1, receiver: 0 }],
      ['set_attenuator', { db: 6, receiver: 0 }],
      ['set_agc', { mode: 3, receiver: 0 }],
      ['set_nr', { on: true, receiver: 0 }],
      ['set_nb', { on: true, receiver: 0 }],
      ['set_auto_notch', { on: true, receiver: 0 }],
      ['set_ip_plus', { on: true, receiver: 0 }],
    ]);
    expectIntentTransport();
    expect(h.patchActiveReceiver).not.toHaveBeenCalled();
    expect(h.patchRadioState).not.toHaveBeenCalled();
  });

  it('delegates the complete A03d1b RIT, audio, monitor, and VFO keyboard families through typed intents', () => {
    const actions = [
      { action: 'toggle_rit' }, { action: 'toggle_xit' }, { action: 'clear_rit_xit' },
      { action: 'adjust_af_level', params: { direction: 'up' } },
      { action: 'adjust_rf_gain', params: { direction: 'down' } },
      { action: 'toggle_monitor' }, { action: 'toggle_split' }, { action: 'vfo_swap' },
      { action: 'vfo_equalize' }, { action: 'switch_active_vfo' },
      { action: 'set_active_vfo', params: { vfo: 'MAIN' } },
    ];

    for (const action of actions) expect(dispatchKeyboardRadioAction(action)).toBe(true);

    expect(exactCalls()).toEqual([
      ['set_rit_status', { on: true }],
      ['set_rit_tx_status', { on: false }],
      ['set_rit_frequency', { freq: 0 }],
      ['set_af_level', { level: 50 / 255 + 0.05, receiver: 0 }],
      ['set_rf_gain', { level: Math.round((128 / 255 - 0.05) * 255), receiver: 0 }],
      ['set_monitor', { on: true }], ['set_split', { on: true }],
      ['vfo_swap', {}], ['vfo_equalize', {}],
      ['set_vfo', { vfo: 'SUB' }], ['set_vfo', { vfo: 'MAIN' }],
    ]);
    expectIntentTransport();
    expect(h.patchActiveReceiver).not.toHaveBeenCalled();
    expect(h.patchRadioState).not.toHaveBeenCalled();
    expect(h.setAudioConfig).toHaveBeenNthCalledWith(1, { focus: 'sub' });
    expect(h.setAudioConfig).toHaveBeenNthCalledWith(2, { focus: 'main' });
  });

  it('fails closed for invalid keyboard input while leaving deferred and local actions to their current owner', () => {
    h.unavailable.add('main.freqHz');
    expect(dispatchKeyboardRadioAction({ action: 'tune', params: { direction: 'up' } })).toBe(true);
    h.unavailable.clear();
    h.caps = { ...h.caps!, providerGeneration: 99 };
    expect(dispatchKeyboardRadioAction({ action: 'cycle_preamp' })).toBe(true);
    h.caps = { ...h.caps!, providerGeneration: 31, receivers: 1, vfoScheme: 'ab' };
    h.state = { ...oneReceiverAbState(), active: 'SUB' } as ServerState;
    expect(dispatchKeyboardRadioAction({ action: 'toggle_nr' })).toBe(true);
    h.state = oneReceiverAbState();
    (h.state.main as any).preamp = 99;
    expect(dispatchKeyboardRadioAction({ action: 'cycle_preamp' })).toBe(true);
    expect(dispatchKeyboardRadioAction({ action: 'cycle_filter', params: { direction: 'sideways' } })).toBe(true);
    expect(dispatchKeyboardRadioAction({ action: 'toggle_rit' })).toBe(true);
    expect(dispatchKeyboardRadioAction({ action: 'scope_toggle_hold' })).toBe(false);
    expect(dispatchKeyboardRadioAction({ action: 'open_filter_settings' })).toBe(false);
    expect(dispatchKeyboardRadioAction({ action: 'ptt_on' })).toBe(false);
    expect(h.sendCommand).not.toHaveBeenCalled();
    expect(h.patchActiveReceiver).not.toHaveBeenCalled();
    expect(h.patchRadioState).not.toHaveBeenCalled();
    expect(getCommandLifecycles()).toHaveLength(0);
  });

  it('handles null params and non-positive observed tuning frequencies without emitting', () => {
    expect(() => dispatchKeyboardRadioAction({ action: 'tune', params: null as unknown as Record<string, unknown> })).not.toThrow();
    expect(dispatchKeyboardRadioAction({ action: 'tune', params: null as unknown as Record<string, unknown> })).toBe(true);
    expect(dispatchKeyboardRadioAction({ action: 'toggle_nr', params: null as unknown as Record<string, unknown> })).toBe(true);
    expect(h.sendCommand).not.toHaveBeenCalled();
    expect(getCommandLifecycles()).toHaveLength(0);

    for (const frequency of [0, -500]) {
      (h.state!.main as any).freqHz = frequency;
      expect(dispatchKeyboardRadioAction({ action: 'tune', params: { deltaHz: 1_000 } })).toBe(true);
      expect(dispatchKeyboardRadioAction({ action: 'band_select', params: { index: 3 } })).toBe(true);
    }

    expect(h.sendCommand).not.toHaveBeenCalled();
    expect(h.patchActiveReceiver).not.toHaveBeenCalled();
    expect(h.patchRadioState).not.toHaveBeenCalled();
    expect(getCommandLifecycles()).toHaveLength(0);
  });

  it('handles hostile keyboard params without reading accessors or reflecting deferred actions', () => {
    const getter = vi.fn(() => { throw new Error('getter executed'); });
    const accessor = Object.defineProperty({}, 'direction', { enumerable: true, get: getter });
    const prototypeTrap = new Proxy({}, { getPrototypeOf: () => { throw new Error('prototype trap executed'); } });
    const ownKeysTrap = new Proxy({}, { ownKeys: () => { throw new Error('ownKeys trap executed'); } });
    const descriptorTrap = new Proxy({}, {
      ownKeys: () => ['direction'],
      getOwnPropertyDescriptor: () => { throw new Error('descriptor trap executed'); },
    });
    const dataGet = vi.fn(() => { throw new Error('data get trap executed'); });
    const dataProxy = new Proxy({ direction: 'up' }, { get: dataGet });
    const invalid = [null, [], 1, 'up', accessor, prototypeTrap, ownKeysTrap, descriptorTrap];

    for (const params of invalid) {
      expect(() => dispatchKeyboardRadioAction({ action: 'tune', params: params as Record<string, unknown> })).not.toThrow();
      expect(dispatchKeyboardRadioAction({ action: 'tune', params: params as Record<string, unknown> })).toBe(true);
    }
    expect(getter).not.toHaveBeenCalled();
    expect(dispatchKeyboardRadioAction({ action: 'toggle_rit', params: prototypeTrap as Record<string, unknown> })).toBe(true);
    expect(h.sendCommand).not.toHaveBeenCalled();
    expect(h.patchActiveReceiver).not.toHaveBeenCalled();
    expect(h.patchRadioState).not.toHaveBeenCalled();
    expect(getCommandLifecycles()).toHaveLength(0);

    const nullPrototype = Object.assign(Object.create(null) as Record<string, unknown>, { direction: 'up' });
    expect(dispatchKeyboardRadioAction({ action: 'tune', params: { direction: 'up' } })).toBe(true);
    expect(dispatchKeyboardRadioAction({ action: 'tune', params: nullPrototype })).toBe(true);
    expect(dispatchKeyboardRadioAction({ action: 'tune', params: dataProxy })).toBe(true);
    expect(dataGet).not.toHaveBeenCalled();
    expect(exactCalls()).toEqual([
      ['set_freq', { freq: 14_075_000, receiver: 0 }],
      ['set_freq', { freq: 14_075_000, receiver: 0 }],
      ['set_freq', { freq: 14_075_000, receiver: 0 }],
    ]);
    expectIntentTransport();
  });

  it('keeps a one-receiver MAIN with A/B and Unselected facts valid for keyboard tuning', () => {
    h.state = oneReceiverAbState();
    h.caps = { ...h.caps!, receivers: 1, vfoScheme: 'ab' };

    expect(dispatchKeyboardRadioAction({ action: 'tune', params: { deltaHz: 500 } })).toBe(true);

    expect(exactCalls()).toEqual([['set_freq', { freq: 14_074_500, receiver: 0 }]]);
    expectIntentTransport();
  });

  it('turns auto-notch off without changing the independently observed manual-notch state', () => {
    (h.state!.main as any).autoNotch = true;
    (h.state!.main as any).manualNotch = true;

    expect(dispatchKeyboardRadioAction({ action: 'toggle_auto_notch' })).toBe(true);

    expect(exactCalls()).toEqual([['set_auto_notch', { on: false, receiver: 0 }]]);
    expectIntentTransport();
  });

  it('keeps raw transport out of migrated blocks and Store writers out of their implementation', () => {
    const panelSource = readFileSync(resolve(process.cwd(), 'src/lib/runtime/commands/panel-commands.ts'), 'utf8');
    const busSource = readFileSync(resolve(process.cwd(), 'src/components-v2/wiring/command-bus.ts'), 'utf8');
    const assignedNames = [
      'makeAgcHandlers', 'makeBandHandlers', 'makeDspHandlers', 'makeFilterHandlers',
      'makeModeHandlers', 'makePresetHandlers', 'makeRfFrontEndHandlers',
      'makeRitXitHandlers', 'makeRxAudioHandlers', 'makeCwPanelHandlers',
      'makeTxHandlers', 'makeAntennaHandlers', 'makeScanHandlers',
      'makeVfoHandlers', 'makeVoxHandlers', 'makeAudioRoutingHandlers',
    ];

    for (const [index, name] of assignedNames.entries()) {
      const start = panelSource.indexOf(`export function ${name}`);
      const nextStarts = assignedNames
        .slice(index + 1)
        .map((next) => panelSource.indexOf(`export function ${next}`, start + 1))
        .filter((next) => next >= 0);
      const genericNext = panelSource.indexOf('\nexport function ', start + 1);
      const end = Math.min(...[genericNext, ...nextStarts].filter((next) => next >= 0));
      const block = panelSource.slice(start, end);
      expect(block).not.toMatch(/\b(?:patchActiveReceiver|patchRadioState|patchReceiver|sendCommand)\s*\(/);
      expect(busSource).not.toContain(`export function ${name}`);
    }
    const a03aNamesStart = panelSource.indexOf('const A03A_INTENT_NAMES');
    const a03aNamesEnd = panelSource.indexOf(']);', a03aNamesStart);
    const a03aNames = panelSource.slice(a03aNamesStart, a03aNamesEnd);
    for (const name of [
      'set_attenuator', 'set_preamp', 'set_rf_gain',
      'set_squelch', 'set_digisel', 'set_ip_plus',
    ]) {
      expect(a03aNames).not.toContain(`'${name}'`);
    }
    expect(panelSource).toContain('export function makeRfFrontEndHandlers');
    expect(busSource).not.toContain('export function makeRfFrontEndHandlers');
    const rfStart = panelSource.indexOf('export function makeRfFrontEndHandlers');
    const rfEnd = panelSource.indexOf('\nexport function ', rfStart + 1);
    const rfBlock = panelSource.slice(rfStart, rfEnd);
    expect(rfBlock).not.toMatch(/\b(?:freqHz|activeSlot|vfoA|vfoB|unselectedVfo)\b/);
    expect(panelSource).toContain('dispatchRadioIntent({ name, params } as RadioIntent)');

    for (const name of [
      'set_agc', 'set_agc_time_constant', 'set_rit_status', 'set_rit_tx_status',
      'set_rit_frequency', 'set_band', 'set_freq', 'set_mode', 'set_nr',
      'set_nr_level', 'set_nb', 'set_nb_level', 'set_auto_notch',
      'set_manual_notch', 'set_notch_filter', 'set_nb_depth', 'set_nb_width',
      'set_manual_notch_width', 'set_af_level',
    ]) {
      expect(a03aNames).not.toContain(`'${name}'`);
    }

    for (const name of [
      'set_antenna_1', 'set_antenna_2', 'set_rx_antenna_ant1', 'set_rx_antenna_ant2',
      'scan_start', 'scan_stop', 'scan_set_df_span', 'scan_set_resume',
      'set_cw_pitch', 'set_key_speed', 'set_break_in', 'set_apf', 'set_twin_peak',
      'cw_auto_tune', 'set_break_in_delay', 'set_monitor_gain', 'set_dash_ratio',
      'set_rf_power', 'set_mic_gain', 'set_tuner_status', 'set_vox', 'set_compressor',
      'set_compressor_level', 'set_monitor', 'set_drive_gain',
      'set_vfo', 'vfo_swap', 'vfo_equalize', 'set_split', 'set_dual_watch',
      'quick_dualwatch', 'quick_split', 'set_main_sub_tracking',
    ]) {
      expect(a03aNames).not.toContain(`'${name}'`);
    }

    for (const factory of ['makeBandHandlers', 'makePresetHandlers']) {
      const start = panelSource.indexOf(`export function ${factory}`);
      const end = panelSource.indexOf('\nexport function ', start + 1);
      expect(panelSource.slice(start, end)).not.toMatch(/\b(?:activeSlot|vfoA|vfoB|unselectedVfo)\b/);
    }
    for (const factory of ['makeCwPanelHandlers', 'makeTxHandlers', 'makeAntennaHandlers', 'makeScanHandlers']) {
      const start = panelSource.indexOf(`export function ${factory}`);
      const end = panelSource.indexOf('\nexport function ', start + 1);
      const block = panelSource.slice(start, end);
      expect(block).not.toMatch(/\bcmd\s*\(/);
      expect(block).not.toMatch(/\b(?:activeSlot|vfoA|vfoB|unselectedVfo)\b/);
      expect(block).toContain('dispatchRadioIntent');
    }
    expect(panelSource).not.toContain('onKeyerTypeChange');
    expect(panelSource).not.toContain('set_keyer_type');
    expect(busSource).not.toContain('onKeyerTypeChange');
    expect(busSource).not.toContain('set_keyer_type');
    expect(busSource).not.toContain('export function makeVfoHandlers');
    expect(busSource).not.toContain('export function makeVoxHandlers');
    expect(panelSource).toContain('export function makeVfoHandlers');
    expect(panelSource).toContain('export function makeVoxHandlers');
    expect(busSource).not.toMatch(/function _activateReceiver\s*\(/);
    expect(busSource).not.toContain('activeReceiverParam');
    expect(busSource).not.toContain("case 'set_active_vfo'");
    for (const deferred of [
      'makeSystemHandlers', 'makeScopeControlsHandlers', 'makeKeyboardHandlers',
    ]) expect(busSource).toContain(`export function ${deferred}`);
    expect(panelSource).not.toContain('export function makeMeterHandlers');
    expect(busSource).not.toContain('export function makeMeterHandlers');
    expect(panelSource.match(/function toggleVox/g)).toHaveLength(1);
    expect(panelSource.match(/onVoxToggle:\s*toggleVox/g)).toHaveLength(2);
    expect(panelSource).not.toMatch(/dispatchRadioIntent\(\{\s*name:\s*['"]ptt(?:_on|_off)?['"]/);
  });
});
