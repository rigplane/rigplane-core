import { describe, expect, it } from 'vitest';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState } from '$lib/types/state';
import { createAppAuthorityProjector } from '../app-authority';
const session = (epoch: number, state: 'connected' | 'disconnected' = 'connected') => ({ epoch, state } as const);
const capabilities = (overrides: Partial<Capabilities> = {}): Capabilities => ({
  model: 'fixture', scope: false, audio: true, tx: true, audioTx: true,
  audioTxRoute: 'lan', audioTxRequiredModInputSource: 5,
  capabilities: ['tx', 'mod_input_routing'], receivers: 2, vfoScheme: 'main_sub',
  freqRanges: [], modes: [], filters: [], audioConfig: { sampleRate: 48000, channels: 1, codecs: ['pcm16'] },
  webrtc: { available: false, enabled: false },
  txBands: [{ name: 'test', start: 100, end: 200 }], ...overrides,
});
const field = (at: number, source = 'poll_response', overrides: Record<string, unknown> = {}) => ({
  storePath: 'global.tx_state.ptt', observed: true, freshness: 'fresh',
  availability: 'available', lastObservedMonotonic: at, source: { source, provider: 'fixture' }, ...overrides,
});
function radio(overrides: Record<string, unknown> = {}): ServerState {
  return {
    ptt: false, active: 'SUB',
    txTarget: { status: 'known', receiver: 'SUB', slot: 'B', frequencyHz: 150 },
    main: { dataMode: 0 }, sub: { dataMode: 1 }, data1ModInput: 5,
    fieldStatus: { txTarget: field(1), data1ModInput: field(1), ptt: field(1) }, ...overrides,
  } as unknown as ServerState;
}
const withPtt = (evidence: unknown, ptt = false) => radio({ ptt, fieldStatus: { ...radio().fieldStatus, ptt: evidence } });
describe('App TX authority projector', () => {
  it('reuses canonical capability, target, MOD-input, and session facts fail closed', () => {
    const project = createAppAuthorityProjector();
    const state = radio();
    const before = structuredClone(state);
    const result = project(state, capabilities(), session(1));
    expect(result).toMatchObject({
      epoch: 1, modInputSource: { status: 'known', source: 5 },
      facts: {
        catPttAvailable: true, browserTxAudioAvailable: true,
        modInputReadiness: { status: 'ready', source: 5 },
        txTarget: { status: 'known', receiver: 'SUB', slot: 'B', frequencyHz: 150 },
      },
      eligibility: {
        catPtt: true, browserTxAudio: true, controlLive: true,
        permit: 'allowed', target: { receiver: 'SUB', slot: 'B', frequencyHz: 150 },
      },
    });
    expect(state).toEqual(before);
    const stale = radio({ fieldStatus: {
      ...state.fieldStatus, txTarget: field(2, 'poll_response', { availability: 'stale' }),
    } });
    expect(project(stale, capabilities(), session(1)).eligibility.target).toBeNull();
    expect(project(state, capabilities({ vfoScheme: 'single', receivers: 1 }), session(1)).facts?.txTarget.status).toBe('unknown');
    expect(project(radio({ fieldStatus: { ...state.fieldStatus, data1ModInput: field(2, 'poll_response', { availability: 'stale' }) } }), capabilities(), session(1)).modInputSource).toEqual({ status: 'unknown' });
    expect(project(stale, capabilities({ tx: false }), session(1)).eligibility.catPtt).toBe(false);
    expect(project(null, null, session(1, 'disconnected'))).toMatchObject({
      eligibility: { catPtt: false, browserTxAudio: false, controlLive: false, target: null },
      facts: null, modInputSource: { status: 'unknown' },
    });
  });
  it('advances only for genuinely newer qualifying field-specific evidence', () => {
    const project = createAppAuthorityProjector();
    expect(project(radio(), capabilities(), session(1)).ptt).toMatchObject({
      observed: true, fresh: true, source: 'radio-readback',
      marker: { pttObservationSeq: 1 },
    });
    const newer = withPtt(field(2, 'civ_unsolicited'));
    expect(project(newer, capabilities(), session(1)).ptt).toMatchObject({
      value: false, observed: true, fresh: true, source: 'backend-observation',
      marker: { pttObservationSeq: 2, pttLastObservedMonotonic: 2 },
    });
    // MOR-1880: a repeated lastObservedMonotonic (the radio reporting the same
    // thing again, e.g. because a delta omitted this field) does not advance
    // the ordinal — but it is still `fresh`, since freshness now reads the
    // field's own status rather than delivery cadence.
    const duplicate = project(radio({ ptt: true, revision: 1_000, fieldStatus: { ...radio().fieldStatus, ptt: field(2, 'civ_unsolicited') } }), capabilities(), session(1));
    expect(duplicate.ptt).toMatchObject({ value: true, fresh: true, marker: { pttObservationSeq: 2 } });
    for (const malicious of ['toString', 'constructor', '__proto__'])
      expect(project(withPtt(field(3, malicious)), capabilities(), session(1)).ptt).toMatchObject({ source: 'other', fresh: false, marker: { pttObservationSeq: 2, pttLastObservedMonotonic: 2 } });
    expect(project(withPtt(field(3, 'hamlib_response')), capabilities(), session(1)).ptt.source).toBe('radio-readback');
    expect(project(withPtt(field(4, 'yaesu_poll_response')), capabilities(), session(1)).ptt.fresh).toBe(true);
  });
  it('rejects bad evidence outright; a new epoch or a repeated timestamp does not by itself make a reading non-fresh', () => {
    const rejected = [
      field(2, 'command_response'), field(3, 'state_poller'),
      field(4, 'local_reconcile'), field(5, 'test'), field(6, 'unknown'),
      field(Number.NaN), field(7, 'poll_response', { observed: false }),
      field(8, 'poll_response', { freshness: 'stale' }),
      field(9, 'poll_response', { availability: 'missing' }), undefined,
    ];
    for (const evidence of rejected) {
      const project = createAppAuthorityProjector();
      const first = project(withPtt(evidence), capabilities(), session(1));
      expect(first.ptt).toMatchObject({ fresh: false, marker: { pttObservationSeq: 0 } });
    }
    const project = createAppAuthorityProjector();
    project(radio(), capabilities(), session(1));
    project(withPtt(field(2)), capabilities(), session(1));
    // MOR-1880: the first qualifying projection of a new epoch (session(2), a
    // reconnect) is fresh — there is no more per-epoch baseline suppression.
    const afterEpochBump = project(withPtt(field(10)), capabilities(), session(2));
    expect(afterEpochBump.ptt).toMatchObject({ fresh: true, marker: { authorityEpoch: 2, pttObservationSeq: 3 } });
    // A stale session (epoch behind the projector's current epoch) still
    // reports controlLive: false and therefore non-fresh — unrelated to
    // decay or baseline, and unaffected by MOR-1880.
    expect(project(withPtt(field(11)), capabilities(), session(1)))
      .toMatchObject({ epoch: 2, eligibility: { controlLive: false }, ptt: { fresh: false } });
    // MOR-1880: repeating the SAME lastObservedMonotonic (field(10) again,
    // identical to afterEpochBump's) does not advance the ordinal, but it is
    // still fresh — this is the operator's actual bug: the radio reporting
    // the same thing because nothing changed must not read as stale.
    expect(project(withPtt(field(10)), capabilities(), session(2)).ptt)
      .toMatchObject({ fresh: true, marker: { pttObservationSeq: 3 } });
    expect(project(withPtt(field(12), true), capabilities(), session(2)).ptt)
      .toMatchObject({ value: true, fresh: true, marker: { pttObservationSeq: 4 } });
  });
  it('reports fresh: true on a repeated fieldStatus.ptt timestamp, not just the first (MOR-1880 decay regression)', () => {
    const project = createAppAuthorityProjector();
    project(withPtt(field(1)), capabilities(), session(1)); // epoch baseline
    project(withPtt(field(2)), capabilities(), session(1)); // genuinely newer, past the baseline
    const first = project(withPtt(field(5)), capabilities(), session(1));
    // Identical lastObservedMonotonic: the radio reporting the same thing
    // again because nothing changed, exactly as it does on every delta that
    // omits fieldStatus.ptt.
    const second = project(withPtt(field(5)), capabilities(), session(1));
    expect(first.ptt.fresh).toBe(true);
    expect(second.ptt.fresh).toBe(true);
  });
  it('reports fresh: true on the first qualifying projection after an epoch change (MOR-1880 baseline regression)', () => {
    const project = createAppAuthorityProjector();
    project(withPtt(field(1)), capabilities(), session(1));
    project(withPtt(field(2)), capabilities(), session(1));
    const afterReconnect = project(withPtt(field(3)), capabilities(), session(2)); // epoch bump 1 -> 2
    expect(afterReconnect.ptt.fresh).toBe(true);
  });
});
