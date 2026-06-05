import { describe, expect, it } from 'vitest';

import {
  toAgcProps,
  toDspProps,
  toRfFrontEndProps,
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
    powerLevel: 128,
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
    expect(props.rfPower).toBe(128);
    expect(props.rfPowerAvailable).toBe(false);
    expect(props.micGainAvailable).toBe(false);
    expect(props.txActiveAvailable).toBe(true);
  });

  it('keeps observed TX controls available', () => {
    const props = toTxProps(
      makeState({
        powerLevel: 200,
        micGain: 90,
        fieldStatus: {
          powerLevel: fieldStatus('available'),
          micGain: fieldStatus('available'),
        },
      }),
      { tx: true, capabilities: [] } as any,
    );

    expect(props.rfPower).toBe(200);
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
