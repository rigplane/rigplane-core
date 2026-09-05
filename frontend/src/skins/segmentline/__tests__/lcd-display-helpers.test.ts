import { describe, expect, it } from 'vitest';
import type {
  DisplayOffset, DisplayTelemetry, DisplayValue, PeerSplitReceiverDisplay,
} from '../../../semantic/radio-display-model';
import {
  fftInputState,
  filterEnvelopes,
  formatBandwidth,
  formatOffset,
  meterFill,
  notchIndicators,
  stateText, telemetryText, telemetryDescription,
} from '../lcd-display-helpers';

const known = <T>(value: T): DisplayValue<T> => ({ state: 'known', value });

const receiver = {
  receiver: 'MAIN',
  label: 'VFO A',
  activity: 'active',
  operational: true,
  frequency: known(14_250_000),
  mode: known('USB'),
  filter: known('FIL1'),
  band: known('20m'),
  sMeter: known(-18),
  bandwidthHz: known(2400),
  ifShiftHz: known(0),
  pbtInnerHz: known(400),
  pbtOuterHz: known(-400),
  spectrum: 'waiting',
  dsp: {
    agc: known('MID'),
    nb: { state: 'inactive' },
    nr: { state: 'active' },
    notch: known('off'),
  },
  front: {
    preamp: known(1),
    attenuator: known(0),
    rfGain: known(0.7),
    digiSel: { state: 'unsupported' },
    ipPlus: { state: 'inactive' },
  },
} satisfies PeerSplitReceiverDisplay;

describe('LCD display helpers', () => {
  it('preserves known, unknown, and unsupported text without inventing values', () => {
    expect(stateText(known('USB'))).toBe('USB');
    expect(stateText({ state: 'unknown' })).toBe('?');
    expect(stateText({ state: 'unsupported' })).toBe('—');
    expect(formatBandwidth(known(2400))).toBe('2.4k');
    expect(formatBandwidth({ state: 'unknown' })).toBe('?');
  });

  it.each([
    [{ state: 'active', offsetHz: 250 }, '+0.250'],
    [{ state: 'active', offsetHz: -54_500 }, '−54.500'],
    [{ state: 'inactive', offsetHz: 250 }, '+0.250'],
    [{ state: 'inactive' }, '—'],
    [{ state: 'unknown' }, '?'],
    [{ state: 'unsupported' }, '—'],
  ] satisfies readonly (readonly [DisplayOffset, string])[])(
    'formats offset state %j truthfully',
    (field, expected) => expect(formatOffset(field)).toBe(expected),
  );

  it('keeps unknown meters at empty geometry and clamps calibrated fill', () => {
    expect(meterFill({ state: 'unknown' })).toBe(0);
    expect(meterFill(known(-999))).toBe(0);
    expect(meterFill(known(999))).toBe(1);
  });

  it('keeps PBT envelopes distinct and refuses unknown geometry', () => {
    expect(filterEnvelopes(receiver).map(({ kind }) => kind)).toEqual(['inner', 'outer']);
    expect(filterEnvelopes({ ...receiver, pbtInnerHz: { state: 'unknown' } })).toEqual([]);
    expect(filterEnvelopes({
      ...receiver,
      pbtInnerHz: { state: 'unsupported' },
      pbtOuterHz: { state: 'unsupported' },
      ifShiftHz: { state: 'unknown' },
    })).toEqual([]);
  });

  it.each([
    ['manual', 'active', 'inactive'],
    ['auto', 'inactive', 'active'],
    ['off', 'inactive', 'inactive'],
  ] as const)('maps %s to mutually exclusive NOTCH/ANF indicators', (
    mode, notchState, anfState,
  ) => {
    expect(notchIndicators(known(mode))).toEqual({
      notch: { state: notchState },
      anf: { state: anfState },
    });
  });

  it('does not enrich unknown notch state and keeps FFT admission passive', () => {
    expect(notchIndicators({ state: 'unknown' })).toEqual({
      notch: { state: 'unknown' },
      anf: { state: 'unknown' },
    });
    expect(fftInputState(receiver, [0, 0.5, 1])).toBe('live');
    expect(fftInputState({ ...receiver, activity: 'inactive' }, [0, 0.5, 1])).toBe('live');
    expect(fftInputState({ ...receiver, spectrum: 'unsupported' }, [0, 0.5, 1]))
      .toBe('unsupported');
    expect(fftInputState(receiver, undefined)).toBe('missing');
  });
});

it.each([
  [{ state: 'known', value: 12.345, relevant: false }, '12.35'],
  [{ state: 'known', value: 0, relevant: true }, '0'],
  [{ state: 'unknown', relevant: true }, '?'],
  [{ state: 'unsupported', relevant: false }, '?'],
] satisfies [DisplayTelemetry, string][])('keeps legacy telemetry formatting %j', (field, text) => {
  expect(telemetryText(field)).toBe(text);
});

for (const relevance of ['idle', 'relevant', 'indeterminate'] as const)
  for (const observation of [{ state: 'current', value: 12.345 }, { state: 'current', value: 0 },
    { state: 'stale', value: 87.654 }, { state: 'unknown', reason: 'not-observed' }] as const) {
    it(`formats TX ${relevance}/${observation.state}/${'value' in observation ? observation.value : ''}`, () => {
      const field: DisplayTelemetry = { state: 'known', value: 207, relevant: true,
        txDisplay: { supported: true, relevance, observation } };
      const text = telemetryText(field);
      const description = telemetryDescription('PWR', field);
      expect(text).toBe(relevance === 'idle' ? 'IDLE' : observation.state === 'stale' ? 'STALE'
        : observation.state === 'unknown' ? '?' : `${Number(observation.value.toFixed(2))}${relevance === 'indeterminate' ? ' ?' : ''}`);
      expect(description).toContain('PWR');
      expect(description).not.toMatch(/207|87.65/);
      if (relevance === 'idle') expect(description).toContain('Not measuring in RX');
      if (relevance === 'indeterminate') expect(description).toContain('RF relevance indeterminate');
      if (relevance !== 'idle' && observation.state === 'stale') expect(description).toContain('Stale observation');
      if (relevance !== 'idle' && observation.state === 'unknown') expect(description).toContain('Not observed');
    });
  }

it('delegates only a current TX observation to the optional formatter', () => {
  const format = (value: number) => `${value}W`;
  const current: DisplayTelemetry = { state: 'known', value: 207, relevant: true,
    txDisplay: { supported: true, relevance: 'relevant', observation: { state: 'current', value: 100 } } };
  expect(telemetryText(current, format)).toBe('100W');
  expect(telemetryDescription('PWR', current, format)).toBe('PWR: Current observation: 100W');
  const idle: DisplayTelemetry = { ...current, txDisplay: { supported: true, relevance: 'idle', observation: { state: 'current', value: 100 } } };
  const forbidden = () => { throw new Error('unmeasured value formatted'); };
  expect(telemetryText(idle, forbidden)).toBe('IDLE');
  expect(telemetryDescription('PWR', idle, forbidden)).toBe('PWR: Not measuring in RX');
});
