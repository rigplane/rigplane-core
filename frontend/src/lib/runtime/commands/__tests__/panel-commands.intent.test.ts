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
  getControlRange: vi.fn((name: string) =>
    (h.caps?.controls as Record<string, unknown> | undefined)?.[name] ?? null),
}));

vi.mock('$lib/runtime/frontend-runtime', () => ({
  runtime: {
    rxEnabled: false,
    setMuted: vi.fn(),
    setRxLive: vi.fn(),
    setRxVolume: vi.fn(),
    setVolume: vi.fn(),
  },
}));

vi.mock('$lib/audio/audio-manager', () => ({
  audioManager: { setAudioConfig: vi.fn() },
}));

import {
  makeFilterHandlers,
  makeModeHandlers,
  makePresetHandlers,
  makeRfFrontEndHandlers,
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
    digisel: false,
    ipplus: false,
    afLevel: 50,
    rfGain: 128,
    squelch: 0,
    nb: false,
    nr: false,
    sMeter: 0,
  };
  return {
    active,
    main: { ...receiver },
    sub: { ...receiver, freqHz: 7_100_000 },
    ritOn: false,
    ritTx: true,
    ritFreq: 300,
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

describe('MOR-1409 A03a canonical RX/filter/core intent handlers', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    h.state = state();
    h.unavailable.clear();
    h.caps = {
      capabilities: ['pbt'],
      receivers: 2,
      vfoScheme: 'main_sub',
      controls: {
        pbt_inner: { raw_min: 0, raw_max: 255, raw_center: 128, display_min: -1200, display_max: 1200 },
      },
    };
    h.sendCommand.mockClear();
    h.patchActiveReceiver.mockClear();
    h.patchRadioState.mockClear();
    h.patchReceiver.mockClear();
  });

  afterEach(() => {
    resetCommandLifecycle();
    vi.useRealTimers();
  });

  it('exports the A03a1 and A03a2 canonical factories from the compatibility bus', () => {
    expect(compatibilityBus.makeModeHandlers).toBe(makeModeHandlers);
    expect(compatibilityBus.makeFilterHandlers).toBe(makeFilterHandlers);
    expect(compatibilityBus.makeRfFrontEndHandlers).toBe(makeRfFrontEndHandlers);
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

  it('keeps both deferred preset callbacks wholly on the legacy raw path', () => {
    const preset = makePresetHandlers();
    preset.onPresetSelect(14_074_000, 'USB', 2);
    preset.onFreqPreset(7_074_000, 'CW');

    expect(h.sendCommand.mock.calls).toEqual([
      ['set_freq', { freq: 14_074_000, receiver: 0 }],
      ['set_mode', { mode: 'USB', filter: 2, receiver: 0 }],
      ['set_freq', { freq: 7_074_000, receiver: 0 }],
      ['set_mode', { mode: 'CW', filter: 1, receiver: 0 }],
    ]);
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

  it('keeps raw transport out of migrated blocks and Store writers out of their implementation', () => {
    const panelSource = readFileSync(resolve(process.cwd(), 'src/lib/runtime/commands/panel-commands.ts'), 'utf8');
    const busSource = readFileSync(resolve(process.cwd(), 'src/components-v2/wiring/command-bus.ts'), 'utf8');
    const assignedNames = ['makeFilterHandlers', 'makeModeHandlers', 'makeRfFrontEndHandlers'];

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
  });
});
