import { describe, expect, it } from 'vitest';

import {
  toAgcProps,
  toAmberTelemetryProps,
  toAntennaProps,
  toAudioSpectrumProps,
  toBandSelectorProps,
  toCwProps,
  toDspProps,
  toFilterProps,
  toMemoryPanelProps,
  toMeterProps,
  toModeProps,
  toRfFrontEndProps,
  toRitXitProps,
  toRxAudioProps,
  toScanProps,
  toTxProps,
  toVfoProps,
} from '../panel-props';
import { findActiveBand } from '$lib/radio/band-plan';
import type { FreqRange } from '$lib/types/capabilities';

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
    // A11 (MOR-1409): this assertion previously pinned the exact bug its own
    // title disclaims — `agcMode` read back the fabricated MID (2) default
    // even though the field was never observed. `hasAgc` gated the control's
    // visibility, but the raw value underneath was still a lie. Now the
    // value itself is unknown (NaN) whenever the field is not `available`,
    // matching every other batch-A family.
    const props = toAgcProps(
      makeState({
        fieldStatus: {
          'main.agc': fieldStatus('missing', false),
        },
      }),
      { capabilities: ['agc'] } as any,
    );

    expect(props.agcMode).toBeNaN();
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

/**
 * A11 (MOR-1409, Core #2317) — batch-A projections stop fabricating a
 * plausible-looking default (14.074 MHz / USB / FIL1 / 2400 Hz / AGC MID /
 * one antenna) for missing/unsupported input. Every case below documents a
 * value that panel-props.ts used to invent out of thin air on exact base;
 * after the fix each one renders a value that cannot be mistaken for a real
 * reading (`NaN` for numbers whose type stays `number`, `'---'` for strings
 * whose type stays `string` — the same non-fabricating-sentinel convention
 * `toVfoControlProps` already used for `mode` before this gate touched
 * anything), never `null`/`undefined` (the prop type contracts are frozen —
 * no fourth production file may be touched to accommodate a wider type).
 */
describe('A11 — batch-A projections do not fabricate defaults (MOR-1409)', () => {
  describe('toVfoProps', () => {
    it('does not invent 14.074 MHz / USB / FIL1 when state is entirely absent', () => {
      const props = toVfoProps(null, 'main');
      expect(props.freq).toBeNaN();
      expect(props.mode).toBe('---');
      expect(props.filter).toBe('---');
    });

    it('does not invent 14.074 MHz / USB / FIL1 when the addressed receiver is absent from a present state', () => {
      const props = toVfoProps(makeState({ sub: undefined }), 'sub');
      expect(props.freq).toBeNaN();
      expect(props.mode).toBe('---');
      expect(props.filter).toBe('---');
    });

    it('still reports the real value for a populated receiver', () => {
      const props = toVfoProps(makeState(), 'main');
      expect(props.freq).toBe(14_074_000);
      expect(props.mode).toBe('USB');
    });
  });

  describe('toBandSelectorProps', () => {
    const HF_RANGES: FreqRange[] = [
      {
        start: 1_800_000,
        end: 30_000_000,
        label: 'HF',
        bands: [
          { name: '20m', start: 14_000_000, end: 14_350_000, default: 14_225_000 },
        ],
      },
    ];

    it('does not invent 14.074 MHz when state is absent', () => {
      const props = toBandSelectorProps(null);
      expect(props.currentFreq).toBeNaN();
    });

    it('the fabricated default used to resolve to a real "20m" band tab — the fix must not, at the consumer boundary', () => {
      // Proven at findActiveBand (BandSelector.svelte's own consumer call),
      // not merely as a raw-literal unit assertion: on exact base,
      // toBandSelectorProps(null).currentFreq (14074000) sits inside 20m's
      // 14.0-14.35 MHz range, so a real, populated band plan would highlight
      // a "20m" tab from an operator no one has identified.
      const props = toBandSelectorProps(null);
      expect(findActiveBand(props.currentFreq, HF_RANGES)).toBeNull();
    });

    it('still reports the real value for a populated receiver', () => {
      const props = toBandSelectorProps(makeState());
      expect(props.currentFreq).toBe(14_074_000);
      expect(findActiveBand(props.currentFreq, HF_RANGES)).toBe('20m');
    });
  });

  describe('toFilterProps', () => {
    it('does not invent USB / a three-filter FIL1-FIL3 catalog without state or capabilities', () => {
      const props = toFilterProps(null, null);
      expect(props.currentMode).toBe('---');
      expect(props.filterLabels).toEqual([]);
    });

    it('does not invent the 2400 Hz width fallback (MOR-1409 A12, adjudication 5245697359, Core #2317)', () => {
      // A11 deliberately kept `?? 2400` here: a NaN sentinel renders as the
      // literal "NaNkHz" in FilterPanel.svelte's BW readout and settings
      // modal — a formatted-display consumer, unlike `findActiveBand`'s
      // comparison consumer. A12 is granted FilterPanel.svelte as a fourth
      // production file specifically to add the consumer-boundary guard
      // (see FilterPanel.isolated.test.ts), so the fabricated default can
      // now be removed at the source without leaking "NaN" into the UI.
      const props = toFilterProps(null, null);
      expect(props.filterWidth).toBeNaN();
    });

    it('still reports the real values for a populated receiver and capabilities', () => {
      const props = toFilterProps(makeState(), { filters: ['FIL1', 'FIL2'] } as any);
      expect(props.currentMode).toBe('USB');
      expect(props.filterLabels).toEqual(['FIL1', 'FIL2']);
    });
  });

  describe('toModeProps', () => {
    it('does not invent USB when state is absent', () => {
      const props = toModeProps(null, null);
      expect(props.currentMode).toBe('---');
    });

    it('still reports the real mode for a populated receiver', () => {
      const props = toModeProps(makeState(), null);
      expect(props.currentMode).toBe('USB');
    });
  });

  describe('toAgcProps', () => {
    it('does not invent a 3-mode [1,2,3] AGC catalog without capabilities', () => {
      const props = toAgcProps(makeState(), null);
      expect(props.agcModes).toEqual([]);
    });

    it('still reports the real AGC choice set from capabilities', () => {
      const props = toAgcProps(makeState(), { capabilities: ['agc'], agcModes: [1, 3] } as any);
      expect(props.agcModes).toEqual([1, 3]);
    });

    it('reports the real observed agcMode value when the field IS available (symmetric positive pin)', () => {
      // Companion to 'does not present missing AGC as the default MID mode':
      // that test proves the missing-field side (agcAvailable === false =>
      // NaN); this one proves the populated-field side. Together they kill
      // the mutant class that forces `agcAvailable` to a constant in either
      // direction — a `false`-forced mutant would wrongly turn this real,
      // available AGC=1 reading into NaN; a `true`-forced mutant is already
      // caught by the missing-field test. `agc: 1` (not the base fixture's
      // default 2, and not the old fabricated MID default) makes the pin
      // unambiguous — it cannot pass by coincidentally matching a stale
      // literal.
      const props = toAgcProps(
        makeState({ main: { ...makeState().main, agc: 1 } }),
        { capabilities: ['agc'] } as any,
      );
      expect(props.agcMode).toBe(1);
      expect(props.hasAgc).toBe(true);
    });
  });

  describe('toAntennaProps', () => {
    it('does not invent a single declared antenna port without capabilities', () => {
      const props = toAntennaProps(makeState(), null);
      expect(props.antennaCount).toBe(0);
    });

    it('still reports the real declared antenna count', () => {
      const props = toAntennaProps(makeState(), { antennas: 2 } as any);
      expect(props.antennaCount).toBe(2);
    });
  });
});

/**
 * A12 (MOR-1409, Core #2317) — batch-B projections stop fabricating a
 * plausible-looking default for RIT/XIT, the mode catalog, CW pitch/speed/
 * sidetone/keyer, meters, RX audio level, scan status, filter width (its
 * `toFilterProps` twin), and the memory-panel "store VFO → channel" fields.
 * Same non-fabricating-sentinel convention as A11: `NaN` for numbers whose
 * type stays `number`, `'---'` for strings whose type stays `string`,
 * `null` for the two RIT/XIT booleans whose type widens to `boolean | null`
 * per the A12 re-anchor plan's §5 consumer-boundary matrix (both feed only
 * a `hasCap`-gated panel — see the matrix's golden-safety column — so the
 * type widening carries no rendering risk). `toCwProps.keyerType` is
 * removed entirely (dead output field, no consumer, per the matrix). Every
 * case below documents a value `panel-props.ts` used to invent on exact
 * base; A11's own §3.3 confirms each literal was still present at the A12
 * anchor.
 */
describe('A12 — batch-B projections do not fabricate defaults (MOR-1409)', () => {
  describe('toRitXitProps', () => {
    it('does not invent a zero offset when state is entirely absent (offset only — active stays boolean, see below)', () => {
      const props = toRitXitProps(null, null);
      expect(props.ritOffset).toBeNaN();
      expect(props.xitOffset).toBeNaN();
    });

    it('ritActive/xitActive keep a plain boolean contract (false when absent) — RitXitPanel.svelte is not an A12 owner, see panel-props.ts header comment', () => {
      // `RitXitPanel.svelte`'s `HardwareButton active={…}` prop is typed
      // `boolean | undefined`; widening `ritActive`/`xitActive` to
      // `boolean | null` breaks that (non-A12-owned) consumer's compile — a
      // fifth production file A12 is not granted. `false` is the
      // conservative "off" reading and the panel is gated on `hasRit`/
      // `hasXit` regardless.
      const props = toRitXitProps(null, null);
      expect(props.ritActive).toBe(false);
      expect(props.xitActive).toBe(false);
    });

    it('still reports the real observed RIT/XIT state', () => {
      const props = toRitXitProps(
        makeState({ ritOn: true, ritFreq: 150, ritTx: true }),
        { capabilities: ['rit', 'xit'] } as any,
      );
      expect(props.ritActive).toBe(true);
      expect(props.ritOffset).toBe(150);
      expect(props.xitActive).toBe(true);
      expect(props.xitOffset).toBe(150);
    });
  });

  describe('toModeProps modes catalog', () => {
    it('does not invent a 10-mode catalog without capabilities', () => {
      const props = toModeProps(makeState(), null);
      expect(props.modes).toEqual([]);
    });

    it('still reports the real capability-derived mode list', () => {
      const props = toModeProps(makeState(), { modes: ['USB', 'LSB', 'CW'] } as any);
      expect(props.modes).toEqual(['USB', 'LSB', 'CW']);
    });
  });

  describe('toCwProps', () => {
    it('does not invent pitch/speed/sidetone values when state is absent', () => {
      const props = toCwProps(null, null);
      expect(props.cwPitch).toBeNaN();
      expect(props.keySpeed).toBeNaN();
      expect(props.wpm).toBeNaN();
      expect(props.sidetonePitch).toBeNaN();
      expect(props.sidetoneLevel).toBeNaN();
    });

    it('twinPeak keeps a plain boolean contract (false when absent) — CwPanel.svelte is not an A12 owner, see panel-props.ts header comment', () => {
      // `CwPanel.svelte`'s `HardwareButton active={…}` prop is typed
      // `boolean | undefined`; widening `twinPeak` to `boolean | null`
      // breaks that (non-A12-owned) consumer's compile — a fifth
      // production file A12 is not granted. `false` is the conservative
      // "off" reading and the panel is gated on `hasCw` regardless.
      const props = toCwProps(null, null);
      expect(props.twinPeak).toBe(false);
    });

    it('removes the dead keyerType output field entirely (no production consumer)', () => {
      const props = toCwProps(makeState(), null);
      expect('keyerType' in props).toBe(false);
    });

    it('still reports the real observed CW values', () => {
      const props = toCwProps(
        makeState({
          cwPitch: 700,
          keySpeed: 25,
          main: { ...makeState().main, twinPeakFilter: true },
        }),
        null,
      );
      expect(props.cwPitch).toBe(700);
      expect(props.keySpeed).toBe(25);
      expect(props.wpm).toBe(25);
      expect(props.sidetonePitch).toBe(700);
      expect(props.twinPeak).toBe(true);
    });
  });

  describe('toMeterProps', () => {
    // No live `.svelte` consumer of `panel-props.ts`'s `toMeterProps` was
    // found repo-wide (the desktop `MetersDockPanel.svelte` reads raw
    // `radioState` fields directly; the LCD/mobile skins' `toMeterProps`
    // is an independent, frozen `state-adapter.ts` copy). Zero golden risk
    // either way — this is a direct unit-level pin, per the A12 re-anchor
    // plan §5's row (a) resolution.
    it('does not invent zero meter readings when state is absent', () => {
      const props = toMeterProps(null, null);
      expect(props.sValue).toBeNaN();
      expect(props.signal).toBeNaN();
      expect(props.rfPower).toBeNaN();
      expect(props.swr).toBeNaN();
      expect(props.alc).toBeNaN();
      expect(props.comp).toBeNaN();
      expect(props.vd).toBeNaN();
      expect(props.id).toBeNaN();
    });

    it('still reports the real observed meter readings', () => {
      const props = toMeterProps(
        makeState({
          powerMeter: 10,
          swrMeter: 1.5,
          alcMeter: 3,
          compMeter: 4,
          vdMeter: 137,
          idMeter: 55,
        }),
        null,
      );
      expect(props.sValue).toBe(50);
      expect(props.rfPower).toBe(10);
      expect(props.swr).toBe(1.5);
      expect(props.vd).toBe(137);
      expect(props.id).toBe(55);
    });
  });

  describe('toRxAudioProps afLevel', () => {
    it('does not invent the 0.5 normalized AF level when state is absent (local mode)', () => {
      const props = toRxAudioProps(null, null, { muted: false, rxEnabled: false, volume: 50 }, false);
      expect(props.afLevel).toBeNaN();
    });

    it('still reports the real observed local AF level', () => {
      const props = toRxAudioProps(
        makeState({ main: { ...makeState().main, afLevel: 0.3 } }),
        null,
        { muted: false, rxEnabled: false, volume: 50 },
        false,
      );
      expect(props.afLevel).toBe(0.3);
    });
  });

  describe('toScanProps', () => {
    it('does not invent scanType=0/scanResumeMode=0 when state is absent', () => {
      const props = toScanProps(null);
      expect(props.scanType).toBeNaN();
      expect(props.scanResumeMode).toBeNaN();
    });

    it('scanning keeps a plain boolean contract (false when absent) — ScanPanel.svelte is not an A12 owner, see panel-props.ts header comment', () => {
      // `ScanPanel.svelte`'s `HardwareButton active={…}` prop is typed
      // `boolean | undefined`; widening `scanning` to `boolean | null`
      // breaks that (non-A12-owned) consumer's compile — a fifth
      // production file A12 is not granted. `false` is the conservative
      // "not scanning" reading.
      const props = toScanProps(null);
      expect(props.scanning).toBe(false);
    });

    it('still reports the real observed scan state', () => {
      const props = toScanProps(makeState({ scanning: true, scanType: 0x01, scanResumeMode: 0xd2 }));
      expect(props.scanning).toBe(true);
      expect(props.scanType).toBe(0x01);
      expect(props.scanResumeMode).toBe(0x02);
    });
  });

  describe('toAudioSpectrumProps filterWidth (toFilterProps twin)', () => {
    it('does not invent the 2400 Hz width fallback when state is absent', () => {
      const props = toAudioSpectrumProps(null, null);
      expect(props.filterWidth).toBeNaN();
    });

    it('still reports the real observed filter width', () => {
      const props = toAudioSpectrumProps(makeState({ main: { ...makeState().main, filterWidth: 1800 } }), null);
      expect(props.filterWidth).toBe(1800);
    });

    it('still fabricates a centre contourFreq — explicit non-fix, distinct class (MOR-1409 A12)', () => {
      // Per the A12 re-anchor plan §5: `contourFreq` is not yet exposed in
      // `ServerState` at all — it is a placeholder for a feature not wired
      // end-to-end, not a per-field "unobserved" case like every other
      // literal in this file. Converting it to NaN was explicitly flagged
      // as a distinct decision point, not folded into this mechanical sweep.
      const props = toAudioSpectrumProps(null, null);
      expect(props.contourFreq).toBe(128);
    });
  });

  describe('toMemoryPanelProps', () => {
    it('does not invent activeFreqHz=0/activeMode="" when state is absent', () => {
      const props = toMemoryPanelProps(null, null);
      expect(props.activeFreqHz).toBeNaN();
      expect(props.activeMode).toBe('---');
    });

    it('still reports the real observed active-receiver frequency/mode', () => {
      const props = toMemoryPanelProps(makeState(), null);
      expect(props.activeFreqHz).toBe(14_074_000);
      expect(props.activeMode).toBe('USB');
    });
  });

  describe('toTxProps — explicit non-fix (MOR-1409 A12)', () => {
    // TxPanel.svelte (panel-props.ts's `toTxProps`' only production
    // consumer) is entirely hidden on the desktop-v2 skin
    // (`hideTxPanel={semanticRxTx}`, true for desktop-v2 — zero golden
    // risk there) but IS reachable on the mobile skin. Its settings-modal
    // `ValueControl` calls for rfPower/micGain/monLevel/driveGain pass
    // `displayFn={normalizedPercentDisplay}`/`rawToPercentDisplay`, neither
    // of which guards a non-finite input — a `NaN` sentinel here would
    // render the literal "NaN%" in a real, live, mobile-reachable surface
    // (not covered by any committed golden — the only enforced
    // `toHaveScreenshot` gate is the desktop-root capture, which always
    // fixtures the receiver as populated). Fixing this cleanly needs a
    // consumer-boundary guard in TxPanel.svelte, mirroring FilterPanel's
    // fix in this same gate — but TxPanel.svelte is not one of A12's four
    // granted production files. A12 defers the entire `toTxProps` batch-B
    // family (rather than a partial boolean-only fix) rather than trade a
    // plausible-looking-but-wrong value for a raw "NaN%" glitch in a file
    // it cannot guard. This mirrors A11's own `toFilterProps.filterWidth`
    // deferral to A12 exactly.
    it('still fabricates the RF power / mic gain / mon level / drive gain / vox / comp defaults', () => {
      const props = toTxProps(null, null);
      expect(props.rfPower).toBe(0.5);
      expect(props.micGain).toBe(128);
      expect(props.monLevel).toBe(128);
      expect(props.driveGain).toBe(128);
      expect(props.voxActive).toBe(false);
      expect(props.compActive).toBe(false);
    });
  });
});
