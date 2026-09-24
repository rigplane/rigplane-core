import { describe, expect, it } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import type { FieldStatus, ServerState } from '$lib/types/state';
import { receiverInRepeaterBand } from '$lib/radio/band-plan';
import { validateRadioViewModel, type RadioViewModel } from '../../../../semantic/radio-view-model';
import { toRadioViewModel } from '../radio-view-model-adapter';

const fresh: FieldStatus = {
  storePath: 'x', observed: true, freshness: 'fresh', availability: 'available',
};

function repeaterCaps(): Capabilities {
  return {
    model: 'fixture', scope: false, audio: false, tx: true,
    capabilities: ['tx', 'dual_rx', 'repeater_tone', 'tsql', 'repeater_shift'],
    receivers: 2, vfoScheme: 'main_sub',
    freqRanges: [
      { start: 100_000, end: 60_000_000, label: 'HF' },
      { start: 144_000_000, end: 148_000_000, label: '2m', repeater: true },
      { start: 430_000_000, end: 450_000_000, label: '70cm', repeater: true },
    ],
    modes: [], filters: [],
    audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
    webrtc: { available: false, enabled: false },
    txBands: [{ start: 144_000_000, end: 148_000_000, name: '2m' }],
  } as Capabilities;
}

type RepeaterRx = {
  freqHz: number;
  repeaterTone: boolean;
  repeaterTsql: boolean;
  toneFreq: number | null;
  repeaterShift: number | null;
};

function receiver(
  freqHz: number,
  repeaterTone: boolean,
  repeaterTsql: boolean,
  toneFreq: number | null,
  repeaterShift: number | null,
): RepeaterRx {
  return { freqHz, repeaterTone, repeaterTsql, toneFreq, repeaterShift };
}

function repeaterState(main: RepeaterRx, sub: RepeaterRx): ServerState {
  const paths = [
    'active', 'split', 'dualWatch', 'txTarget',
    'main.freqHz', 'main.mode', 'main.filter',
    'sub.freqHz', 'sub.mode', 'sub.filter',
    'main.repeaterTone', 'main.repeaterTsql', 'main.toneFreq', 'main.repeaterShift',
    'sub.repeaterTone', 'sub.repeaterTsql', 'sub.toneFreq', 'sub.repeaterShift',
  ];
  return {
    active: 'MAIN', split: false, dualWatch: false, ptt: false,
    txTarget: { status: 'known', receiver: 'MAIN', slot: 'A', frequencyHz: 14250000 },
    main: { ...main, mode: 'USB', filter: 1 },
    sub: { ...sub, mode: 'FM', filter: 1 },
    fieldStatus: Object.fromEntries(paths.map((p) => [p, fresh])),
  } as ServerState;
}

function repeater(main: RepeaterRx, sub: RepeaterRx): NonNullable<RadioViewModel['repeater']> {
  const view = toRadioViewModel(repeaterState(main, sub), repeaterCaps());
  expect(view).not.toBeNull();
  expect(view!.repeater).toBeDefined();
  // The emitted group must round-trip the real validator.
  validateRadioViewModel(view!);
  return view!.repeater!;
}

describe('receiverInRepeaterBand (range-level match, MOR-2111)', () => {
  const ranges = repeaterCaps().freqRanges;
  it('is inclusive at both edges of a flagged range', () => {
    expect(receiverInRepeaterBand(144_000_000, ranges)).toBe(true);
    expect(receiverInRepeaterBand(148_000_000, ranges)).toBe(true);
  });
  it('is false just outside the edges and inside an unflagged range', () => {
    expect(receiverInRepeaterBand(143_999_999, ranges)).toBe(false);
    expect(receiverInRepeaterBand(148_000_001, ranges)).toBe(false);
    expect(receiverInRepeaterBand(14_250_000, ranges)).toBe(false);
  });
});

describe('deriveRepeater (per-receiver repeater facts, MOR-2111)', () => {
  it('gates the whole group on a flagged range being present', () => {
    const noRepeaterCaps = {
      ...repeaterCaps(),
      freqRanges: [{ start: 100_000, end: 60_000_000, label: 'HF' }],
    } as Capabilities;
    const view = toRadioViewModel(
      repeaterState(
        receiver(14_250_000, false, false, 8850, 0),
        receiver(144_700_000, false, false, 8850, 0),
      ),
      noRepeaterCaps,
    );
    expect(view!.repeater).toBeUndefined();
  });

  it('flags only the receiver whose own frequency is in a repeater band', () => {
    const group = repeater(
      receiver(14_250_000, false, false, 8850, 0),
      receiver(144_700_000, false, false, 8850, 0),
    );
    expect(group.main.inRepeaterBand.reading).toEqual({ status: 'known', value: false });
    expect(group.sub.inRepeaterBand.reading).toEqual({ status: 'known', value: true });
  });

  it('derives toneMode off / tone / tsql from the two booleans', () => {
    expect(
      repeater(receiver(144_000_000, false, false, 8850, 0), receiver(14_250_000, false, false, 8850, 0))
        .main.toneMode.reading,
    ).toEqual({ status: 'known', value: 'off' });
    expect(
      repeater(receiver(144_000_000, true, false, 8850, 0), receiver(14_250_000, false, false, 8850, 0))
        .main.toneMode.reading,
    ).toEqual({ status: 'known', value: 'tone' });
    expect(
      repeater(receiver(144_000_000, true, true, 8850, 0), receiver(14_250_000, false, false, 8850, 0))
        .main.toneMode.reading,
    ).toEqual({ status: 'known', value: 'tsql' });
  });

  it('reads toneMode unknown for the unrepresentable pair (tone off, TSQL on)', () => {
    const group = repeater(
      receiver(144_000_000, false, true, 8850, 0),
      receiver(14_250_000, false, false, 8850, 0),
    );
    expect(group.main.toneMode.reading).toEqual({ status: 'unknown' });
    expect(group.main.toneMode.availability.structural).toBe(true);
  });

  it('reads toneFreq from toneFreq (the ENC register on the FTX-1)', () => {
    const group = repeater(
      receiver(144_000_000, true, false, 8850, 0),
      receiver(14_250_000, false, false, 8850, 0),
    );
    expect(group.main.toneFreq.reading).toEqual({ status: 'known', value: 8850 });
  });

  it('maps shift simplex / plus / minus and ARS (3) to unknown', () => {
    const simplex = repeater(
      receiver(144_000_000, false, false, 8850, 0),
      receiver(14_250_000, false, false, 8850, 0),
    ).main.shift.reading;
    const plus = repeater(
      receiver(144_000_000, false, false, 8850, 1),
      receiver(14_250_000, false, false, 8850, 0),
    ).main.shift.reading;
    const minus = repeater(
      receiver(144_000_000, false, false, 8850, 2),
      receiver(14_250_000, false, false, 8850, 0),
    ).main.shift.reading;
    const ars = repeater(
      receiver(144_000_000, false, false, 8850, 3),
      receiver(14_250_000, false, false, 8850, 0),
    ).main.shift.reading;
    expect(simplex).toEqual({ status: 'known', value: 'simplex' });
    expect(plus).toEqual({ status: 'known', value: 'plus' });
    expect(minus).toEqual({ status: 'known', value: 'minus' });
    expect(ars).toEqual({ status: 'unknown' });
  });

  it('marks shift unsupported when the radio lacks the repeater_shift capability', () => {
    const noShiftCaps = {
      ...repeaterCaps(),
      capabilities: ['tx', 'dual_rx', 'repeater_tone', 'tsql'],
    } as Capabilities;
    const view = toRadioViewModel(
      repeaterState(
        receiver(144_000_000, false, false, 8850, 0),
        receiver(14_250_000, false, false, 8850, 0),
      ),
      noShiftCaps,
    );
    expect(view!.repeater!.main.shift.availability.structural).toBe(false);
  });
});
