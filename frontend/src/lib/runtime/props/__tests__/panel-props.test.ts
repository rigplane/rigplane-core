import { describe, expect, it } from 'vitest';

import {
  toAgcProps,
  toAmberTelemetryProps,
  toCwProps,
  toDspProps,
  toModeProps,
  toRfFrontEndProps,
  toRxAudioProps,
  toTxProps,
} from '../panel-props';

function fieldStatus(
  availability: 'available' | 'missing' | 'stale',
  observed = availability === 'available',
) {
  return {
    storePath: 'test.path',
    observed,
    freshness: availability === 'stale' ? 'stale' : availability === 'missing' ? 'unknown' : 'fresh',
    availability,
  };
}

function makeState(overrides: Record<string, unknown> = {}) {
  return {
    revision: 1,
    updatedAt: '2026-06-03T00:00:00Z',
    active: 'MAIN',
    ptt: false,
    split: false,
    dualWatch: false,
    tunerStatus: 0,
    main: {
      freqHz: 14_074_000,
      mode: 'USB',
      filter: 1,
      dataMode: 0,
      sMeter: 50,
      att: 0,
      preamp: 0,
      nb: false,
      nr: false,
      afLevel: 128,
      rfGain: 255,
      squelch: 0,
      agc: 2,
      nbLevel: 0,
      nrLevel: 0,
      autoNotch: false,
      manualNotch: false,
      agcTimeConstant: 0,
    },
    sub: {
      freqHz: 7_074_000,
      mode: 'LSB',
      filter: 1,
      dataMode: 0,
      sMeter: 20,
      att: 0,
      preamp: 0,
      nb: false,
      nr: false,
      afLevel: 128,
      rfGain: 255,
      squelch: 0,
      agc: 2,
      nbLevel: 0,
      nrLevel: 0,
      autoNotch: false,
      manualNotch: false,
      agcTimeConstant: 0,
    },
    connection: { rigConnected: true, radioReady: true, controlConnected: true },
    powerLevel: 0.5,
    micGain: 128,
    voxOn: false,
    compressorOn: false,
    compressorLevel: 0,
    monitorOn: false,
    monitorGain: 128,
    driveGain: 128,
    ...overrides,
  } as any;
}

describe('panel prop field availability', () => {
  it('defaults RF power to the normalized midpoint without state', () => {
    const props = toTxProps(null, { tx: true, capabilities: [] } as any);

    expect(props.rfPower).toBe(0.5);
  });

  it('defaults RF front-end normalized controls without state', () => {
    const props = toRfFrontEndProps(null, {
      capabilities: ['rf_gain', 'squelch'],
    } as any);

    expect(props.rfGain).toBe(1.0);
    expect(props.squelch).toBe(0.0);
  });

  it('returns normalized AF level for local and live RX audio', () => {
    const local = toRxAudioProps(
      makeState({ main: { ...makeState().main, afLevel: 0.75 } }),
      { capabilities: ['audio'] } as any,
      { muted: false, rxEnabled: false, volume: 50 },
      true,
    );
    const live = toRxAudioProps(
      makeState({ main: { ...makeState().main, afLevel: 0.75 } }),
      { capabilities: ['audio'] } as any,
      { muted: false, rxEnabled: true, volume: 50 },
      true,
    );

    expect(local.afLevel).toBe(0.75);
    expect(live.afLevel).toBe(0.5);
  });

  it('marks top-level TX controls unavailable when fieldStatus is missing or stale', () => {
    const props = toTxProps(
      makeState({
        fieldStatus: {
          powerLevel: fieldStatus('missing', false),
          micGain: fieldStatus('stale'),
          ptt: fieldStatus('available'),
        },
      }),
      { tx: true, capabilities: ['tuner', 'monitor'] } as any,
    );

    expect(props.txActive).toBe(false);
    expect(props.rfPower).toBe(0.5);
    expect(props.rfPowerAvailable).toBe(false);
    expect(props.micGainAvailable).toBe(false);
    expect(props.txActiveAvailable).toBe(true);
  });

  it('keeps observed TX controls available', () => {
    const props = toTxProps(
      makeState({
        powerLevel: 0.75,
        micGain: 90,
        fieldStatus: {
          powerLevel: fieldStatus('available'),
          micGain: fieldStatus('available'),
        },
      }),
      { tx: true, capabilities: [] } as any,
    );

    expect(props.rfPower).toBe(0.75);
    expect(props.micGain).toBe(90);
    expect(props.rfPowerAvailable).toBe(true);
    expect(props.micGainAvailable).toBe(true);
  });

  it('shows RF front-end controls that are stale-but-known and hides only missing ones', () => {
    const props = toRfFrontEndProps(
      makeState({
        fieldStatus: {
          'main.rfGain': fieldStatus('missing', false),
          'main.att': fieldStatus('stale'),
          'main.preamp': fieldStatus('available'),
        },
      }),
      {
        capabilities: ['rf_gain', 'squelch', 'attenuator', 'preamp'],
        attValues: [0, 6, 12],
        preValues: [0, 1, 2],
      } as any,
    );

    expect(props.showRfGain).toBe(false);
    expect(props.showAtt).toBe(true);
    expect(props.showPre).toBe(true);
    expect(props.att).toBe(0);
    expect(props.rfGain).toBe(255);
  });

  it('renders all four operator controls at last-known value when stale (no flap)', () => {
    const props = toRfFrontEndProps(
      makeState({
        main: {
          freqHz: 14_074_000,
          mode: 'USB',
          filter: 1,
          dataMode: 0,
          sMeter: 50,
          rfGain: 100,
          squelch: 40,
          att: 6,
          preamp: 1,
          nb: false,
          nr: false,
          afLevel: 128,
          agc: 2,
          nbLevel: 0,
          nrLevel: 0,
          autoNotch: false,
          manualNotch: false,
          agcTimeConstant: 0,
        },
        fieldStatus: {
          'main.rfGain': fieldStatus('stale'),
          'main.squelch': fieldStatus('stale'),
          'main.att': fieldStatus('stale'),
          'main.preamp': fieldStatus('stale'),
        },
      }),
      {
        capabilities: ['rf_gain', 'squelch', 'attenuator', 'preamp'],
        attValues: [0, 6, 12],
        preValues: [0, 1, 2],
      } as any,
    );

    expect(props.showRfGain).toBe(true);
    expect(props.showSquelch).toBe(true);
    expect(props.showAtt).toBe(true);
    expect(props.showPre).toBe(true);
    expect(props.rfGain).toBe(100);
    expect(props.squelch).toBe(40);
    expect(props.att).toBe(6);
    expect(props.pre).toBe(1);
  });

  it('keeps preDisabled when preamp is stale and DIGI-SEL is on', () => {
    const props = toRfFrontEndProps(
      makeState({
        main: {
          freqHz: 14_074_000,
          mode: 'USB',
          filter: 1,
          dataMode: 0,
          sMeter: 50,
          att: 0,
          preamp: 0,
          digisel: true,
          nb: false,
          nr: false,
          afLevel: 128,
          rfGain: 255,
          squelch: 0,
          agc: 2,
          nbLevel: 0,
          nrLevel: 0,
          autoNotch: false,
          manualNotch: false,
          agcTimeConstant: 0,
        },
        fieldStatus: {
          'main.preamp': fieldStatus('stale'),
        },
      }),
      {
        capabilities: ['preamp'],
        preValues: [0, 1, 2],
      } as any,
    );

    expect(props.showPre).toBe(true);
    expect(props.preDisabled).toBe(true);
    expect(props.preDisabledReason).toMatch(/DIGI-SEL/);
  });

  it('hides operator controls that were never observed (missing)', () => {
    const props = toRfFrontEndProps(
      makeState({
        fieldStatus: {
          'main.rfGain': fieldStatus('missing', false),
          'main.squelch': fieldStatus('missing', false),
          'main.att': fieldStatus('missing', false),
          'main.preamp': fieldStatus('missing', false),
        },
      }),
      {
        capabilities: ['rf_gain', 'squelch', 'attenuator', 'preamp'],
        attValues: [0, 6, 12],
        preValues: [0, 1, 2],
      } as any,
    );

    expect(props.showRfGain).toBe(false);
    expect(props.showSquelch).toBe(false);
    expect(props.showAtt).toBe(false);
    expect(props.showPre).toBe(false);
  });

  it('does not present missing AGC as the default MID mode', () => {
    const props = toAgcProps(
      makeState({
        fieldStatus: {
          'main.agc': fieldStatus('missing', false),
        },
      }),
      { capabilities: ['agc'] } as any,
    );

    expect(props.agcMode).toBe(2);
    expect(props.hasAgc).toBe(false);
  });

  it('treats stale DSP fields as unavailable controls', () => {
    const props = toDspProps(
      makeState({
        fieldStatus: {
          'main.nb': fieldStatus('stale'),
          'main.nr': fieldStatus('available'),
          'main.agcTimeConstant': fieldStatus('missing', false),
        },
      }),
      { capabilities: ['nb', 'nr'] } as any,
    );

    expect(props.hasNb).toBe(false);
    expect(props.hasNr).toBe(true);
    expect(props.hasAgcTime).toBe(false);
  });

  it('scales the raw 0-255 NR wire value down to the 0-15 slider value (MOR-490)', () => {
    // Store holds the raw CI-V wire value; the slider is 0-15.
    expect(toDspProps(makeState({ main: { nrLevel: 0 } }), null).nrLevel).toBe(0);
    expect(toDspProps(makeState({ main: { nrLevel: 128 } }), null).nrLevel).toBe(8);
    expect(toDspProps(makeState({ main: { nrLevel: 255 } }), null).nrLevel).toBe(15);
  });

  it('offsets the 0-9 NB-depth wire value up to the 1-10 slider value (MOR-498)', () => {
    // Store holds the wire value (0-9); the slider is 1-10.
    expect(toDspProps(makeState({ nbDepth: 0 }), null).nbDepth).toBe(1);
    expect(toDspProps(makeState({ nbDepth: 5 }), null).nbDepth).toBe(6);
    expect(toDspProps(makeState({ nbDepth: 9 }), null).nbDepth).toBe(10);
  });

  it('gates NB depth/width on the nb_depth control range (MOR-502)', () => {
    const withDepth = toDspProps(
      makeState(),
      { capabilities: ['nb'], controls: { nb_depth: { raw_min: 0, raw_max: 9 } } } as any,
    );
    expect(withDepth.hasNbDepth).toBe(true);
    expect(withDepth.hasNbWidth).toBe(true);

    const withoutDepth = toDspProps(
      makeState(),
      { capabilities: ['nb'], controls: { nb_level: { raw_min: 0, raw_max: 10 } } } as any,
    );
    expect(withoutDepth.hasNbDepth).toBe(false);
    expect(withoutDepth.hasNbWidth).toBe(false);

    const noCaps = toDspProps(makeState(), null);
    expect(noCaps.hasNbDepth).toBe(false);
    expect(noCaps.hasNbWidth).toBe(false);
  });

  it('derives the NB-level scale from the nb_level control range (MOR-502)', () => {
    const icom = toDspProps(
      makeState(),
      { capabilities: ['nb'], controls: { nb_level: { raw_min: 0, raw_max: 255 } } } as any,
    );
    expect(icom.nbLevelMax).toBe(255);
    expect(icom.nbLevelPercent).toBe(true);

    const ftx1 = toDspProps(makeState(), { capabilities: ['nb'], controls: {} } as any);
    expect(ftx1.nbLevelMax).toBe(10);
    expect(ftx1.nbLevelPercent).toBe(false);

    const noCaps = toDspProps(makeState(), null);
    expect(noCaps.nbLevelMax).toBe(10);
    expect(noCaps.nbLevelPercent).toBe(false);
  });
});

describe('RF front-end preamp/digisel mutex', () => {
  it('disables the preamp control while DIGI-SEL is on but keeps the panel laid out', () => {
    const props = toRfFrontEndProps(
      makeState({
        main: {
          freqHz: 14_074_000,
          mode: 'USB',
          filter: 1,
          dataMode: 0,
          sMeter: 50,
          att: 0,
          preamp: 0,
          digisel: true,
          nb: false,
          nr: false,
          afLevel: 128,
          rfGain: 255,
          squelch: 0,
          agc: 2,
          nbLevel: 0,
          nrLevel: 0,
          autoNotch: false,
          manualNotch: false,
          agcTimeConstant: 0,
        },
        fieldStatus: {
          'main.preamp': fieldStatus('available'),
        },
      }),
      {
        capabilities: ['preamp'],
        preValues: [0, 1, 2],
      } as any,
    );

    expect(props.showPre).toBe(true);
    expect(props.preDisabled).toBe(true);
    expect(props.preDisabledReason).toMatch(/DIGI-SEL/);
  });

  it('leaves the preamp control enabled while DIGI-SEL is off', () => {
    const props = toRfFrontEndProps(
      makeState({
        fieldStatus: {
          'main.preamp': fieldStatus('available'),
        },
      }),
      {
        capabilities: ['preamp'],
        preValues: [0, 1, 2],
      } as any,
    );

    expect(props.showPre).toBe(true);
    expect(props.preDisabled).toBe(false);
    expect(props.preDisabledReason).toBe('');
  });
});

describe('CW panel APF/TPF mode gating (MOR-492)', () => {
  const caps = {
    capabilities: ['cw', 'break_in', 'apf', 'twin_peak'],
  } as any;

  function cwPropsForMode(mode: string) {
    return toCwProps(makeState({ main: { mode } }), caps);
  }

  it('enables APF and disables TPF in CW', () => {
    const props = cwPropsForMode('CW');
    expect(props.apfDisabled).toBe(false);
    expect(props.tpfDisabled).toBe(true);
  });

  it('enables APF in CW-R (reverse)', () => {
    const props = cwPropsForMode('CW-R');
    expect(props.apfDisabled).toBe(false);
  });

  it('enables TPF and disables APF in RTTY', () => {
    const props = cwPropsForMode('RTTY');
    expect(props.tpfDisabled).toBe(false);
    expect(props.apfDisabled).toBe(true);
  });

  it('enables TPF in RTTY-R (reverse)', () => {
    const props = cwPropsForMode('RTTY-R');
    expect(props.tpfDisabled).toBe(false);
  });

  it('disables both APF and TPF in USB', () => {
    const props = cwPropsForMode('USB');
    expect(props.apfDisabled).toBe(true);
    expect(props.tpfDisabled).toBe(true);
  });

  it('follows the SUB receiver mode when it is active', () => {
    const props = toCwProps(
      makeState({
        active: 'SUB',
        main: { mode: 'USB' },
        sub: { mode: 'RTTY' },
      }),
      caps,
    );
    expect(props.tpfDisabled).toBe(false);
    expect(props.apfDisabled).toBe(true);
  });
});

describe('AmberTelemetry props (MOR-483: drop dead TEMP tile)', () => {
  it('surfaces vd/id raw meter values', () => {
    const props = toAmberTelemetryProps(makeState({ vdMeter: 157, idMeter: 151 }));
    expect(props.vdRaw).toBe(157);
    expect(props.idRaw).toBe(151);
  });

  it('does not expose a tempRaw field — IC-7610 has no CI-V temperature', () => {
    const props = toAmberTelemetryProps(makeState({ vdMeter: 157, idMeter: 151 }));
    expect('tempRaw' in props).toBe(false);
  });

  it('falls back to null raws when meters are absent', () => {
    const props = toAmberTelemetryProps(makeState());
    expect(props.vdRaw).toBeNull();
    expect(props.idRaw).toBeNull();
  });
});

describe('Mode panel MOD-input source (MOR-616)', () => {
  const caps = { capabilities: ['data_mode'], dataModeCount: 3 } as any;

  function modInputState(overrides: Record<string, unknown> = {}) {
    return makeState({
      dataOffModInput: 0,
      data1ModInput: 3,
      data2ModInput: 1,
      data3ModInput: 5,
      fieldStatus: {
        dataOffModInput: fieldStatus('available'),
        data1ModInput: fieldStatus('available'),
        data2ModInput: fieldStatus('available'),
        data3ModInput: fieldStatus('available'),
      },
      ...overrides,
    });
  }

  it('exposes the DATA OFF group source when data mode is off', () => {
    const props = toModeProps(modInputState(), caps);
    expect(props.modInputSource).toBe(0);
    expect(props.hasModInput).toBe(true);
  });

  it('follows the active receiver into its DATA group (D1 on SUB)', () => {
    const state = modInputState({ active: 'SUB' });
    state.sub.dataMode = 1;
    const props = toModeProps(state, caps);
    expect(props.modInputSource).toBe(3);
  });

  it('hides the control without the data_mode capability', () => {
    const props = toModeProps(modInputState(), { capabilities: [] } as any);
    expect(props.hasModInput).toBe(false);
  });

  it('hides the control while the active group is unread (missing)', () => {
    const props = toModeProps(
      modInputState({
        dataOffModInput: null,
        fieldStatus: { dataOffModInput: fieldStatus('missing', false) },
      }),
      caps,
    );
    expect(props.hasModInput).toBe(false);
    expect(props.modInputSource).toBeNull();
  });

  it('keeps a stale-but-known source visible', () => {
    const props = toModeProps(
      modInputState({ fieldStatus: { dataOffModInput: fieldStatus('stale') } }),
      caps,
    );
    expect(props.hasModInput).toBe(true);
    expect(props.modInputSource).toBe(0);
  });

  it('defaults to hidden/null when state is missing', () => {
    const props = toModeProps(null, caps);
    expect(props.hasModInput).toBe(false);
    expect(props.modInputSource).toBeNull();
  });
});
