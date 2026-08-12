import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import { clearCapabilities, setCapabilities } from '$lib/stores/capabilities.svelte';

// MOR-1470 (finishes ADR level-meter-calibrated-domain Phase 3 for the six
// TX/PA meters, mirroring what MOR-1451 did for s_meter):
//
// - A meter whose profile declares a `[[meters.<key>.calibration]]` table
//   arrives at the frontend ALREADY in engineering units — the backend
//   (MOR-469) interpolates raw→actual at the observation boundary
//   (power=W, swr=ratio, alc=normalized 0–1, comp=dB, vd=V, id=A). The
//   formatters/level fns must render that value directly, never re-run it
//   through the curve (the double conversion this ticket removes).
// - A meter with NO declared table arrives as the raw device byte flagged
//   uncalibrated server-side; the frontend degrades to an honest raw
//   readout (plain number, neutral raw/255 bar) — never a unit claim
//   through a borrowed radio's curve. The hardcoded IC-7610 fallback knots
//   are gone.
//
// Capability state is seeded into the REAL store, not vi.mock'd: this file
// runs in the `fast` pool (`isolate: false`), where a module-scope mock
// races the shared module cache (see #2408/#2409).
const IC7610_LIKE_S_METER_CAL = [
  { raw: 0, actual: -54, label: 'S0' },
  { raw: 26, actual: -48, label: 'S1' },
  { raw: 52, actual: -36, label: 'S3' },
  { raw: 78, actual: -24, label: 'S5' },
  { raw: 103, actual: -12, label: 'S7' },
  { raw: 130, actual: 0, label: 'S9' },
  { raw: 165, actual: 10, label: 'S9+10' },
  { raw: 200, actual: 20, label: 'S9+20' },
  { raw: 240, actual: 40, label: 'S9+40' },
];

// The values rigs/ic7610.toml declares (power/swr/alc) and the operator's
// bench-measured vd anchor (MOR-1471 moves vd/id/comp into the profile) —
// one worked example of "a radio profile published curves", not a default.
const IC7610_LIKE_TX_METER_CALS = {
  power: [
    { raw: 0, actual: 0, label: '0' },
    { raw: 143, actual: 50, label: '50' },
    { raw: 212, actual: 100, label: '100' },
  ],
  swr: [
    { raw: 0, actual: 1.0, label: '1.0' },
    { raw: 48, actual: 1.5, label: '1.5' },
    { raw: 80, actual: 2.0, label: '2.0' },
    { raw: 120, actual: 3.0, label: '3.0' },
    { raw: 240, actual: 6.0, label: '6.0+' },
  ],
  alc: [
    { raw: 0, actual: 0, label: '0' },
    { raw: 120, actual: 100, label: '100' },
  ],
  vd: [
    { raw: 0, actual: 0, label: '0' },
    { raw: 13, actual: 10, label: '10' },
    { raw: 184, actual: 13.8, label: '13.8' },
    { raw: 241, actual: 16, label: '16' },
  ],
  id: [
    { raw: 0, actual: 0, label: '0' },
    { raw: 151, actual: 10, label: '10' },
    { raw: 195, actual: 15, label: '15' },
    { raw: 212, actual: 25, label: '25' },
  ],
  comp: [
    { raw: 0, actual: 0, label: '0' },
    { raw: 75, actual: 15, label: '15' },
    { raw: 150, actual: 30, label: '30' },
  ],
};

function makeCaps(overrides: Partial<Capabilities> = {}): Capabilities {
  return {
    model: 'IC-7610',
    scope: true,
    audio: true,
    tx: true,
    capabilities: ['scope', 'tx'],
    receivers: 2,
    vfoScheme: 'main_sub',
    freqRanges: [{ start: 1800000, end: 30000000, label: 'HF' }],
    modes: ['USB', 'LSB', 'CW', 'AM', 'FM'],
    filters: ['FIL1', 'FIL2', 'FIL3'],
    audioConfig: { sampleRate: 48000, channels: 1, codecs: ['opus'] },
    webrtc: { available: true, enabled: false },
    txBands: null,
    stateContractVersion: 1,
    providerGeneration: 0,
    ...overrides,
  };
}

import {
  normalize,
  formatPowerWatts,
  normalizePower,
  formatSwr,
  formatAlc,
  formatVolts,
  formatAmps,
  formatCompDb,
  formatSMeter,
  getNeedleMarks,
  swrRatio,
  swrLevel,
  alcLevel,
  idLevel,
  vdLevel,
  compLevel,
  sLevel,
  isSwrFault,
  isAlcFault,
  updatePeakHold,
  peakHoldDisplay,
  type PeakHoldState,
} from './meter-utils';

beforeEach(() => {
  setCapabilities(makeCaps({
    meterCalibrations: {
      s_meter: IC7610_LIKE_S_METER_CAL,
      ...IC7610_LIKE_TX_METER_CALS,
    },
  }));
});

afterEach(() => {
  clearCapabilities();
});

describe('normalize', () => {
  it('returns 0 for raw=0', () => {
    expect(normalize(0)).toBe(0);
  });
  it('returns 1 for raw=255', () => {
    expect(normalize(255)).toBe(1);
  });
  it('clamps negative to 0', () => {
    expect(normalize(-10)).toBe(0);
  });
  it('clamps >255 to 1', () => {
    expect(normalize(300)).toBe(1);
  });
  it('interpolates midpoint', () => {
    expect(normalize(128)).toBeCloseTo(128 / 255);
  });
});

// ---------------------------------------------------------------------------
// Calibrated domain: the profile declares the table, the backend already
// interpolated — the input IS the engineering quantity (MOR-469 / MOR-1470).
// ---------------------------------------------------------------------------

describe('formatPowerWatts (calibrated: input is watts)', () => {
  it('renders 0 W', () => {
    expect(formatPowerWatts(0)).toBe('0W');
  });
  it('renders 50 W as-is, no re-interpolation', () => {
    expect(formatPowerWatts(50)).toBe('50W');
  });
  it('renders 100 W full scale', () => {
    expect(formatPowerWatts(100)).toBe('100W');
  });
  it('rounds fractional watts', () => {
    expect(formatPowerWatts(49.6)).toBe('50W');
  });
  it('clamps beyond the table top (backend clamps too)', () => {
    expect(formatPowerWatts(140)).toBe('100W');
  });
});

describe('normalizePower (calibrated: watts / top knot)', () => {
  it('returns 0 for 0 W', () => {
    expect(normalizePower(0)).toBe(0);
  });
  it('returns 0.5 at 50 W (of 100 W full scale)', () => {
    expect(normalizePower(50)).toBeCloseTo(0.5);
  });
  it('returns 1.0 at 100 W', () => {
    expect(normalizePower(100)).toBeCloseTo(1.0);
  });
});

describe('formatSwr (calibrated: input is the ratio)', () => {
  it('renders 1.0 for a perfect match', () => {
    expect(formatSwr(1.0)).toBe('1.0');
  });
  it('renders the live-evidence FTX-1 ratio 2.375 as 2.4 (was "1.0" pre-fix)', () => {
    expect(formatSwr(2.375)).toBe('2.4');
  });
  it('renders 3.0', () => {
    expect(formatSwr(3.0)).toBe('3.0');
  });
  it('renders the table top label at/beyond the top knot', () => {
    expect(formatSwr(6.0)).toBe('6.0+');
    expect(formatSwr(9.9)).toBe('6.0+');
  });
  it('clamps sub-1.0 noise up to 1.0', () => {
    expect(formatSwr(0.2)).toBe('1.0');
  });
});

describe('swrRatio / isSwrFault (calibrated)', () => {
  it('swrRatio is the identity on a calibrated ratio', () => {
    expect(swrRatio(2.375)).toBeCloseTo(2.375);
  });
  it('flags fault above 2.0', () => {
    expect(isSwrFault(2.375)).toBe(true);
    expect(isSwrFault(3.0)).toBe(true);
  });
  it('does not flag at or below 2.0', () => {
    expect(isSwrFault(2.0)).toBe(false);
    expect(isSwrFault(1.5)).toBe(false);
  });
});

describe('swrLevel (calibrated bar: ratio / top knot)', () => {
  it('returns 0.5 at ratio 3.0 (top knot 6.0)', () => {
    expect(swrLevel(3.0)).toBeCloseTo(0.5);
  });
  it('returns 1.0 at the top knot', () => {
    expect(swrLevel(6.0)).toBe(1.0);
  });
  it('clamps beyond-scale ratios to 1.0', () => {
    expect(swrLevel(12)).toBe(1.0);
  });
});

describe('formatAlc / alcLevel / isAlcFault (calibrated: input is normalized 0-1)', () => {
  it('renders 0%', () => {
    expect(formatAlc(0)).toBe('0%');
  });
  it('renders 50% for 0.5', () => {
    expect(formatAlc(0.5)).toBe('50%');
  });
  it('renders 100% at the redline (1.0)', () => {
    expect(formatAlc(1.0)).toBe('100%');
  });
  it('clamps above 1.0', () => {
    expect(formatAlc(1.4)).toBe('100%');
  });
  it('alcLevel is the identity (already redline-relative)', () => {
    expect(alcLevel(0.5)).toBeCloseTo(0.5);
    expect(alcLevel(1.0)).toBeCloseTo(1.0);
  });
  it('flags fault past 90% of the redline', () => {
    expect(isAlcFault(0.95)).toBe(true);
    expect(isAlcFault(0.5)).toBe(false);
  });
});

describe('formatVolts / vdLevel (calibrated: input is volts)', () => {
  it('renders the bench supply voltage as-is', () => {
    expect(formatVolts(13.8)).toBe('13.8 V');
  });
  it('renders 0 V', () => {
    expect(formatVolts(0)).toBe('0.0 V');
  });
  it('vdLevel normalizes against the 16 V top knot', () => {
    expect(vdLevel(13.8)).toBeCloseTo(13.8 / 16);
    expect(vdLevel(16)).toBeCloseTo(1.0);
  });
});

describe('formatAmps / idLevel (calibrated: input is amps)', () => {
  it('renders 10 A', () => {
    expect(formatAmps(10)).toBe('10.0 A');
  });
  it('idLevel normalizes against the 25 A top knot', () => {
    expect(idLevel(10)).toBeCloseTo(10 / 25);
    expect(idLevel(25)).toBeCloseTo(1.0);
  });
});

describe('formatCompDb / compLevel (calibrated: input is dB)', () => {
  it('renders 15 dB', () => {
    expect(formatCompDb(15)).toBe('15 dB');
  });
  it('compLevel normalizes against the 30 dB top knot', () => {
    expect(compLevel(15)).toBeCloseTo(0.5);
    expect(compLevel(30)).toBeCloseTo(1.0);
  });
});

// ---------------------------------------------------------------------------
// Uncalibrated: no table declared — the backend publishes the raw device
// byte flagged uncalibrated. Honest degradation: plain number, neutral
// raw/255 bar, no fault claims, no borrowed-curve unit claims (MOR-1470).
// ---------------------------------------------------------------------------

describe('TX meters — uncalibrated honest fallback (MOR-1470)', () => {
  beforeEach(() => {
    // A profile that declares no meter calibration at all (X6200 class).
    setCapabilities(makeCaps({ model: 'X6200' }));
  });

  it('formatPowerWatts renders the plain raw number, not fabricated watts', () => {
    expect(formatPowerWatts(143)).toBe('143');
  });

  it('normalizePower degrades to raw/255', () => {
    expect(normalizePower(143)).toBeCloseTo(143 / 255);
  });

  it('formatSwr renders the plain raw number, not a fabricated ratio', () => {
    expect(formatSwr(48)).toBe('48');
  });

  it('swrRatio yields NaN — there is no honest ratio to compare', () => {
    expect(Number.isNaN(swrRatio(120))).toBe(true);
  });

  it('isSwrFault never claims a fault it cannot measure', () => {
    expect(isSwrFault(255)).toBe(false);
  });

  it('swrLevel degrades to raw/255', () => {
    expect(swrLevel(120)).toBeCloseTo(120 / 255);
  });

  it('formatAlc renders the plain raw number without table or redline', () => {
    expect(formatAlc(60)).toBe('60');
  });

  it('alcLevel degrades to raw/255; isAlcFault stays silent', () => {
    expect(alcLevel(60)).toBeCloseTo(60 / 255);
    expect(isAlcFault(255)).toBe(false);
  });

  it('formatVolts renders the plain raw number, not fabricated volts', () => {
    expect(formatVolts(184)).toBe('184');
  });

  it('formatAmps / formatCompDb render plain raw numbers', () => {
    expect(formatAmps(151)).toBe('151');
    expect(formatCompDb(75)).toBe('75');
  });

  it('vdLevel / idLevel / compLevel degrade to raw/255', () => {
    expect(vdLevel(184)).toBeCloseTo(184 / 255);
    expect(idLevel(151)).toBeCloseTo(151 / 255);
    expect(compLevel(75)).toBeCloseTo(75 / 255);
  });
});

// ALC middle ground: a profile may declare only `redline_raw` (no table).
// The value arrives raw; the redline makes a percent readout honest.

describe('formatAlc — redline-only profile (raw domain, data-driven %)', () => {
  beforeEach(() => {
    setCapabilities(makeCaps({
      model: 'IC-7300',
      meterRedlines: { alc: 120 },
    }));
  });

  it('renders percent relative to the declared redline', () => {
    expect(formatAlc(60)).toBe('50%');
    expect(formatAlc(120)).toBe('100%');
  });

  it('clamps beyond the redline', () => {
    expect(formatAlc(200)).toBe('100%');
  });

  it('alcLevel is redline-relative; fault past 90%', () => {
    expect(alcLevel(60)).toBeCloseTo(0.5);
    expect(isAlcFault(115)).toBe(true);
    expect(isAlcFault(60)).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// S-meter (unchanged by MOR-1470 — pinned here against regressions)
// ---------------------------------------------------------------------------

describe('formatSMeter', () => {
  it('returns S0 for the calibrated floor (-54 dB-rel-S9)', () => {
    expect(formatSMeter(-54)).toBe('S0');
  });
  it('returns S9 for a calibrated 0 dB-rel-S9 reading', () => {
    expect(formatSMeter(0)).toBe('S9');
  });
  it('returns S9+20 for a calibrated +20 dB reading', () => {
    expect(formatSMeter(20)).toBe('S9+20');
  });
  it('clamps weaker-than-floor readings to S0', () => {
    expect(formatSMeter(-80)).toBe('S0');
  });
  it('returns correct S-unit below S9 for calibrated values', () => {
    expect(formatSMeter(-24)).toBe('S5');
  });
});

describe('formatSMeter / sLevel — uncalibrated fallback (MOR-1451)', () => {
  beforeEach(() => {
    setCapabilities(makeCaps({ model: 'X6200' }));
  });

  it('formatSMeter renders the plain raw number, not a fabricated S-unit', () => {
    expect(formatSMeter(53)).toBe('53');
  });

  it('sLevel degrades to a neutral raw-proportional bar position', () => {
    expect(sLevel(53)).toBeCloseTo(53 / 255);
  });
});

describe('formatSMeter — IC-7300 profile conformance (MOR-1451)', () => {
  const IC7300_S_METER_CAL = [
    { raw: 0, actual: -54, label: 'S0' },
    { raw: 120, actual: 0, label: 'S9' },
    { raw: 241, actual: 60, label: 'S9+60' },
  ];

  beforeEach(() => {
    setCapabilities(makeCaps({
      model: 'IC-7300',
      meterCalibrations: { s_meter: IC7300_S_METER_CAL },
    }));
  });

  it('0 dB-rel-S9 -> S9 (the documented anchor)', () => {
    expect(formatSMeter(0)).toBe('S9');
  });

  it('the top anchor -> S9+60', () => {
    expect(formatSMeter(60)).toBe('S9+60');
  });
});

describe('sLevel (calibrated bar)', () => {
  it('returns ~1.0 at the strongest calibrated reading', () => {
    expect(sLevel(40)).toBeCloseTo(1.0);
  });
  it('returns the S9 marker position for a calibrated 0 dB-rel-S9 reading', () => {
    expect(sLevel(0)).toBeCloseTo(130 / 240);
  });
});

// ---------------------------------------------------------------------------
// Peak-hold on the meter value drives BOTH the number and the fill
// (MOR-498). Domain-agnostic: it latches/decays whatever quantity flows
// through it — here exercised in the calibrated watts domain.
// ---------------------------------------------------------------------------

describe('peak-hold on value -> number + fill coupling (MOR-498)', () => {
  const DECAY = 1500;

  function heldValue(peak: number, live: number, elapsedMs: number): number {
    let state: PeakHoldState | undefined = updatePeakHold(undefined, peak, 0, DECAY);
    state = updatePeakHold(state, live, elapsedMs, DECAY);
    return peakHoldDisplay(state, live, elapsedMs, DECAY);
  }

  it('formats the held PEAK watts shortly after a drop, not the trough', () => {
    // Peak 100 W, trough 2 W, 150ms into a 1500ms window.
    const held = heldValue(100, 2, 150);
    const watts = parseInt(formatPowerWatts(held), 10);
    expect(watts).toBeGreaterThan(80);
  });

  it('fill and number derive from the SAME held value (consistent)', () => {
    const held = heldValue(100, 2, 150);
    expect(normalizePower(held) * 100).toBeGreaterThan(80);
  });

  it('decays linearly to the live trough by the end of the 1.5 s window', () => {
    const atHalf = heldValue(100, 2, 750);
    expect(atHalf).toBeGreaterThan(45);
    expect(atHalf).toBeLessThan(100);
    const atEnd = heldValue(100, 2, 1500);
    expect(atEnd).toBe(2);
  });

  it('returns to 0 from a 0 input (RX, no stale peak)', () => {
    expect(heldValue(0, 0, 0)).toBe(0);
    expect(heldValue(0, 0, 1600)).toBe(0);
  });

  it('attack latches immediately to a higher sample', () => {
    const state = updatePeakHold({ latchedPeak: 50, latchedAt: 0 }, 90, 300, DECAY);
    expect(state).toEqual({ latchedPeak: 90, latchedAt: 300 });
    expect(peakHoldDisplay(state, 90, 300, DECAY)).toBe(90);
  });
});

// ---------------------------------------------------------------------------
// Needle marks: engineering-domain positions from the declared table;
// no marks when the radio declares no curve (nothing honest to draw).
// ---------------------------------------------------------------------------

describe('getNeedleMarks', () => {
  it('returns S-meter marks for source "S"', () => {
    const marks = getNeedleMarks('S');
    expect(marks.length).toBe(7);
    expect(marks[0].label).toBe('S1');
    expect(marks[4].label).toBe('S9');
    expect(marks[5].label).toBe('+20');
    expect(marks[6].label).toBe('+40');
    expect(marks[4].pos).toBeCloseTo(130 / 240);
  });

  it('returns SWR marks positioned by ratio / top knot', () => {
    const marks = getNeedleMarks('SWR');
    expect(marks.length).toBe(5);
    expect(marks[0].label).toBe('1.0');
    expect(marks[3].label).toBe('3.0');
    expect(marks[3].pos).toBeCloseTo(3.0 / 6.0);
    expect(marks[4].pos).toBeCloseTo(1.0);
  });

  it('returns POWER marks from the declared table', () => {
    const marks = getNeedleMarks('POWER');
    expect(marks.length).toBe(3);
    expect(marks[0].label).toBe('0');
    expect(marks[1].label).toBe('50');
    expect(marks[1].pos).toBeCloseTo(0.5);
    expect(marks[2].label).toBe('100');
  });

  it('returns same marks for "po" as "POWER"', () => {
    expect(getNeedleMarks('po')).toEqual(getNeedleMarks('POWER'));
  });

  it('returns no SWR/POWER marks for a radio with no declared curves', () => {
    setCapabilities(makeCaps({ model: 'X6200' }));
    expect(getNeedleMarks('SWR')).toEqual([]);
    expect(getNeedleMarks('POWER')).toEqual([]);
  });

  it('all mark positions are in 0-1 range', () => {
    for (const source of ['S', 'SWR', 'POWER', 'po'] as const) {
      for (const mark of getNeedleMarks(source)) {
        expect(mark.pos).toBeGreaterThanOrEqual(0);
        expect(mark.pos).toBeLessThanOrEqual(1);
      }
    }
  });
});
