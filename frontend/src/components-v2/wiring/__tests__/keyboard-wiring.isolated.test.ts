import { beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('$lib/transport/ws-client', () => ({
  sendCommand: vi.fn(),
}));
vi.mock('$lib/runtime/commands/radio-intents', async () => {
  const { sendCommand } = await import('$lib/transport/ws-client');
  return {
    dispatchRadioIntent: ({ name, params }: { name: string; params: Record<string, unknown> }) => sendCommand(name, params),
    currentControlSessionEpoch: () => 0,
  };
});

vi.mock('$lib/stores/radio.svelte', () => ({
  getActiveReceiver: vi.fn(() => null),
  getRadioState: vi.fn(() => null),
}));

vi.mock('$lib/stores/capabilities.svelte', () => ({
  getCapabilities: vi.fn(() => ({
    capabilities: ['bsr', 'preamp', 'attenuator', 'agc', 'nr', 'nb', 'notch', 'ip_plus', 'split', 'dual_rx'],
    stateContractVersion: 1,
    providerGeneration: 31,
    receivers: 2,
    vfoScheme: 'main_sub',
    freqRanges: [
      {
        start: 1,
        end: 2,
        label: 'HF',
        bands: [
          { name: '160m', start: 1800000, end: 2000000, default: 1825000, bsrCode: 1 },
          { name: '80m', start: 3500000, end: 4000000, default: 3573000, bsrCode: 2 },
        ],
      },
    ],
    attValues: [0, 6, 12],
    preValues: [0, 1, 2],
    agcModes: [1, 2, 3],
    filters: ['FIL1', 'FIL2', 'FIL3'],
    dataModeCount: 3,
  })),
  capabilitiesMatchGeneration: vi.fn(() => true),
  getControlRange: vi.fn(() => null),
}));

vi.mock('$lib/state/field-status', () => ({
  isFieldAvailable: vi.fn(() => true),
}));

vi.mock('$lib/stores/tuning.svelte', () => ({
  getTuningStep: vi.fn(() => 1_000),
  adjustTuningStep: vi.fn(),
}));

vi.mock('$lib/audio/audio-manager', () => ({
  audioManager: {
    setAudioConfig: vi.fn(),
    startRx: vi.fn(),
    stopRx: vi.fn(),
    setRxVolume: vi.fn(),
    rxEnabled: false,
  },
}));

import { sendCommand } from '$lib/transport/ws-client';
import { getRadioState, getActiveReceiver } from '$lib/stores/radio.svelte';
import { adjustTuningStep } from '$lib/stores/tuning.svelte';
import { audioManager } from '$lib/audio/audio-manager';
import { makeKeyboardHandlers } from '$lib/runtime/commands/panel-commands';

const makeAction = (action: string, params?: Record<string, unknown>) => ({
  id: `test-${action}`,
  section: 'Test',
  sequence: [],
  action,
  ...(params ? { params } : {}),
});

describe('makeKeyboardHandlers', () => {
  beforeEach(() => {
    vi.mocked(sendCommand).mockClear();
    vi.mocked(getRadioState).mockReturnValue({
      active: 'MAIN', providerGeneration: 31,
      main: {
        freqHz: 14_074_000, preamp: 1, dataMode: 2, mode: 'USB', filter: 2,
        att: 0, agc: 2, nr: false, nb: false, autoNotch: false, ipplus: false,
      },
      sub: {
        freqHz: 7_074_000, preamp: 1, dataMode: 2, mode: 'USB', filter: 2,
        att: 0, agc: 2, nr: false, nb: false, autoNotch: false, ipplus: false,
      },
    } as any);
  });

  it('cycles to the next band by index', () => {
    makeKeyboardHandlers().dispatch(makeAction('band_select', { index: 2 }));

    expect(sendCommand).toHaveBeenCalledWith('set_band', { band: 2 });
  });

  it('cycles preamp values from capabilities', () => {
    makeKeyboardHandlers().dispatch(makeAction('cycle_preamp'));

    expect(sendCommand).toHaveBeenCalledWith('set_preamp', { level: 2, receiver: 0 });
  });

  it('toggles split using radio state', () => {
    vi.mocked(getRadioState).mockReturnValue({
      active: 'MAIN', providerGeneration: 31, split: false,
      main: { freqHz: 14_074_000 }, sub: { freqHz: 7_074_000 },
    } as any);

    makeKeyboardHandlers().dispatch(makeAction('toggle_split'));

    expect(sendCommand).toHaveBeenCalledWith('set_split', { on: true });
  });

  it('cycles data mode values based on capability count', () => {
    makeKeyboardHandlers().dispatch(makeAction('cycle_data_mode'));

    expect(sendCommand).toHaveBeenCalledWith('set_data_mode', { mode: 0, receiver: 0 });
  });

  it('emits a filter-settings UI event when requested', () => {
    const listener = vi.fn();
    window.addEventListener('rigplane:open-filter-settings', listener as EventListener);

    makeKeyboardHandlers().dispatch(makeAction('open_filter_settings'));

    expect(listener).toHaveBeenCalledTimes(1);
    window.removeEventListener('rigplane:open-filter-settings', listener as EventListener);
  });

  it('tunes frequency by the current frontend step', () => {
    makeKeyboardHandlers().dispatch({ action: 'tune', params: { direction: 'up' }, id: 'tune-up', section: 'Tuning', sequence: ['ArrowRight'] });

    expect(sendCommand).toHaveBeenCalledWith('set_freq', { freq: 14_075_000, receiver: 0 });
  });

  it('adjusts the frontend tuning step without sending a backend command', () => {
    makeKeyboardHandlers().dispatch({ action: 'adjust_tuning_step', params: { direction: 'down' }, id: 'step-down', section: 'Tuning', sequence: ['ArrowDown'] });

    expect(adjustTuningStep).toHaveBeenCalledWith('down');
  });

  // Regression for #827: the keyboard path for switching the active
  // receiver must route through the same helper as the VFO click so
  // audio focus follows the new receiver.  Otherwise the operator
  // tunes MAIN but keeps hearing SUB (or vice-versa) in Dual-Watch /
  // browser-audio flows.
  it('set_active_vfo couples audio focus to the requested receiver', () => {
    vi.mocked(audioManager.setAudioConfig).mockClear();

    makeKeyboardHandlers().dispatch(makeAction('set_active_vfo', { vfo: 'SUB' }));

    expect(sendCommand).toHaveBeenCalledWith('set_vfo', { vfo: 'SUB' });
    expect(audioManager.setAudioConfig).toHaveBeenCalledWith({ focus: 'sub' });

    vi.mocked(audioManager.setAudioConfig).mockClear();

    makeKeyboardHandlers().dispatch(makeAction('set_active_vfo', { vfo: 'MAIN' }));

    expect(audioManager.setAudioConfig).toHaveBeenCalledWith({ focus: 'main' });
  });
});
