import { describe, expect, it } from 'vitest';
import type {
  RadioViewModel, ReceiverIndicatorViewModel, TxAuxField,
} from '../radio-view-model';
import { topologyFixtures } from '../fixtures/topologies';
import { projectPeerSplitDisplay } from '../radio-display-model';

const availability = (structural = true, operational = true) => ({ structural, operational });
const known = <T>(value: T): TxAuxField<T> => ({ reading: { status: 'known', value }, availability: availability() });
const unknown = <T>(structural = true): TxAuxField<T> => ({
  reading: { status: 'unknown' }, availability: availability(structural, false),
});

const indicator = (receiver: 'MAIN' | 'SUB'): ReceiverIndicatorViewModel => ({
  receiver,
  availability: availability(true, receiver === 'MAIN'),
  sMeter: receiver === 'MAIN' ? known(-18) : unknown(),
  bandwidthHz: known(receiver === 'MAIN' ? 2400 : 500),
  agcMode: known('MID'),
  nbActive: known(false),
  nrActive: known(receiver === 'MAIN'),
  notchMode: known('off'),
  attenuator: known(0),
  preamp: known(receiver === 'MAIN' ? 1 : 0),
  rfGain: known(0.72),
  digiSel: unknown(false),
  ipPlus: known(false),
});

function view(): RadioViewModel {
  return {
    topologyId: '2/main_sub',
    vfoScheme: 'main_sub',
    activeReceiver: { status: 'known', receiver: 'MAIN' },
    vfos: [
      {
        receiver: 'MAIN', slot: { kind: 'unslotted' }, label: 'MAIN',
        frequencyHz: 14_250_000, mode: 'USB', filter: 'FIL1',
        isActive: true, isActiveSlot: true, isTxTarget: false,
      },
      {
        receiver: 'SUB', slot: { kind: 'unslotted' }, label: 'SUB',
        frequencyHz: 14_195_500, mode: 'CW', filter: 'FIL2',
        isActive: false, isActiveSlot: true, isTxTarget: true,
      },
    ],
    split: { status: 'known', value: true },
    dualWatch: { status: 'known', value: false },
    txTarget: {
      status: 'known', receiver: 'SUB', slot: { kind: 'unslotted' }, frequencyHz: 14_195_500,
    },
    txPermit: { status: 'allowed', band: '20m' },
    scope: { hardwareScope: availability(false, false), audioFftScope: availability() },
    disabledReasons: [],
    receiverIndicators: [indicator('MAIN'), indicator('SUB')],
    radioWideIndicators: {
      rfState: 'receiving',
      antenna: known(1),
      atu: known('off'),
      ritActive: known(true),
      ritOffset: known(250),
      xitActive: known(false),
      xitOffset: known(250),
      actions: {
        main: availability(), sub: availability(), equalize: availability(), swap: availability(),
        quickSplit: availability(false, false), quickDualWatch: availability(false, false),
        speak: availability(),
      },
    },
    txAux: {
      atu: known('off'), vox: known(false), voxGain: known(50), antiVoxGain: known(30),
      voxDelay: known(200), compressor: known(true), compressorLevel: known(4),
      monitor: known(false), monitorLevel: known(20), rfPower: known(55),
      micGain: known(44), driveGain: unknown(false),
    },
    meters: {
      rfState: 'receiving',
      signal: { ...known(-18), relevant: true },
      power: { ...unknown(), relevant: false },
      swr: { ...unknown(), relevant: false },
      alc: { ...unknown(), relevant: false },
      compression: { ...unknown(), relevant: false },
      drainVoltage: { ...known(13.8), relevant: true },
      drainCurrent: { ...known(0.7), relevant: true },
    },
    band: {
      currentBand: known('20m'), bandChoices: [], currentBandTx: 'allowed',
      tuneMinHz: 30_000, tuneMaxHz: 74_800_000,
    },
    filterPassband: {
      filterShape: known(1), filterShapeControlStructural: false,
      ifShift: known(250), ifShiftControlStructural: false,
      pbtInner: known(400), pbtOuter: known(-400), dataMode: unknown(false),
    },
  };
}

describe('projectPeerSplitDisplay', () => {
  it('projects exactly two truthful receiver columns and computes split delta from known facts', () => {
    const display = projectPeerSplitDisplay(view());
    expect(display.kind).toBe('peer-split');
    expect(display.receivers.map((receiver) => receiver.receiver)).toEqual(['MAIN', 'SUB']);
    expect(display.receivers[0]).toMatchObject({
      activity: 'active', frequency: { state: 'known', value: 14_250_000 },
      band: { state: 'known', value: '20m' },
      ifShiftHz: { state: 'known', value: 250 },
      pbtInnerHz: { state: 'known', value: 400 },
      pbtOuterHz: { state: 'known', value: -400 },
      spectrum: 'waiting',
    });
    expect(display.receivers[1]).toMatchObject({
      activity: 'inactive', frequency: { state: 'known', value: 14_195_500 },
      band: { state: 'unsupported' }, sMeter: { state: 'unknown' },
      spectrum: 'inactive',
    });
    expect(display.offsets.split).toEqual({ state: 'active', offsetHz: -54_500 });
  });

  it('keeps false, zero, unknown and structural absence distinct', () => {
    const display = projectPeerSplitDisplay(view());
    expect(display.top.vox).toEqual({ state: 'inactive' });
    expect(display.activeReceiver?.front.attenuator).toEqual({ state: 'known', value: 0 });
    expect(display.activeReceiver?.front.ipPlus).toEqual({ state: 'inactive' });
    expect(display.activeReceiver?.front.digiSel).toEqual({ state: 'unsupported' });
    expect(display.telemetry.power).toEqual({ state: 'unknown', relevant: false });
  });

  it('does not treat a hardware RF scope as an AF-FFT source', () => {
    const hardwareOnly = view();
    hardwareOnly.scope = {
      hardwareScope: availability(),
      audioFftScope: availability(false, false),
    };
    const display = projectPeerSplitDisplay(hardwareOnly);
    expect(display.receivers.map((receiver) => receiver.spectrum)).toEqual([
      'unsupported', 'unsupported',
    ]);

    hardwareOnly.activeReceiver = { status: 'unknown' };
    expect(projectPeerSplitDisplay(hardwareOnly).receivers.map((receiver) => receiver.spectrum)).toEqual([
      'unsupported', 'unsupported',
    ]);
  });

  it('keeps an available AF-FFT slot waiting until real bins reach the passive renderer', () => {
    const display = projectPeerSplitDisplay(view());
    expect(display.receivers[0].spectrum).toBe('waiting');
    expect(display.receivers[1].spectrum).toBe('inactive');
  });

  it('does not invent a zero split delta when split is known off', () => {
    const splitOff = view();
    splitOff.split = { status: 'known', value: false };
    const display = projectPeerSplitDisplay(splitOff);

    expect(display.offsets.split).toEqual({ state: 'inactive' });
    expect(display.offsets.split).not.toHaveProperty('offsetHz');
  });

  it('fails closed when the active receiver is unknown', () => {
    const unknownActive = view();
    unknownActive.activeReceiver = { status: 'unknown' };
    const display = projectPeerSplitDisplay(unknownActive);

    expect(display.activeReceiver).toBeNull();
    expect(display.offsets.split).toEqual({ state: 'unknown' });
    expect(display.receivers.map((receiver) => receiver.activity)).toEqual(['unknown', 'unknown']);
  });

  it('contains no callbacks, command availability or fabricated demo domains', () => {
    const display = projectPeerSplitDisplay(view());
    const json = JSON.stringify(display);
    expect(json).not.toMatch(/actions|quickSplit|quickDualWatch|callback|handler/i);
    expect(json).not.toMatch(/temperature|bandEdge|memoryChannel|fftBins/i);
    const visit = (value: unknown): void => {
      if (typeof value === 'function') throw new Error('display model contains a function');
      if (Array.isArray(value)) value.forEach(visit);
      else if (value && typeof value === 'object') Object.values(value).forEach(visit);
    };
    expect(() => visit(display)).not.toThrow();
  });
});

function singleAb(active: 'A' | 'B' | null, knownB = true): RadioViewModel {
  const fixture = topologyFixtures['1/ab'];
  return {
    ...view(), ...fixture,
    scope: view().scope,
    receiverIndicators: [indicator('MAIN')],
    vfos: fixture.vfos.map((vfo) => ({
      ...vfo,
      frequencyHz: vfo.slot.kind === 'slotted' && vfo.slot.id === 'B' && !knownB
        ? null : vfo.frequencyHz,
      isActive: vfo.slot.kind === 'slotted' && vfo.slot.id === active,
      isActiveSlot: vfo.slot.kind === 'slotted' && vfo.slot.id === active,
    })),
  };
}

describe('single-receiver LCD display slots', () => {
  it.each(['A', 'B'] as const)('keeps A left and B right with %s active without duplicating receiver facts', (active) => {
    const display = projectPeerSplitDisplay(singleAb(active));
    expect(display.receivers.map((slot) => slot.label)).toEqual(['VFO A', 'VFO B']);
    expect(display.receivers.map((slot) => slot.receiver)).toEqual(['MAIN', 'MAIN']);
    expect(display.receivers.map((slot) => slot.vfoSlot)).toEqual(['A', 'B']);
    expect(display.receivers.map((slot) => slot.frequency)).toEqual([
      { state: 'known', value: 7_100_000 }, { state: 'known', value: 7_150_000 },
    ]);
    expect(display.activeReceiver?.vfoSlot).toBe(active);
    for (const slot of display.receivers) {
      const selected = slot.vfoSlot === active;
      expect(slot.activity).toBe(selected ? 'active' : 'inactive');
      expect(slot.operational).toBe(selected);
      expect(slot.sMeter).toEqual(selected ? { state: 'known', value: -18 } : { state: 'unknown' });
      expect(slot.bandwidthHz.state).toBe(selected ? 'known' : 'unknown');
      expect(slot.dsp.agc.state).toBe(selected ? 'known' : 'unknown');
      expect(slot.front.rfGain.state).toBe(selected ? 'known' : 'unknown');
      expect(slot.spectrum).toBe(selected ? 'waiting' : 'inactive');
    }
  });

  it.each(['A', 'B'] as const)('keeps unobserved B unknown with %s active', (active) => {
    const display = projectPeerSplitDisplay(singleAb(active, false));
    expect(display.receivers[0].frequency).toEqual({ state: 'known', value: 7_100_000 });
    expect(display.receivers[1].frequency).toEqual({ state: 'unknown' });
  });

  it('does not assign MAIN telemetry or FFT when the active A/B slot is unobserved', () => {
    const display = projectPeerSplitDisplay(singleAb(null));
    expect(display.activeReceiver).toBeNull();
    for (const slot of display.receivers) {
      expect(slot.activity).toBe('unknown');
      expect(slot.sMeter).toEqual({ state: 'unknown' });
      expect(slot.spectrum).toBe('unknown');
    }
  });

  it('does not fill a missing B observation with A or an unknown relative slot', () => {
    const fixture = singleAb('A');
    fixture.vfos = [fixture.vfos[0], {
      ...fixture.vfos[1], slot: { kind: 'unknown' }, frequencyHz: 9_999_000,
    }];
    expect(projectPeerSplitDisplay(fixture).receivers[1].frequency).toEqual({ state: 'unknown' });
  });
});
