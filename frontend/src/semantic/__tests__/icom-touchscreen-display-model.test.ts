import { describe, expect, it, vi } from 'vitest';
import type {
  RadioViewModel,
  ReceiverIndicatorViewModel,
  TxAuxField,
} from '../radio-view-model';
import {
  createIcomTouchscreenDisplayModel,
  type NeedleMeterDescriptor,
  type ReceiverScopeFrame,
} from '../icom-touchscreen-display-model';

const known = <T>(value: T, operational = true): TxAuxField<T> => ({
  reading: { status: 'known', value },
  availability: { structural: true, operational },
});

const unknown = <T>(operational = true): TxAuxField<T> => ({
  reading: { status: 'unknown' },
  availability: { structural: true, operational },
});

const unsupported = <T>(): TxAuxField<T> => ({
  reading: { status: 'unknown' },
  availability: { structural: false, operational: false },
});

function indicator(
  receiver: 'MAIN' | 'SUB',
  sMeter: TxAuxField<number>,
  bandwidthHz: TxAuxField<number>,
): ReceiverIndicatorViewModel {
  return {
    receiver,
    availability: { structural: true, operational: true },
    rfState: 'receiving',
    sMeter,
    bandwidthHz,
    agcMode: unknown(),
    nbActive: unknown(),
    nrActive: unknown(),
    notchMode: unknown(),
    attenuator: unknown(),
    preamp: unknown(),
    rfGain: unknown(),
    digiSel: unknown(),
    ipPlus: unknown(),
  };
}

function view(
  mainMeter: TxAuxField<number> = known(-20),
  subMeter: TxAuxField<number> = known(10),
): RadioViewModel {
  return {
    topologyId: '2/main_sub',
    vfoScheme: 'main_sub',
    activeReceiver: { status: 'known', receiver: 'MAIN' },
    vfos: [
      {
        receiver: 'MAIN', slot: { kind: 'slotted', id: 'A' }, label: 'MAIN A',
        frequencyHz: 7_070_000, mode: 'LSB', filter: 'FIL2',
        isActive: true, isActiveSlot: true, isTxTarget: false,
      },
      {
        receiver: 'SUB', slot: { kind: 'slotted', id: 'A' }, label: 'SUB A',
        frequencyHz: 14_250_000, mode: 'USB', filter: 'FIL1',
        isActive: false, isActiveSlot: true, isTxTarget: false,
      },
    ],
    split: { status: 'known', value: false },
    dualWatch: { status: 'known', value: true },
    txTarget: { status: 'unknown', reason: 'not-observed' },
    txPermit: { status: 'unknown', reason: 'tx-target-unknown' },
    scope: {
      hardwareScope: { structural: true, operational: true },
      audioFftScope: { structural: false, operational: false },
    },
    disabledReasons: [],
    receiverIndicators: [
      indicator('MAIN', mainMeter, known(2_400)),
      indicator('SUB', subMeter, known(3_100)),
    ],
  } as RadioViewModel;
}

const descriptor: NeedleMeterDescriptor = {
  ticks: [
    { fraction: 0, label: 'S0' },
    { fraction: 0.5, label: 'S9' },
    { fraction: 1, label: '+60', emphasis: 'warning' },
  ],
  project: vi.fn((value: number) => ({
    fraction: (value + 60) / 120,
    label: value > 0 ? `S9+${value}` : `S${Math.max(0, Math.round((value + 60) / 7))}`,
  })),
};

describe('createIcomTouchscreenDisplayModel', () => {
  it('projects MAIN and SUB independently into the same receiver contract', () => {
    const model = createIcomTouchscreenDisplayModel({ view: view(), sMeter: descriptor });
    expect(model.receivers.map((receiver) => receiver.receiver)).toEqual(['MAIN', 'SUB']);
    expect(model.receivers[0].frequencyHz).toEqual({ status: 'known', value: 7_070_000 });
    expect(model.receivers[1].frequencyHz).toEqual({ status: 'known', value: 14_250_000 });
    expect(model.receivers[0].bandwidthHz).toEqual({ status: 'known', value: 2_400 });
    expect(model.receivers[1].bandwidthHz).toEqual({ status: 'known', value: 3_100 });
    expect(model.receivers[0].receiverActivity).toBe('active');
    expect(model.receivers[1].receiverActivity).toBe('inactive');
  });

  it('fails closed when receiver slot selection is ambiguous', () => {
    const candidate = view();
    candidate.vfos = [
      ...candidate.vfos,
      {
        receiver: 'MAIN', slot: { kind: 'slotted', id: 'B' }, label: 'MAIN B',
        frequencyHz: 7_100_000, mode: 'CW', filter: 'FIL3',
        isActive: false, isActiveSlot: true, isTxTarget: false,
      },
    ];
    const model = createIcomTouchscreenDisplayModel({ view: candidate, sMeter: descriptor });
    expect(model.receivers[0].frequencyHz.status).toBe('unknown');
    expect(model.receivers[0].mode.status).toBe('unknown');
    expect(model.receivers[0].filter.status).toBe('unknown');
  });

  it('preserves known, unknown, unsupported, and inactive meter truth without fabricating zero', () => {
    const cases = [
      [known(-20), 'known'],
      [unknown<number>(), 'unknown'],
      [unsupported<number>(), 'unsupported'],
      [known(-20, false), 'inactive'],
    ] as const;
    for (const [source, expected] of cases) {
      const meter = createIcomTouchscreenDisplayModel({
        view: view(source, known(10)),
        sMeter: descriptor,
      }).receivers[0].sMeter;
      expect(meter.status).toBe(expected);
      if (meter.status !== 'known') expect(meter.value?.fraction).not.toBe(0);
    }
  });

  it('treats a missing or invalid calibration descriptor as unknown, not S0', () => {
    const noDescriptor = createIcomTouchscreenDisplayModel({ view: view(), sMeter: null });
    expect(noDescriptor.receivers[0].sMeter).toEqual({ status: 'unknown' });

    const invalid: NeedleMeterDescriptor = { ticks: [], project: () => ({ fraction: Number.NaN, label: 'S0' }) };
    const invalidProjection = createIcomTouchscreenDisplayModel({ view: view(), sMeter: invalid });
    expect(invalidProjection.receivers[0].sMeter).toEqual({ status: 'unknown' });
  });

  it('accepts only App-supplied receiver-matching frames and clones their pixels', () => {
    const pixels = new Uint8Array([1, 2, 3]);
    const main: ReceiverScopeFrame = { receiver: 'MAIN', pixels, startHz: 7_000_000, endHz: 7_100_000 };
    const model = createIcomTouchscreenDisplayModel({
      view: view(),
      sMeter: descriptor,
      scopeFrames: { MAIN: main },
    });
    pixels[0] = 255;
    expect(model.receivers[0].scope.status).toBe('known');
    expect(model.receivers[0].scope.value?.pixels[0]).toBe(1);
    expect(model.receivers[1].scope).toEqual({ status: 'unknown' });

    const mismatched = createIcomTouchscreenDisplayModel({
      view: view(),
      sMeter: descriptor,
      scopeFrames: { MAIN: { receiver: 'SUB', pixels: new Uint8Array([9]) } },
    });
    expect(mismatched.receivers[0].scope).toEqual({ status: 'unknown' });
  });
});
