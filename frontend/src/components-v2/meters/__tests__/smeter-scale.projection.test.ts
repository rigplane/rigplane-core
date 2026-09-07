import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import { clearCapabilities, setCapabilities } from '$lib/stores/capabilities.svelte';
import { projectSignalMeter } from '../smeter-scale';

const NONUNIFORM_CAL = [
  { raw: 0, actual: -54, label: 'S0' },
  { raw: 26, actual: -48, label: 'S1' },
  { raw: 52, actual: -36, label: 'S3' },
  { raw: 78, actual: -24, label: 'S5' },
  { raw: 103, actual: -12, label: 'S7' },
  { raw: 130, actual: 0, label: 'S9' },
  { raw: 165, actual: 10, label: 'S9+10' },
  { raw: 200, actual: 20, label: 'S9+20' },
  { raw: 240, actual: 40, label: 'S9+40' },
] as const;

function capabilities(calibrated = true): Capabilities {
  return {
    model: 'projection-test',
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
    meterCalibrations: calibrated ? { s_meter: [...NONUNIFORM_CAL] } : undefined,
  };
}

beforeEach(() => setCapabilities(capabilities()));
afterEach(() => clearCapabilities());

describe('projectSignalMeter', () => {
  it('projects one calibrated reading into motion, text, crossover, and labeled marks', () => {
    const projection = projectSignalMeter(-48, { kind: 'engineering', unit: 'db' });

    expect(Object.keys(projection).sort()).toEqual([
      'accessibleDescription', 'crossoverFraction', 'marks', 'motionFraction', 'primaryText',
      'scaleMode', 'secondaryText', 'ticks',
    ]);
    expect(projection.scaleMode).toBe('s');
    expect(projection.motionFraction).toBeCloseTo(11 / 9 / 20);
    expect(projection.primaryText).toBe('S1');
    expect(projection.secondaryText).toBe('\u2212121 dBm');
    expect(projection.crossoverFraction).toBe(11 / 20);
    expect(projection.marks.map(({ actual, text }) => ({ actual, text }))).toEqual([
      { actual: -48, text: 'S1' },
      { actual: -36, text: 'S3' },
      { actual: -24, text: 'S5' },
      { actual: -12, text: 'S7' },
      { actual: 0, text: 'S9' },
      { actual: 10, text: '+10' },
      { actual: 20, text: '+20' },
      { actual: 40, text: '+40' },
    ]);
    expect(Object.keys(projection.marks[0]).sort()).toEqual([
      'actual', 'color', 'fraction', 'text',
    ]);
    expect(Object.keys(projection.ticks[0]).sort()).toEqual(['color', 'fraction', 'kind']);
    expect(projection.ticks).toHaveLength(81);
    expect(projection.marks[0].fraction).toBeCloseTo(11 / 9 / 20);
    expect(projection.marks[4].fraction).toBe(11 / 20);
    expect(projection.marks.at(-1)?.fraction).toBe(1);
  });

  it('keeps unknown distinct from a known zero while retaining scale context', () => {
    const unknown = projectSignalMeter(null);
    const zero = projectSignalMeter(0);

    expect(unknown).toMatchObject({
      motionFraction: null,
      primaryText: 'S ?',
      secondaryText: '',
      crossoverFraction: 11 / 20,
      scaleMode: 's',
    });
    expect(unknown.marks).toEqual(zero.marks);
    expect(unknown.ticks).toEqual(zero.ticks);
    expect(zero.motionFraction).toBe(11 / 20);
    expect(zero.primaryText).toBe('S9');
    expect(zero.secondaryText).toBe('\u221273 dBm');
  });

  it.each([
    [-54, 'S0', 0, '\u2212127 dBm'],
    [-36, 'S3', (3 / 9) * 11 / 20, '\u2212109 dBm'],
    [10, 'S9+10', (11 + (35 / 110) * 9) / 20, '\u221263 dBm'],
    [40, 'S9+40', 1, '\u221233 dBm'],
  ] as const)(
    'keeps nonuniform calibration aligned at actual=%s',
    (actual, primaryText, motionFraction, secondaryText) => {
      expect(projectSignalMeter(actual)).toMatchObject({ primaryText, secondaryText });
      expect(projectSignalMeter(actual).motionFraction).toBeCloseTo(motionFraction);
    },
  );

  it('assigns the existing scale colors without adding a second zone policy', () => {
    expect(projectSignalMeter(0).marks.map((mark) => mark.color)).toEqual([
      'var(--v2-text-bright)',
      'var(--v2-text-bright)',
      'var(--v2-text-bright)',
      'var(--v2-text-bright)',
      'var(--v2-text-bright)',
      'var(--v2-accent-yellow)',
      'var(--v2-accent-yellow)',
      'var(--v2-accent-orange-alt)',
    ]);
  });

  it('maps dense subdivisions through hidden calibration knots', () => {
    const hiddenKnotCal = [
      { raw: 0, actual: -54, label: 'S0' },
      { raw: 30, actual: -48, label: 'S1' },
      { raw: 35, actual: -42, label: 'S2' },
      { raw: 80, actual: -36, label: 'S3' },
      { raw: 100, actual: -24, label: 'S5' },
      { raw: 120, actual: -12, label: 'S7' },
      { raw: 140, actual: 0, label: 'S9' },
      { raw: 180, actual: 20, label: 'S9+20' },
      { raw: 240, actual: 40, label: 'S9+40' },
    ];
    setCapabilities({
      ...capabilities(),
      meterCalibrations: { s_meter: hiddenKnotCal },
    });

    const projection = projectSignalMeter(-42);
    const halfwayBetweenLabeledS1AndS3 = (
      projection.marks[0].fraction + projection.marks[1].fraction
    ) / 2;
    const expectedThroughHiddenS2 = (2 + (55 - 35) / (80 - 35)) / 9 * 11 / 20;

    // Tick 15 is the raw-55 midpoint between labeled S1/raw-30 and S3/raw-80.
    expect(projection.ticks[15]).toMatchObject({ kind: 'mid' });
    expect(projection.ticks[15].fraction).toBeCloseTo(expectedThroughHiddenS2);
    expect(projection.ticks[15].fraction).not.toBeCloseTo(halfwayBetweenLabeledS1AndS3);
  });

  it.each([
    [-999, 0, 'S0', '\u2212127 dBm'],
    [999, 1, 'S9+40', '\u221233 dBm'],
  ] as const)(
    'keeps out-of-range actual=%s on the existing calibrated boundaries',
    (actual, motionFraction, primaryText, secondaryText) => {
      expect(projectSignalMeter(actual)).toMatchObject({
        motionFraction,
        primaryText,
        secondaryText,
      });
    },
  );

  it('uses the existing honest raw fallback when no calibration is declared', () => {
    setCapabilities(capabilities(false));

    const projection = projectSignalMeter(53);
    expect(projection.motionFraction).toBeCloseTo((53 / 127.5) * 11 / 20);
    expect(projection.scaleMode).toBe('raw');
    expect(projection.primaryText).toBe('53');
    expect(projection.secondaryText).toBe('uncalibrated');
    expect(projection.crossoverFraction).toBe(11 / 20);
    expect(projection.marks).toEqual([]);
    expect(projection.ticks).toEqual([]);
  });

  it('honors an explicit raw domain even when a calibration table is available', () => {
    const projection = projectSignalMeter(53, { kind: 'raw' });

    expect(projection).toMatchObject({
      scaleMode: 'raw',
      primaryText: '53',
      secondaryText: 'uncalibrated',
      crossoverFraction: null,
      marks: [],
      ticks: [],
    });
    expect(projection.motionFraction).toBeCloseTo((53 / 127.5) * 11 / 20);
    expect(projection.accessibleDescription).not.toMatch(/S[0-9]|dBm/);
  });

  it('keeps engineering text but suppresses unsupported geometry without calibration', () => {
    setCapabilities(capabilities(false));

    const projection = projectSignalMeter(-12, { kind: 'engineering', unit: 'db' });
    expect(projection).toMatchObject({
      scaleMode: 'none',
      motionFraction: null,
      primaryText: '\u221212 dB rel S9',
      secondaryText: 'scale unavailable',
      crossoverFraction: null,
      marks: [],
      ticks: [],
    });
    expect(projection.accessibleDescription).toContain('\u221212 decibels relative to S9');
  });

  it('never infers units or S geometry for an explicit unknown domain', () => {
    const projection = projectSignalMeter(53, { kind: 'unknown' });

    expect(projection).toMatchObject({
      scaleMode: 'none',
      motionFraction: null,
      primaryText: '53',
      secondaryText: 'unit unknown',
      crossoverFraction: null,
      marks: [],
      ticks: [],
    });
    expect(projection.accessibleDescription).not.toMatch(/S[0-9]|dBm|raw/);
  });

  it('does not emit an S-unit placeholder for an unknown sample with explicit unknown domain', () => {
    const projection = projectSignalMeter(null, { kind: 'unknown' });

    expect(projection).toMatchObject({
      scaleMode: 'none', motionFraction: null, primaryText: '?',
      secondaryText: 'unit unknown', crossoverFraction: null, marks: [], ticks: [],
    });
    expect(projection.primaryText).not.toContain('S');
  });
});
