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
    receivers: 1, vfoScheme: 'single', freqRanges: [], modes: [], filters: [],
    audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
    webrtc: { available: false, enabled: false },
    txBands: [{ start: 14000000, end: 14350000, name: '20m' }],
    scopeSource: 'hardware', audioFftAvailable: false, ...overrides,
  } as Capabilities;
}

const fresh: FieldStatus = { storePath: 'x', observed: true, freshness: 'fresh', availability: 'available' };
const stale: FieldStatus = { storePath: 'x', observed: true, freshness: 'stale', availability: 'stale' };

/** The RX authority: positively observed OFF with zero TX risk. */
const RX: MetersTxAuthority = { radioTx: 'off', txRisk: 'none' };
/** The TX authority: the transmitter is confirmed on. */
const TX: MetersTxAuthority = { radioTx: 'on', txRisk: 'confirmed-on' };

function meterState(overrides: Partial<ServerState> = {}): ServerState {
  return {
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
    });
  });

  it('degrades a stale meter to unknown while keeping structural availability', () => {
    const meters = model(meterState({
      fieldStatus: { ...meterState().fieldStatus, swrMeter: stale },
    }), caps(), TX).meters!;
    expect(meters.swr).toEqual({
      reading: { status: 'unknown' }, availability: { structural: true, operational: false }, relevant: true,
    });
  });

  it('follows the active receiver for the S-meter, with its own field status', () => {
    const onSub = meterState({
      active: 'SUB',
      sub: { freqHz: 7100000, mode: 'LSB', filter: 1, sMeter: 60 } as ServerState['main'],
    });
    expect(model(onSub, caps(), RX).meters!.signal.reading).toEqual({ status: 'known', value: 60 });
    const staleSub = meterState({
      active: 'SUB',
      sub: { freqHz: 7100000, mode: 'LSB', filter: 1, sMeter: 60 } as ServerState['main'],
      fieldStatus: { ...meterState().fieldStatus, 'sub.sMeter': stale },
    });
    expect(model(staleSub, caps(), RX).meters!.signal.reading).toEqual({ status: 'unknown' });
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
