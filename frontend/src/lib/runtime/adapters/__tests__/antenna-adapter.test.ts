/**
 * MOR-1262 decomposition slice 8A (MOR-1295) — `antenna` fact-group adapter
 * derivation.
 *
 * Companion to `rit-xit-adapter.test.ts`/`rf-front-end-adapter.test.ts`,
 * which this file does NOT modify. `antenna` is a SEPARATE optional group —
 * see `radio-view-model.ts`'s `AntennaViewModel` doc comment.
 *
 * PARITY — the parity pin below calls the REAL `toAntennaProps`
 * (`lib/runtime/props/panel-props.ts`), never a reimplementation.
 *
 * Neither `txAntenna`/`rxAntenna1`/`rxAntenna2` nor `caps.antennas`/
 * `rx_antenna` consume a capabilities-STORE-backed helper, so this file
 * never calls the real `setCapabilities` and does not need the isolated
 * pool (MOR-1272).
 */
import { describe, expect, it } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import type { FieldStatus, ServerState } from '$lib/types/state';
import { validateRadioViewModel, type RadioViewModel } from '../../../../semantic/radio-view-model';
import { toRadioViewModel } from '../radio-view-model-adapter';
import { toAntennaProps } from '../../props/panel-props';

function caps(overrides: Partial<Capabilities> = {}): Capabilities {
  return {
    model: 'fixture', scope: true, audio: true, tx: true, capabilities: ['scope', 'audio', 'tx'],
    receivers: 1, vfoScheme: 'single', freqRanges: [], modes: [], filters: [],
    audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
    webrtc: { available: false, enabled: false },
    txBands: [], scopeSource: 'hardware', audioFftAvailable: false, ...overrides,
  } as Capabilities;
}

const fresh: FieldStatus = { storePath: 'x', observed: true, freshness: 'fresh', availability: 'available' };
const stale: FieldStatus = { storePath: 'x', observed: true, freshness: 'stale', availability: 'stale' };

function bareState(overrides: Partial<ServerState> = {}): ServerState {
  return {
    active: 'MAIN', split: false, dualWatch: false, ptt: false,
    txTarget: { status: 'known', receiver: 'MAIN', slot: null, frequencyHz: 14195000 },
    main: {
      freqHz: 14195000, mode: 'USB', filter: 1, dataMode: 0, att: 0, preamp: 0,
      nb: false, nr: false, afLevel: 1, rfGain: 1, squelch: 0, sMeter: 0,
    },
    sub: {
      freqHz: 7100000, mode: 'LSB', filter: 2, dataMode: 0, att: 0, preamp: 0,
      nb: false, nr: false, afLevel: 1, rfGain: 1, squelch: 0, sMeter: 0,
    },
    fieldStatus: {
      active: fresh, split: fresh, dualWatch: fresh, txTarget: fresh,
      'main.freqHz': fresh, 'main.mode': fresh, 'main.filter': fresh,
    },
    ...overrides,
  } as ServerState;
}

function model(state: ServerState | null, capabilities: Capabilities | null): RadioViewModel {
  const view = toRadioViewModel(state, capabilities);
  expect(view).not.toBeNull();
  return validateRadioViewModel(view);
}

describe('antenna evidence gate (MOR-1295, N3)', () => {
  it('emits no antenna when capabilities are absent', () => {
    expect(toRadioViewModel(bareState(), null)).toBeNull();
  });

  it('emits no antenna for a baseline radio with no declared antenna port count (regression pin)', () => {
    const view = model(bareState(), caps());
    expect(view.antenna).toBeUndefined();
    expect(Object.keys(view)).not.toContain('antenna');
  });

  it('emits no antenna for a radio explicitly declaring exactly one port — nothing to select', () => {
    const view = model(bareState(), caps({ antennas: 1 }));
    expect(view.antenna).toBeUndefined();
  });

  it('emits no antenna for the rx_antenna capability ALONE, without a multi-port declaration — matches the nested v2 gate', () => {
    const view = model(bareState(), caps({ capabilities: ['rx_antenna'] }));
    expect(view.antenna).toBeUndefined();
  });

  it('emits antenna once antennas > 1 is declared', () => {
    const view = model(bareState(), caps({ antennas: 2 }));
    expect(view.antenna).toBeDefined();
    expect(view.antenna!.antennaCount).toBe(2);
  });
});

describe('antenna per-field structural gates (MOR-1295)', () => {
  it('rxAnt is structurally absent without the rx_antenna capability, even though txAntenna is present', () => {
    const view = model(bareState(), caps({ antennas: 2 }));
    expect(view.antenna!.rxAnt.availability.structural).toBe(false);
    expect(view.antenna!.txAntenna.availability.structural).toBe(true);
  });

  it('rxAnt is structurally present once rx_antenna is declared alongside a multi-port radio', () => {
    const view = model(bareState(), caps({ antennas: 2, capabilities: ['rx_antenna'] }));
    expect(view.antenna!.rxAnt.availability.structural).toBe(true);
  });
});

describe('antenna per-field derivation (MOR-1295)', () => {
  const fullCaps = caps({ antennas: 2, capabilities: ['rx_antenna'] });

  it('reports known readings for observed, fresh fields — parity with the real toAntennaProps', () => {
    const state = bareState({
      txAntenna: 1, rxAntenna1: true,
      fieldStatus: { ...bareState().fieldStatus, txAntenna: fresh, rxAntenna1: fresh },
    });
    const real = toAntennaProps(state, fullCaps);
    const view = model(state, fullCaps);
    expect(view.antenna!.txAntenna.reading).toEqual({ status: 'known', value: real.txAntenna });
    expect(view.antenna!.rxAnt.reading).toEqual({ status: 'known', value: real.rxAnt });
  });

  it('reads rxAntenna2 (not rxAntenna1) once txAntenna selects port 2 — parity with the real toAntennaProps', () => {
    const state = bareState({
      txAntenna: 2, rxAntenna1: true, rxAntenna2: false,
      fieldStatus: { ...bareState().fieldStatus, txAntenna: fresh, rxAntenna2: fresh },
    });
    const real = toAntennaProps(state, fullCaps);
    const view = model(state, fullCaps);
    expect(real.rxAnt).toBe(false);
    expect(view.antenna!.rxAnt.reading).toEqual({ status: 'known', value: false });
  });

  it('degrades a stale txAntenna field to unknown while keeping structural availability true', () => {
    const state = bareState({
      txAntenna: 1, fieldStatus: { ...bareState().fieldStatus, txAntenna: stale },
    });
    const view = model(state, fullCaps);
    expect(view.antenna!.txAntenna).toEqual({
      reading: { status: 'unknown' }, availability: { structural: true, operational: false },
    });
  });

  it('marks rxAnt structurally absent when the rx_antenna capability is missing, never known', () => {
    const state = bareState({
      txAntenna: 1, rxAntenna1: true,
      fieldStatus: { ...bareState().fieldStatus, txAntenna: fresh, rxAntenna1: fresh },
    });
    const view = model(state, caps({ antennas: 2 }));
    expect(view.antenna!.rxAnt).toEqual({
      reading: { status: 'unknown' }, availability: { structural: false, operational: false },
    });
  });

  it('degrades a malformed raw txAntenna (wrong JS type) to unknown rather than coercing', () => {
    const state = bareState({
      txAntenna: '1' as unknown as number,
      fieldStatus: { ...bareState().fieldStatus, txAntenna: fresh },
    });
    const view = model(state, fullCaps);
    expect(view.antenna!.txAntenna.reading).toEqual({ status: 'unknown' });
  });

  it('emits a validator-clean model carrying the antenna group (round-trip proof)', () => {
    const state = bareState({
      txAntenna: 1, rxAntenna1: true,
      fieldStatus: { ...bareState().fieldStatus, txAntenna: fresh, rxAntenna1: fresh },
    });
    const view = model(state, fullCaps);
    expect(JSON.parse(JSON.stringify(view))).toEqual(view);
  });
});

/**
 * HONESTY — "never derive from a half-observed pair" (the 4A′/5A lesson,
 * applied here to `rxAnt`'s two-field dependency on `txAntenna`). An
 * unobserved `txAntenna` must never silently resolve to port 1's reading —
 * that is exactly the fabricated-default this contract exists to forbid.
 */
describe('antenna rxAnt fails closed on an unobserved txAntenna (half-observed-pair lesson)', () => {
  const fullCaps = caps({ antennas: 2, capabilities: ['rx_antenna'] });

  it('rxAnt reads unknown when txAntenna was never reported, even though rxAntenna1 is fresh and true', () => {
    const state = bareState({
      rxAntenna1: true, fieldStatus: { ...bareState().fieldStatus, rxAntenna1: fresh },
    });
    const view = model(state, fullCaps);
    expect(view.antenna!.txAntenna.reading).toEqual({ status: 'unknown' });
    expect(view.antenna!.rxAnt.reading).toEqual({ status: 'unknown' });
  });

  it('rxAnt reads unknown when txAntenna is stale, even though rxAntenna1 is fresh', () => {
    const state = bareState({
      txAntenna: 1, rxAntenna1: true,
      fieldStatus: { ...bareState().fieldStatus, txAntenna: stale, rxAntenna1: fresh },
    });
    const view = model(state, fullCaps);
    expect(view.antenna!.rxAnt.reading).toEqual({ status: 'unknown' });
  });

  it('rxAnt reads unknown when txAntenna selects port 2 but rxAntenna2 was never reported (port-2 half-observed variant)', () => {
    const state = bareState({
      txAntenna: 2, rxAntenna1: true,
      fieldStatus: { ...bareState().fieldStatus, txAntenna: fresh, rxAntenna1: fresh },
    });
    const view = model(state, fullCaps);
    expect(view.antenna!.rxAnt.reading).toEqual({ status: 'unknown' });
  });
});

/** HONESTY GATE — absent raw values never fabricate. */
describe('antenna honesty gate — absent raw values never fabricate', () => {
  it('txAntenna with nothing reported at all reads unknown, not {known, 1}', () => {
    const view = model(bareState(), caps({ antennas: 2 }));
    expect(view.antenna!.txAntenna.reading).toEqual({ status: 'unknown' });
  });
});
