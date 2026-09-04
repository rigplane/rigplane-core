import { describe, expect, it } from 'vitest';
import type {
  DisplayOffset, DisplayValue, PeerSplitReceiverDisplay,
} from '../../../semantic/radio-display-model';
import {
  fftInputState,
  filterEnvelopes,
  formatBandwidth,
  formatOffset,
  meterFill,
  notchIndicators,
  stateText,
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
