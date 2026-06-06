import { describe, it, expect } from 'vitest';

import {
  resolveFilterModeConfig,
  toDspProps,
  toFilterProps,
  toModeProps,
  toVfoProps,
  toVfoOpsProps,
  toRxAudioProps,
} from '../state-adapter';

describe('toModeProps', () => {
  it('derives the active receiver mode and numeric data mode from state', () => {
    const props = toModeProps(
      {
        active: 'SUB',
        main: { mode: 'USB', dataMode: 0 },
        sub: { mode: 'RTTY', dataMode: 2 },
      } as any,
      {
        modes: ['USB', 'LSB', 'RTTY'],
        capabilities: ['data_mode'],
        dataModeCount: 3,
        dataModeLabels: { '0': 'OFF', '1': 'D1', '2': 'D2', '3': 'D3' },
      } as any,
    );

    expect(props.currentMode).toBe('RTTY');
    expect(props.dataMode).toBe(2);
    expect(props.hasDataMode).toBe(true);
    expect(props.dataModeCount).toBe(3);
    expect(props.dataModeLabels).toEqual({ '0': 'OFF', '1': 'D1', '2': 'D2', '3': 'D3' });
  });

  it('falls back to defaults when state or capabilities are missing', () => {
    const props = toModeProps(null, null);

    expect(props.currentMode).toBe('USB');
    expect(props.modes).toEqual(['USB', 'LSB', 'CW', 'CW-R', 'AM', 'FM', 'RTTY', 'RTTY-R', 'PSK', 'PSK-R']);
    expect(props.dataMode).toBe(0);
    expect(props.hasDataMode).toBe(false);
  });
});

describe('toVfoProps', () => {
  it('returns defaults when receiver state is missing from ServerState', () => {
    // State exists but sub receiver is undefined — must not crash
    const state = { active: 'SUB', main: { freqHz: 14074000, mode: 'USB', filter: 1, sMeter: 0, att: 0, preamp: 0, nb: false, nr: false, afLevel: 0, rfGain: 255, squelch: 0, dataMode: 0 } } as any;
    const props = toVfoProps(state, 'sub');
    expect(props.receiver).toBe('sub');
    expect(props.freq).toBe(14074000);
    expect(props.mode).toBe('USB');
    expect(props.badges).toEqual({});
  });

  it('adds a DATA badge when numeric data mode is active', () => {
    const props = toVfoProps(
      {
        active: 'MAIN',
        split: false,
        main: {
          freqHz: 14_074_000,
          mode: 'USB',
          filter: 1,
          dataMode: 3,
          sMeter: 0,
          att: 0,
          preamp: 0,
          nb: false,
          nr: false,
          afLevel: 0,
          rfGain: 0,
          squelch: 0,
        },
        sub: {
          freqHz: 7_074_000,
          mode: 'LSB',
          filter: 1,
          dataMode: 0,
          sMeter: 0,
          att: 0,
          preamp: 0,
          nb: false,
          nr: false,
          afLevel: 0,
          rfGain: 0,
          squelch: 0,
        },
      } as any,
      'main',
    );

    expect(props.badges).toMatchObject({ DATA: true });
  });
});

describe('toFilterProps', () => {
  it('resolves data-mode specific filter config for the active receiver', () => {
    const props = toFilterProps(
      {
        active: 'MAIN',
        main: { mode: 'USB', dataMode: 1, filter: 2, filterWidth: 1200, pbtInner: 0, pbtOuter: 0 },
        sub: { mode: 'LSB', dataMode: 0, filter: 1, filterWidth: 2400, pbtInner: 0, pbtOuter: 0 },
      } as any,
      {
        capabilities: ['pbt'],
        filters: ['FIL1', 'FIL2', 'FIL3'],
        filterConfig: {
          'USB-D': { defaults: [3000, 1200, 500], fixed: false, minHz: 50, maxHz: 3600, stepHz: 50 },
        },
      } as any,
    );

    expect(props.currentFilter).toBe(2);
    expect(props.filterLabels).toEqual(['FIL1', 'FIL2', 'FIL3']);
    expect(props.filterConfig?.defaults).toEqual([3000, 1200, 500]);
    expect(props.filterWidthMin).toBe(50);
    expect(props.filterWidthMax).toBe(3600);
  });

  it('derives IF shift from PBT when if_shift capability is absent', () => {
    // Both PBT values shifted positive → deriveIfShift = avg of both (non-zero)
    const props = toFilterProps(
      {
        active: 'MAIN',
        main: { mode: 'USB', dataMode: 0, filter: 1, filterWidth: 2400, pbtInner: 148, pbtOuter: 148 },
        sub: { mode: 'LSB', dataMode: 0, filter: 1, filterWidth: 2400, pbtInner: 128, pbtOuter: 128 },
      } as any,
      { capabilities: ['pbt'], filterConfig: {} } as any,
    );

    // No if_shift cap → deriveIfShift is used; both PBT shifted by same amount
    expect(props.hasPbt).toBe(true);
    expect(props.ifShift).not.toBe(0);
  });

  it('uses table-based min/max when minHz/maxHz are absent', () => {
    const props = toFilterProps(
      {
        active: 'MAIN',
        main: { mode: 'CW', dataMode: 0, filter: 1, filterWidth: 500, pbtInner: 128, pbtOuter: 128 },
        sub: { mode: 'LSB', dataMode: 0, filter: 1, filterWidth: 2400, pbtInner: 128, pbtOuter: 128 },
      } as any,
      {
        capabilities: [],
        filterConfig: {
          'CW': { defaults: [500, 250, 100], fixed: false, table: [50, 100, 250, 500, 1200] },
        },
      } as any,
    );

    expect(props.filterWidthMin).toBe(50);   // table[0]
    expect(props.filterWidthMax).toBe(1200);  // table[last]
  });
});

describe('resolveFilterModeConfig', () => {
  const filterConfig = {
    'SSB': { defaults: [2400], fixed: false },
    'SSB-D': { defaults: [3000], fixed: false },
    'CW': { defaults: [500], fixed: false },
    'RTTY': { defaults: [350], fixed: false },
    'USB': { defaults: [2400], fixed: false },
  } as any;

  it('falls back CW-R → CW', () => {
    const result = resolveFilterModeConfig({ filterConfig } as any, 'CW-R', 0);
    expect(result?.defaults).toEqual([500]);
  });

  it('falls back RTTY-R → RTTY', () => {
    const result = resolveFilterModeConfig({ filterConfig } as any, 'RTTY-R', 0);
    expect(result?.defaults).toEqual([350]);
  });

  it('SSB data mode chain: USB-D → USB → SSB-D → SSB', () => {
    const cfg = { 'SSB-D': { defaults: [3000], fixed: false }, 'SSB': { defaults: [2400], fixed: false } } as any;
    const result = resolveFilterModeConfig({ filterConfig: cfg } as any, 'USB', 1);
    expect(result?.defaults).toEqual([3000]);
  });

  it('returns null when no config matches or mode is undefined', () => {
    expect(resolveFilterModeConfig({ filterConfig: {} } as any, 'AM', 0)).toBeNull();
    expect(resolveFilterModeConfig({ filterConfig } as any, undefined, 0)).toBeNull();
  });
});

describe('toVfoOpsProps', () => {
  // IC-7610: TX = MAIN always, except in Split where TX = SUB.
  // See manual p. 3-2, 4-9 — TX does not follow the "active"
  // (selected) receiver, only the split flag.  Epic #774.
  it('derives txVfo purely from the split flag, ignoring active receiver', () => {
    const mainActiveSplit = toVfoOpsProps(
      { active: 'MAIN', split: true, dualWatch: false, mainSubTracking: false } as any, null);
    expect(mainActiveSplit.txVfo).toBe('sub');

    const subActiveSplit = toVfoOpsProps(
      { active: 'SUB', split: true, dualWatch: false, mainSubTracking: false } as any, null);
    expect(subActiveSplit.txVfo).toBe('sub');

    const mainActiveNoSplit = toVfoOpsProps(
      { active: 'MAIN', split: false, dualWatch: false, mainSubTracking: false } as any, null);
    expect(mainActiveNoSplit.txVfo).toBe('main');

    const subActiveNoSplit = toVfoOpsProps(
      { active: 'SUB', split: false, dualWatch: false, mainSubTracking: false } as any, null);
    expect(subActiveNoSplit.txVfo).toBe('main');
  });
});

describe('toRxAudioProps', () => {
  const state = { active: 'MAIN', main: { afLevel: 200 } } as any;
  const caps = { capabilities: ['audio'] } as any;

  it('returns mute when muted, live with browser volume otherwise', () => {
    expect(toRxAudioProps(state, caps, { muted: true, rxEnabled: true, volume: 80 }).monitorMode).toBe('mute');
    const live = toRxAudioProps(state, caps, { muted: false, rxEnabled: true, volume: 50 });
    expect(live.monitorMode).toBe('live');
    expect(live.afLevel).toBe(Math.round(50 / 100 * 255));
  });
});

describe('toDspProps NR-level scaling (MOR-490)', () => {
  it('scales the raw 0-255 NR wire value down to the 0-15 slider value', () => {
    // Store holds the raw CI-V wire value; the slider is 0-15.
    expect(toDspProps({ active: 'MAIN', main: { nrLevel: 0 } } as any, null).nrLevel).toBe(0);
    expect(toDspProps({ active: 'MAIN', main: { nrLevel: 128 } } as any, null).nrLevel).toBe(8);
    expect(toDspProps({ active: 'MAIN', main: { nrLevel: 255 } } as any, null).nrLevel).toBe(15);
  });
});

describe('toDspProps NB-depth offset (MOR-498)', () => {
  it('offsets the 0-9 NB-depth wire value up to the 1-10 slider value', () => {
    // Store holds the wire value (0-9); the slider is 1-10.
    expect(toDspProps({ active: 'MAIN', nbDepth: 0 } as any, null).nbDepth).toBe(1);
    expect(toDspProps({ active: 'MAIN', nbDepth: 5 } as any, null).nbDepth).toBe(6);
    expect(toDspProps({ active: 'MAIN', nbDepth: 9 } as any, null).nbDepth).toBe(10);
  });
});