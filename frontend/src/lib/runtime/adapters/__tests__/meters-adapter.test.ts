/**
 * MOR-1262 decomposition slice 2A — `meters` fact-group adapter derivation.
 *
 * Companion to `radio-view-model-adapter.test.ts` (MOR-1065) and
 * `tx-aux-adapter.test.ts` (MOR-1244), neither of which this file modifies.
 * Those files never pass a TX authority snapshot, so `deriveMeters` declines
 * to emit for them and their exact-key-list assertions stand unchanged.
 *
 * The first describe block is the SAFETY block (invariant R9, MOR-1235): the
 * discriminating pair proves the group's TX truth comes from the App TX
 * authority and from nowhere else — sever it and route `state.ptt` back in,
 * and both halves go red in opposite directions.
 */
import { describe, expect, it } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import type { FieldStatus, ServerState } from '$lib/types/state';
import { validateRadioViewModel, type RadioViewModel } from '../../../../semantic/radio-view-model';
import { toRadioViewModel, type MetersTxAuthority } from '../radio-view-model-adapter';

function caps(overrides: Partial<Capabilities> = {}): Capabilities {
  return {
    model: 'fixture', scope: true, audio: true, tx: true, capabilities: ['scope', 'audio', 'tx'],
    stateContractVersion: 1, providerGeneration: 1,
    receivers: 1, vfoScheme: 'single', freqRanges: [], modes: [], filters: [],
    audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
    webrtc: { available: false, enabled: false },
    txBands: [{ start: 14000000, end: 14350000, name: '20m' }],
    scopeSource: 'hardware', audioFftAvailable: false, ...overrides,
  } as Capabilities;
}

const fresh: FieldStatus = {
  storePath: 'x', observed: true, freshness: 'fresh', availability: 'available',
  lastObservedMonotonic: 0,
};
const stale: FieldStatus = {
  storePath: 'x', observed: true, freshness: 'stale', availability: 'stale',
  lastObservedMonotonic: 0,
};
const METER_PATHS = [
  'main.sMeter', 'sub.sMeter', 'powerMeter', 'swrMeter', 'alcMeter',
  'compMeter', 'vdMeter', 'idMeter',
] as const;

/** The RX authority: positively observed OFF with zero TX risk. */
const RX: MetersTxAuthority = { radioTx: 'off', txRisk: 'none' };
/** The TX authority: the transmitter is confirmed on. */
const TX: MetersTxAuthority = { radioTx: 'on', txRisk: 'confirmed-on' };

function meterState(overrides: Partial<ServerState> = {}): ServerState {
  return {
    stateContractVersion: 1, providerGeneration: 1,
    active: 'MAIN', split: false, dualWatch: false, ptt: false,
    txTarget: { status: 'known', receiver: 'MAIN', slot: null, frequencyHz: 14195000 },
    main: {
      freqHz: 14195000, mode: 'USB', filter: 1, dataMode: 0, att: 0, preamp: 0,
      nb: false, nr: false, afLevel: 1, rfGain: 1, squelch: 0, sMeter: 120,
    },
    powerMeter: 0.6, swrMeter: 20, alcMeter: 40, compMeter: 10, vdMeter: 200, idMeter: 80,
    fieldStatus: {
      active: fresh, split: fresh, dualWatch: fresh, txTarget: fresh,
      'main.freqHz': fresh, 'main.mode': fresh, 'main.filter': fresh,
      ...Object.fromEntries(METER_PATHS.map((path) => [path, fresh])),
    },
    ...overrides,
  } as ServerState;
}

function model(
  state: ServerState | null, capabilities: Capabilities | null, tx?: MetersTxAuthority | null,
): RadioViewModel {
  const view = toRadioViewModel(state, capabilities, tx);
  expect(view).not.toBeNull();
  return validateRadioViewModel(view);
}

describe('meters TX truth comes from the App TX authority (R9 / MOR-1235)', () => {
  // ── The discriminating pair ─────────────────────────────────────────────
  it('reads TX-active from the authority even while radioState.ptt is stale FALSE', () => {
    const view = model(meterState({ ptt: false }), caps(), TX);
    const meters = view.meters!;
    expect(meters.rfState).toBe('transmitting');
    expect(meters.power.relevant).toBe(true);
    expect(meters.swr.relevant).toBe(true);
    expect(meters.alc.relevant).toBe(true);
    expect(meters.compression.relevant).toBe(true);
    expect(meters.drainCurrent.relevant).toBe(true);
    // ...and the RX meter is the one that steps aside.
    expect(meters.signal.relevant).toBe(false);
  });

  it('reads RX from the authority even while radioState.ptt is stale TRUE', () => {
    const view = model(meterState({ ptt: true }), caps(), RX);
    const meters = view.meters!;
    expect(meters.rfState).toBe('receiving');
    expect(meters.power.relevant).toBe(false);
    expect(meters.swr.relevant).toBe(false);
    expect(meters.alc.relevant).toBe(false);
    expect(meters.compression.relevant).toBe(false);
    expect(meters.drainCurrent.relevant).toBe(false);
    expect(meters.signal.relevant).toBe(true);
  });

  it('fails closed on an uncertain authority: the TX fault meters stay relevant', () => {
    const view = model(meterState({ ptt: false }), caps(), { radioTx: 'off', txRisk: 'uncertain' });
    expect(view.meters!.rfState).toBe('uncertain');
    expect(view.meters!.swr.relevant).toBe(true);
    expect(view.meters!.alc.relevant).toBe(true);
    expect(view.meters!.signal.relevant).toBe(false);
  });

  it('fails closed on an unobserved RF state', () => {
    const view = model(meterState(), caps(), { radioTx: 'unknown', txRisk: 'none' });
    expect(view.meters!.rfState).toBe('unknown');
    expect(view.meters!.power.relevant).toBe(true);
  });

  it('emits NO meters group without an authority snapshot — never a ptt-derived guess', () => {
    for (const tx of [undefined, null]) {
      const view = model(meterState({ ptt: true }), caps(), tx);
      expect(view.meters).toBeUndefined();
      expect(Object.keys(view)).not.toContain('meters');
    }
  });
});

describe('target meter display provenance (MOR-2359)', () => {
  const targets = [['power', 'powerMeter'], ['swr', 'swrMeter'], ['alc', 'alcMeter']] as const;
  const observed = { ...fresh, lastObservedMonotonic: 310658.42975425 };
  const capabilities = caps();
  function stateWith(status: Partial<FieldStatus> = {}): ServerState {
    return meterState({
      fieldStatus: { ...meterState().fieldStatus,
        ...Object.fromEntries(targets.map(([, path]) => [path, { ...observed, ...status }])),
      },
    });
  }
  it.each(targets)('projects current/stale/never-observed independently for %s', (meter, path) => {
    const state = stateWith();
    expect(model(state, capabilities, TX).meters![meter].display).toEqual({ state: 'current', value: state[path] });
    for (const status of [{ freshness: 'stale' }, { availability: 'stale' }] as const) {
      expect(model(stateWith(status), capabilities, TX).meters![meter].display).toEqual({ state: 'stale', value: state[path] });
    }
    expect(model(stateWith({ observed: false }), capabilities, TX).meters![meter].display)
      .toEqual({ state: 'unknown', reason: 'not-observed' });
    expect(model({ ...state, fieldStatus: {} }, capabilities, TX).meters![meter].display)
      .toEqual({ state: 'unknown', reason: 'not-observed' });
  });
  it.each(targets)('preserves valid zero and rejects invalid %s scalars', (meter, path) => {
    expect(model({ ...stateWith(), [path]: 0 }, capabilities, TX).meters![meter].display)
      .toEqual({ state: 'current', value: 0 });
    for (const value of [NaN, Infinity, null, '1', false]) {
      expect(model({ ...stateWith(), [path]: value }, capabilities, TX).meters![meter].display)
        .toEqual({ state: 'unknown', reason: 'invalid-value' });
    }
    expect(model({ ...stateWith(), [path]: undefined }, capabilities, TX).meters![meter].display)
      .toEqual({ state: 'unsupported' });
    expect(model(stateWith(), { ...capabilities, tx: false }, TX).meters![meter].display)
      .toEqual({ state: 'unsupported' });
  });
  it('requires observation markers and matching identity but not active receiver freshness', () => {
    for (const lastObservedMonotonic of [null, undefined, NaN, Infinity, -1]) {
      expect(model(stateWith({ lastObservedMonotonic }), capabilities, TX).meters!.power.display)
        .toEqual({ state: 'unknown', reason: 'invalid-evidence' });
    }
    for (const providerGeneration of [undefined, 2]) {
      expect(model({ ...stateWith(), providerGeneration }, capabilities, TX).meters!.power.display)
        .toEqual({ state: 'unknown', reason: 'identity-unresolved' });
    }
    const state = stateWith();
    state.fieldStatus!.active = { ...stale, observed: false };
    expect(model(state, capabilities, TX).meters!.power.display).toEqual({ state: 'current', value: 0.6 });
  });
  it('changes no strict meter facts across RF authority states', () => {
    for (const tx of [RX, TX, { radioTx: 'off', txRisk: 'uncertain' }, { radioTx: 'unknown', txRisk: 'none' }] as const) {
      for (const status of [{}, { freshness: 'stale', availability: 'stale' }, { observed: false }] as const) {
        const state = stateWith(status);
        const meters = model(state, capabilities, tx).meters!;
        const relevant = meters.rfState !== 'receiving';
        for (const [meter, path] of targets) {
          const { display, ...strict } = meters[meter];
          expect(display).toBeDefined();
          const operational = status.observed !== false
            && !('freshness' in status && status.freshness === 'stale');
          expect(strict).toEqual({
            reading: operational ? { status: 'known', value: state[path] } : { status: 'unknown' },
            availability: { structural: true, operational }, relevant,
          });
        }
        for (const meter of ['signal', 'compression', 'drainVoltage', 'drainCurrent'] as const) {
          expect(meters[meter]).not.toHaveProperty('display');
        }
      }
    }
  });
});

describe('meters evidence gate and per-meter derivation (MOR-1262 slice 2A)', () => {
  it('emits no meters when capabilities are absent', () => {
    expect(toRadioViewModel(meterState(), null, RX)).toBeNull();
  });

  it('emits no meters when the radio has reported no meter at all', () => {
    const bare = meterState({
      powerMeter: undefined, swrMeter: undefined, alcMeter: undefined,
      compMeter: undefined, vdMeter: undefined, idMeter: undefined,
      main: { freqHz: 14195000, mode: 'USB', filter: 1 } as ServerState['main'],
    });
    expect(model(bare, caps(), RX).meters).toBeUndefined();
  });

  it('emits the group once a single meter is reported', () => {
    const one = meterState({
      powerMeter: undefined, swrMeter: undefined, alcMeter: undefined,
      compMeter: undefined, idMeter: undefined,
      main: { freqHz: 14195000, mode: 'USB', filter: 1 } as ServerState['main'],
    });
    expect(model(one, caps(), RX).meters?.drainVoltage.reading).toEqual({ status: 'known', value: 200 });
  });

  it('reports known readings for every reported meter', () => {
    const meters = model(meterState(), caps(), TX).meters!;
    expect(meters.signal.reading).toEqual({ status: 'known', value: 120 });
    expect(meters.power.reading).toEqual({ status: 'known', value: 0.6 });
    expect(meters.swr.reading).toEqual({ status: 'known', value: 20 });
    expect(meters.alc.reading).toEqual({ status: 'known', value: 40 });
    expect(meters.compression.reading).toEqual({ status: 'known', value: 10 });
    expect(meters.drainVoltage.reading).toEqual({ status: 'known', value: 200 });
    expect(meters.drainCurrent.reading).toEqual({ status: 'known', value: 80 });
  });

  it('marks TX meters structurally absent on a receive-only radio, never known', () => {
    const meters = model(meterState(), caps({ tx: false, capabilities: ['scope', 'audio'] }), RX).meters!;
    for (const field of [meters.power, meters.swr, meters.alc, meters.compression, meters.drainCurrent]) {
      expect(field.availability).toEqual({ structural: false, operational: false });
      expect(field.reading).toEqual({ status: 'unknown' });
    }
    // The S-meter and the supply rail are not TX facts and survive.
    expect(meters.signal.availability.structural).toBe(true);
    expect(meters.drainVoltage.availability.structural).toBe(true);
  });

  it('marks an unreported meter structurally absent rather than zero', () => {
    const meters = model(meterState({ alcMeter: undefined }), caps(), TX).meters!;
    expect(meters.alc).toEqual({
      reading: { status: 'unknown' }, availability: { structural: false, operational: false }, relevant: true,
      display: { state: 'unsupported' },
    });
  });

  it('degrades a stale meter to unknown while keeping structural availability', () => {
    const meters = model(meterState({
      fieldStatus: { ...meterState().fieldStatus, swrMeter: stale },
    }), caps(), TX).meters!;
    expect(meters.swr).toEqual({
      reading: { status: 'unknown' }, availability: { structural: true, operational: false }, relevant: true,
      display: { state: 'stale', value: 20 },
    });
  });

  it('follows the active receiver for the S-meter, with its own field status', () => {
    const dualCaps = caps({
      receivers: 2, vfoScheme: 'main_sub',
      capabilities: ['scope', 'audio', 'tx', 'dual_rx'],
    });
    const onSub = meterState({
      active: 'SUB',
      sub: { freqHz: 7100000, mode: 'LSB', filter: 1, sMeter: 60 } as ServerState['main'],
    });
    expect(model(onSub, dualCaps, RX).meters!.signal.reading).toEqual({ status: 'known', value: 60 });
    const staleSub = meterState({
      active: 'SUB',
      sub: { freqHz: 7100000, mode: 'LSB', filter: 1, sMeter: 60 } as ServerState['main'],
      fieldStatus: { ...meterState().fieldStatus, 'sub.sMeter': stale },
    });
    expect(model(staleSub, dualCaps, RX).meters!.signal.reading).toEqual({ status: 'unknown' });
  });

  it.each(METER_PATHS.filter((path) => path !== 'sub.sMeter'))(
    'requires current leaf evidence for %s while preserving its structural shell', (path) => {
      const state = meterState();
      const fieldStatus = { ...state.fieldStatus };
      delete fieldStatus[path];
      const meters = model({ ...state, fieldStatus }, caps(), TX).meters!;
      const field = path === 'main.sMeter' ? meters.signal
        : path === 'powerMeter' ? meters.power
          : path === 'swrMeter' ? meters.swr
            : path === 'alcMeter' ? meters.alc
              : path === 'compMeter' ? meters.compression
                : path === 'vdMeter' ? meters.drainVoltage : meters.drainCurrent;
      expect(field.reading).toEqual({ status: 'unknown' });
      expect(field.availability).toEqual({ structural: true, operational: false });
    },
  );

  it.each(METER_PATHS.filter((path) => path !== 'sub.sMeter'))(
    'keeps stale %s structurally present but non-operational', (path) => {
      const state = meterState({
        fieldStatus: { ...meterState().fieldStatus, [path]: stale },
      });
      const meters = model(state, caps(), TX).meters!;
      const field = path === 'main.sMeter' ? meters.signal
        : path === 'powerMeter' ? meters.power
          : path === 'swrMeter' ? meters.swr
            : path === 'alcMeter' ? meters.alc
              : path === 'compMeter' ? meters.compression
                : path === 'vdMeter' ? meters.drainVoltage : meters.drainCurrent;
      expect(field.reading).toEqual({ status: 'unknown' });
      expect(field.availability).toEqual({ structural: true, operational: false });
    },
  );

  it.each([
    ['provider mismatch', { providerGeneration: 2 }],
    ['contract mismatch', { stateContractVersion: 2 }],
  ] as const)('%s fences every canonical reading without removing shells', (_label, capabilityOverride) => {
    const meters = model(meterState(), caps(capabilityOverride), TX).meters!;
    for (const field of [
      meters.signal, meters.power, meters.swr, meters.alc, meters.compression,
      meters.drainVoltage, meters.drainCurrent,
    ]) {
      expect(field.reading).toEqual({ status: 'unknown' });
      expect(field.availability).toEqual({ structural: true, operational: false });
    }
  });

  it.each([undefined, NaN, Infinity, -1])(
    'rejects invalid observation marker %s for every canonical reading', (lastObservedMonotonic) => {
      const state = meterState();
      const fieldStatus = Object.fromEntries(METER_PATHS.map((path) => [
        path, { ...fresh, lastObservedMonotonic },
      ]));
      const meters = model({ ...state, fieldStatus }, caps(), TX).meters!;
      expect([
        meters.signal, meters.power, meters.swr, meters.alc, meters.compression,
        meters.drainVoltage, meters.drainCurrent,
      ].every((field) => field.reading.status === 'unknown')).toBe(true);
    },
  );

  it('preserves valid zero for all seven canonical readings', () => {
    const state = meterState({
      main: { ...meterState().main, sMeter: 0 },
      powerMeter: 0, swrMeter: 0, alcMeter: 0, compMeter: 0, vdMeter: 0, idMeter: 0,
    });
    const meters = model(state, caps(), TX).meters!;
    expect([
      meters.signal, meters.power, meters.swr, meters.alc, meters.compression,
      meters.drainVoltage, meters.drainCurrent,
    ].map((field) => field.reading)).toEqual(Array(7).fill({ status: 'known', value: 0 }));
  });

  it('keeps an unresolved dual active receiver as an unknown supported signal shell', () => {
    const state = meterState({
      fieldStatus: { ...meterState().fieldStatus, active: { ...fresh, observed: false } },
      sub: { ...meterState().main, sMeter: 60 },
    });
    const dualCaps = caps({
      receivers: 2, vfoScheme: 'main_sub',
      capabilities: ['scope', 'audio', 'tx', 'dual_rx'],
    });
    expect(model(state, dualCaps, RX).meters!.signal).toEqual({
      reading: { status: 'unknown' },
      availability: { structural: true, operational: false },
      relevant: true,
    });
  });

  it('does not select raw SUB signal when SUB is structurally non-operational', () => {
    const state = meterState({
      active: 'SUB',
      sub: { ...meterState().main, sMeter: 60 },
    });
    const signal = model(state, caps({ receivers: 2, vfoScheme: 'main_sub' }), RX).meters!.signal;
    expect(signal).toEqual({
      reading: { status: 'unknown' },
      availability: { structural: true, operational: false },
      relevant: true,
    });
  });

  it('ignores a stray raw SUB signal outside the canonical single-receiver topology', () => {
    const state = meterState({
      active: 'SUB',
      main: { freqHz: 14195000, mode: 'USB', filter: 1 } as ServerState['main'],
      sub: { ...meterState().main, sMeter: 60 },
      powerMeter: undefined, swrMeter: undefined, alcMeter: undefined,
      compMeter: undefined, vdMeter: undefined, idMeter: undefined,
    });
    expect(model(state, caps(), RX).meters).toBeUndefined();
  });

  it('degrades a malformed raw value (wrong JS type) to unknown rather than coercing', () => {
    const meters = model(meterState({
      compMeter: 'ten' as unknown as number,
    }), caps(), TX).meters!;
    expect(meters.compression.reading).toEqual({ status: 'unknown' });
  });

  it('emits a validator-clean model carrying the meters group (round-trip proof)', () => {
    const view = model(meterState(), caps(), TX);
    expect(JSON.parse(JSON.stringify(view))).toEqual(view);
  });

  it('leaves the pre-2A families untouched when meters are present', () => {
    const view = model(meterState(), caps(), TX);
    expect(view.topologyId).toBe('1/single');
    expect(view.txAux).toBeUndefined();
  });
});
