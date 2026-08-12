import { afterEach, beforeEach, describe, it, expect } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import { clearCapabilities, setCapabilities } from '$lib/stores/capabilities.svelte';
import {
  SSB_STEPS, CW_STEPS, AM_STEPS, FM_STEPS, DEFAULT_STEPS,
  getStepsForMode, formatStep, formatSValue, formatDbm, formatPower,
} from '../mobile-layout-logic';

// MOR-1451 follow-up: the mobile skin's S-meter readouts must follow the
// active radio's profile-declared calibration curve (via the shared
// `smeter-scale.ts` helpers) instead of a third hardcoded IC-7610-shaped
// copy of the math. An IC-7300-shaped fixture — the documented Icom
// convention with its S9+60 top anchor (`rigs/ic7300.toml`) — is exactly
// the curve the old hardcoded +40 clamp misrendered.
//
// The curve is seeded into the REAL capabilities store, not vi.mock'd:
// this file runs in the `fast` pool (`isolate: false`), where a
// module-scope mock races the shared module cache — a sibling file can
// leave `mobile-layout-logic` → `smeter-scale` bound to a different module
// instance than the one the mock (and its `beforeEach` reconfiguration)
// applies to, and the tests flip red with no production change. Seeding
// real store state is deterministic under any cache order.
const IC7300_LIKE_CAL = [
  { raw: 0, actual: -54, label: 'S0' },
  { raw: 120, actual: 0, label: 'S9' },
  { raw: 241, actual: 60, label: 'S9+60' },
];

function makeCaps(overrides: Partial<Capabilities> = {}): Capabilities {
  return {
    model: 'IC-7300',
    scope: true,
    audio: true,
    tx: true,
    capabilities: ['scope', 'tx'],
    receivers: 1,
    vfoScheme: 'ab',
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

beforeEach(() => {
  setCapabilities(makeCaps({
    meterCalibrations: { s_meter: IC7300_LIKE_CAL },
  }));
});

afterEach(() => {
  clearCapabilities();
});

describe('getStepsForMode', () => {
  it('returns SSB_STEPS for USB', () => {
    expect(getStepsForMode('USB')).toBe(SSB_STEPS);
  });

  it('returns SSB_STEPS for LSB', () => {
    expect(getStepsForMode('LSB')).toBe(SSB_STEPS);
  });

  it('returns SSB_STEPS for lowercase usb', () => {
    expect(getStepsForMode('usb')).toBe(SSB_STEPS);
  });

  it('returns CW_STEPS for CW', () => {
    expect(getStepsForMode('CW')).toBe(CW_STEPS);
  });

  it('returns CW_STEPS for CW-R', () => {
    expect(getStepsForMode('CW-R')).toBe(CW_STEPS);
  });

  it('returns AM_STEPS for AM', () => {
    expect(getStepsForMode('AM')).toBe(AM_STEPS);
  });

  it('returns FM_STEPS for FM', () => {
    expect(getStepsForMode('FM')).toBe(FM_STEPS);
  });

  it('returns DEFAULT_STEPS for unknown mode', () => {
    expect(getStepsForMode('RTTY')).toBe(DEFAULT_STEPS);
  });

  it('returns DEFAULT_STEPS for empty string', () => {
    expect(getStepsForMode('')).toBe(DEFAULT_STEPS);
  });
});

describe('formatStep', () => {
  it('formats Hz for values below 1000', () => {
    expect(formatStep(100)).toBe('100 Hz');
    expect(formatStep(50)).toBe('50 Hz');
    expect(formatStep(10)).toBe('10 Hz');
  });

  it('formats kHz for values >= 1000', () => {
    expect(formatStep(1000)).toBe('1 kHz');
    expect(formatStep(5000)).toBe('5 kHz');
    expect(formatStep(10000)).toBe('10 kHz');
    expect(formatStep(12500)).toBe('12.5 kHz');
  });
});

describe('formatSValue (calibrated radio)', () => {
  it('returns S9 for a calibrated 0 dB-rel-S9 reading', () => {
    expect(formatSValue(0)).toBe('S9');
  });

  it('returns S0 at the calibrated floor', () => {
    expect(formatSValue(-54)).toBe('S0');
  });

  it('returns S-unit for calibrated sub-S9 values', () => {
    expect(formatSValue(-24)).toBe('S5');
  });

  it('returns S9+ for values above S9 but below the next anchor', () => {
    expect(formatSValue(20)).toBe('S9+');
  });

  // Kills: the old hardcoded +40 clamp — the documented Icom top anchor is
  // S9+60, and the profile curve (not the mobile skin) decides the ceiling.
  it('renders the profile-declared S9+60 top anchor', () => {
    expect(formatSValue(60)).toBe('S9+60');
  });

  it('clamps beyond-scale readings to the profile top anchor, not +40', () => {
    expect(formatSValue(255)).toBe('S9+60');
  });
});

describe('formatSValue (uncalibrated radio)', () => {
  beforeEach(() => {
    // A profile that declares no s_meter calibration table.
    setCapabilities(makeCaps({ model: 'X6200' }));
  });

  // Kills: misreading a raw device-scale byte as calibrated dB-rel-S9 — the
  // "S9+40 regardless of signal" class MOR-1451 removed everywhere else.
  it('renders the plain raw number instead of a fabricated S-unit', () => {
    expect(formatSValue(200)).toBe('200');
  });

  it('does not claim S9 for a raw zero', () => {
    expect(formatSValue(0)).toBe('0');
  });
});

describe('formatDbm (calibrated radio)', () => {
  it('returns −73 dBm at S9 (0 dB-rel-S9)', () => {
    expect(formatDbm(0)).toBe('−73 dBm');
  });

  it('returns lower dBm for weaker signals', () => {
    expect(formatDbm(-54)).toBe('−127 dBm');
  });

  it('returns higher dBm for strong signals', () => {
    expect(formatDbm(20)).toBe('−53 dBm');
  });

  it('clamps to the profile-declared +60 ceiling, not +40', () => {
    expect(formatDbm(255)).toBe('−13 dBm');
  });
});

describe('formatDbm (uncalibrated radio)', () => {
  beforeEach(() => {
    setCapabilities(makeCaps({ model: 'X6200' }));
  });

  // Kills: fabricating a physical dBm figure from a raw byte with no curve.
  it('renders the explicit uncalibrated label', () => {
    expect(formatDbm(200)).toBe('uncalibrated');
  });
});

describe('formatPower', () => {
  // power_level is served normalized 0.0-1.0 (MOR-334 contract), not raw 0-255.
  it('returns 0W for zero', () => {
    expect(formatPower(0)).toBe('0W');
  });

  it('returns 100W for max (1.0)', () => {
    expect(formatPower(1)).toBe('100W');
  });

  it('returns approximate wattage for mid-range', () => {
    // 0.5 * 100 → 50
    expect(formatPower(0.5)).toBe('50W');
  });

  it('clamps out-of-range normalized input', () => {
    expect(formatPower(1.4)).toBe('100W');
  });
});
