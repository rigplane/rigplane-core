import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';

const h = vi.hoisted(() => ({
  state: null as Record<string, unknown> | null,
  caps: null as Record<string, unknown> | null,
  unavailable: new Set<string>(),
  sendCommand: vi.fn((
    _name: string,
    _params: Record<string, unknown>,
    _id: string,
  ) => true),
  patchActiveReceiver: vi.fn(),
  patchRadioState: vi.fn(),
  patchReceiver: vi.fn(),
}));

vi.mock('$lib/transport/ws-client', () => ({
  getControlSession: vi.fn(() => ({ state: 'connected', epoch: 47 })),
  onCommandDelivery: vi.fn(() => () => undefined),
  onControlSessionTransition: vi.fn(() => () => undefined),
  sendCommand: h.sendCommand,
}));

vi.mock('$lib/stores/radio.svelte', () => ({
  getActiveReceiver: vi.fn(() => h.state?.active === 'SUB' ? h.state?.sub : h.state?.main),
  getRadioState: vi.fn(() => h.state),
  isRadioFieldAvailable: vi.fn((path: string) => h.state !== null && !h.unavailable.has(path)),
  patchActiveReceiver: h.patchActiveReceiver,
  patchRadioState: h.patchRadioState,
  patchReceiver: h.patchReceiver,
}));

vi.mock('$lib/state/field-status', () => ({
  isFieldAvailable: vi.fn((_state: unknown, path: string) => h.state !== null && !h.unavailable.has(path)),
}));

vi.mock('$lib/stores/capabilities.svelte', () => ({
  getCapabilities: vi.fn(() => h.caps),
  capabilitiesMatchGeneration: vi.fn((generation: unknown) =>
    Number.isSafeInteger(generation)
    && h.caps?.stateContractVersion === 1
    && h.caps?.providerGeneration === generation),
  getControlRange: vi.fn(() => null),
  hasAudioFft: vi.fn(() => false),
  hasDualReceiver: vi.fn(() => h.caps?.receivers === 2),
  hasCapability: vi.fn((name: string) => (h.caps?.capabilities as string[] | undefined)?.includes(name) ?? false),
}));

vi.mock('$lib/runtime/frontend-runtime', () => ({
  runtime: {
    get state() { return h.state; },
    get caps() { return h.caps; },
    get rxEnabled() { return false; },
    setMuted: vi.fn(), setRxLive: vi.fn(), setRxVolume: vi.fn(), setVolume: vi.fn(),
  },
}));

vi.mock('$lib/audio/audio-manager', () => ({ audioManager: { setAudioConfig: vi.fn() } }));
vi.mock('$lib/stores/tuning.svelte', () => ({ getTuningStep: vi.fn(() => 1_000) }));
vi.mock('$lib/runtime/tx-controller/app-host', () => ({
  getAppTxController: () => ({ snapshot: () => Object.freeze({ phase: 'idle', radioTx: 'off' }) }),
}));
vi.mock('$lib/runtime/adapters/radio-view-model-adapter', () => ({ toRadioViewModel: vi.fn(() => null) }));
vi.mock('$lib/runtime/adapters/qsy-history-adapter', () => ({ recordQsy: vi.fn() }));
vi.mock('$lib/runtime/props/panel-props', () => ({
  toAgcProps: vi.fn(), toModeProps: vi.fn(), toAntennaProps: vi.fn(),
  toRfFrontEndProps: vi.fn(), toRitXitProps: vi.fn(), toScanProps: vi.fn(),
  toCwProps: vi.fn(), toDspProps: vi.fn(), toTxProps: vi.fn(),
  toFilterProps: vi.fn(), toBandSelectorProps: vi.fn(), toAudioSpectrumProps: vi.fn(),
  toMemoryPanelProps: vi.fn(), toAmberTelemetryProps: vi.fn(), toVfoControlProps: vi.fn(),
}));

import { getVfoHandlers } from '$lib/runtime/adapters/panel-adapters';
import { getCommandLifecycles, resetCommandLifecycle } from '$lib/stores/commands.svelte';

function state() {
  return {
    stateContractVersion: 1,
    providerGeneration: 47,
    active: 'MAIN',
    main: { freqHz: 14_074_000, mode: 'USB', filter: 1 },
    sub: { freqHz: 7_100_000, mode: 'LSB', filter: 1 },
  };
}

// MOR-1425: `getVfoHandlers()` is a module-level singleton, so its tuning
// accumulator persists across the `it()` blocks below. Fake timers stay
// active (and only ever move forward) for the whole file: each `beforeEach`
// advances the shared clock well past the accumulator's quiet window so a
// receiver touched by a prior test starts this one cold, exactly as it did
// before MOR-1425. Re-calling `useFakeTimers()` per test would instead
// resync "now" to real wall time and cancel out the advance.
beforeAll(() => vi.useFakeTimers());
afterAll(() => vi.useRealTimers());

beforeEach(() => {
  vi.advanceTimersByTime(10_000);
  h.state = state();
  h.caps = {
    stateContractVersion: 1,
    providerGeneration: 47,
    receivers: 2,
    vfoScheme: 'main_sub',
    capabilities: ['dual_rx'],
    modes: ['USB', 'LSB'],
  };
  h.unavailable.clear();
  h.sendCommand.mockClear();
  h.patchActiveReceiver.mockClear();
  h.patchRadioState.mockClear();
  h.patchReceiver.mockClear();
  resetCommandLifecycle();
});

afterEach(() => resetCommandLifecycle());

function calls(): Array<[string, Record<string, unknown>]> {
  return h.sendCommand.mock.calls.map(([name, params]) => [name, params]);
}

function expectTypedLifecycle(): void {
  const ids = h.sendCommand.mock.calls.map((call) => call[2]);
  expect(ids.every((id) => typeof id === 'string' && id.length > 0)).toBe(true);
  expect(new Set(ids).size).toBe(ids.length);
  for (const call of h.sendCommand.mock.calls) expect(call).toHaveLength(3);
  expect(getCommandLifecycles()).toHaveLength(h.sendCommand.mock.calls.length);
  expect(h.patchActiveReceiver).not.toHaveBeenCalled();
  expect(h.patchRadioState).not.toHaveBeenCalled();
  expect(h.patchReceiver).not.toHaveBeenCalled();
  expect(calls().map(([name]) => name).some((name) => /ptt|transmit|key/i.test(name))).toBe(false);
}

describe('MOR-1409 A05b real auxiliary VFO intent seam', () => {
  it('returns the exact canonical singleton handler object through the adapter', () => {
    expect(getVfoHandlers()).toBe(getVfoHandlers());
    expect(Object.keys(getVfoHandlers())).toContain('onFreqChange');
    expect(Object.keys(getVfoHandlers())).toContain('onModeChange');
  });

  it('emits the Amber composite as exact frequency then mode with distinct non-optimistic lifecycles', () => {
    const vfo = getVfoHandlers();
    vfo.onFreqChange(7_155_000, 1);
    vfo.onModeChange('LSB', 1);
    expect(calls()).toEqual([
      ['set_freq', { freq: 7_155_000, receiver: 1 }],
      ['set_mode', { mode: 'LSB', receiver: 1 }],
    ]);
    expectTypedLifecycle();
  });

  it('emits empty-mode Amber and EiBi as one exact frequency callback each', () => {
    const vfo = getVfoHandlers();
    vfo.onFreqChange(14_250_000, 0);
    vfo.onFreqChange(7_100_500, 1);
    expect(calls()).toEqual([
      ['set_freq', { freq: 14_250_000, receiver: 0 }],
      ['set_freq', { freq: 7_100_500, receiver: 1 }],
    ]);
    expectTypedLifecycle();
  });

  it('fails closed at the inherited seam for unavailable receiver leaves or generation drift', () => {
    const vfo = getVfoHandlers();
    h.unavailable.add('main.freqHz');
    vfo.onFreqChange(14_250_000, 0);
    h.unavailable.clear();
    h.caps = { ...h.caps!, providerGeneration: 48 };
    vfo.onModeChange('USB', 0);
    expect(h.sendCommand).not.toHaveBeenCalled();
    expect(getCommandLifecycles()).toHaveLength(0);
  });
});
