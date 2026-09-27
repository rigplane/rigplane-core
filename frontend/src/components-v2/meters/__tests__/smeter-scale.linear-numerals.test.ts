import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import { clearCapabilities, setCapabilities } from '$lib/stores/capabilities.svelte';
import { lerpScaleKnots, projectSignalMeter } from '../smeter-scale';

// MOR-2790: a numeral the table's own interpolation places is not invented.
// The three profiles' [meters.s_meter] calibration tables, verbatim from
// rigs/<rig>.toml (raw/actual/label).

// rigs/ic7300.toml — only the S0/S9/S9+60 anchors the CI-V Reference Guide
// publishes; intermediate S-units interpolate linearly between them.
const IC7300_CAL = [
  { raw: 0, actual: -54, label: 'S0' },
  { raw: 120, actual: 0, label: 'S9' },
  { raw: 241, actual: 60, label: 'S9+60' },
];

// rigs/ftx1.toml — every S-unit plus +10/+20/+40.
const FTX1_CAL = [
  { raw: 0, actual: -54, label: 'S0' },
  { raw: 13, actual: -51, label: 'S0.5' },
  { raw: 26, actual: -48, label: 'S1' },
  { raw: 39, actual: -45, label: 'S2' },
  { raw: 52, actual: -42, label: 'S3' },
  { raw: 65, actual: -39, label: 'S4' },
  { raw: 78, actual: -36, label: 'S5' },
  { raw: 91, actual: -33, label: 'S6' },
  { raw: 103, actual: -18, label: 'S7' },
  { raw: 117, actual: -9, label: 'S8' },
  { raw: 130, actual: 0, label: 'S9' },
  { raw: 165, actual: 10, label: 'S9+10' },
  { raw: 200, actual: 20, label: 'S9+20' },
  { raw: 240, actual: 40, label: 'S9+40' },
];

// rigs/ic7610.toml — the odd S-units plus +10/+20/+40.
const IC7610_CAL = [
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

type Cal = typeof IC7300_CAL | typeof FTX1_CAL | typeof IC7610_CAL;

function makeCaps(model: string, cal: Cal | undefined): Capabilities {
  return {
    model,
    scope: false,
    audio: false,
    tx: false,
    capabilities: [],
    receivers: 1,
    vfoScheme: 'single',
    freqRanges: [],
    modes: [],
    filters: [],
    audioConfig: { sampleRate: 48000, channels: 1, codecs: [] },
    webrtc: { available: false, enabled: false },
    txBands: null,
    stateContractVersion: 1,
    providerGeneration: 0,
    meterCalibrations: cal === undefined ? undefined : { s_meter: [...cal] },
  };
}

afterEach(() => clearCapabilities());

describe('MOR-2790 — numerals the linear calibration itself places', () => {
  beforeEach(() => setCapabilities(makeCaps('IC-7300', IC7300_CAL)));

  it('draws every odd S-unit and +dB rung the S0/S9/S9+60 table brackets', () => {
    const projection = projectSignalMeter(0, { kind: 'engineering', unit: 'db' });
    expect(projection.scaleMode).toBe('s');
    expect(projection.uniformScaleMarks.map((mark) => mark.text))
      .toEqual(['1', '3', '5', '7', '9', '+20', '+40', '+60']);
  });

  it.each([
    ['-48', -48, '1'],
    ['-36', -36, '3'],
    ['-24', -24, '5'],
    ['-12', -12, '7'],
    ['0', 0, '9'],
    ['+20', 20, '+20'],
    ['+40', 40, '+40'],
    ['+60', 60, '+60'],
  ] as const)('places the %s dB level at its numeral slot', (level, value, text) => {
    const projection = projectSignalMeter(value, { kind: 'engineering', unit: 'db' });
    const mark = projection.uniformScaleMarks.find((candidate) => candidate.text === text)!;
    // The pointer projection is the module's own: the level's motion
    // fraction transferred through the projection's uniform scale knots.
    const pointer = lerpScaleKnots(
      projection.uniformScaleKnots, projection.motionFraction!,
    );
    expect(pointer).toBeCloseTo(mark.slot, 6);
  });
});

describe('MOR-2790 — the declared-knot tables keep their numerals', () => {
  it.each([
    ['FTX-1', FTX1_CAL],
    ['IC-7610', IC7610_CAL],
  ] as const)('keeps the %s numeral list unchanged', (_name, cal) => {
    setCapabilities(makeCaps('pin', cal));
    const projection = projectSignalMeter(0, { kind: 'engineering', unit: 'db' });
    expect(projection.uniformScaleMarks.map((mark) => mark.text))
      .toEqual(['1', '3', '5', '7', '9', '+20', '+40']);
  });
});

describe('MOR-2790 — an uncalibrated radio draws no numerals', () => {
  it('keeps the empty uniform scale marks (MOR-2705 part 4a, MOR-2720)', () => {
    setCapabilities(makeCaps('uncalibrated', undefined));
    const projection = projectSignalMeter(53);
    expect(projection.scaleMode).toBe('raw');
    expect(projection.uniformScaleMarks).toEqual([]);
  });
});
