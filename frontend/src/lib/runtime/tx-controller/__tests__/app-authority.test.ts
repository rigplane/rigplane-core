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
      observed: true, fresh: false, source: 'radio-readback',
      marker: { pttObservationSeq: 1 },
    });
    const newer = withPtt(field(2, 'civ_unsolicited'));
    expect(project(newer, capabilities(), session(1)).ptt).toMatchObject({
      value: false, observed: true, fresh: true, source: 'backend-observation',
      marker: { pttObservationSeq: 2, pttLastObservedMonotonic: 2 },
    });
    const duplicate = project(radio({ ptt: true, revision: 1_000, fieldStatus: { ...radio().fieldStatus, ptt: field(2, 'civ_unsolicited') } }), capabilities(), session(1));
    expect(duplicate.ptt).toMatchObject({ value: true, fresh: false, marker: { pttObservationSeq: 2 } });
    for (const malicious of ['toString', 'constructor', '__proto__'])
      expect(project(withPtt(field(3, malicious)), capabilities(), session(1)).ptt).toMatchObject({ source: 'other', fresh: false, marker: { pttObservationSeq: 2, pttLastObservedMonotonic: 2 } });
    expect(project(withPtt(field(3, 'hamlib_response')), capabilities(), session(1)).ptt.source).toBe('radio-readback');
    expect(project(withPtt(field(4, 'yaesu_poll_response')), capabilities(), session(1)).ptt.fresh).toBe(true);
  });
  it('rejects bad evidence and retains a per-epoch baseline against stale epochs', () => {
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
    const baseline = project(withPtt(field(10)), capabilities(), session(2));
    expect(baseline.ptt).toMatchObject({ fresh: false, marker: { authorityEpoch: 2, pttObservationSeq: 3 } });
    expect(project(withPtt(field(11)), capabilities(), session(1)))
      .toMatchObject({ epoch: 2, eligibility: { controlLive: false }, ptt: { fresh: false } });
    expect(project(withPtt(field(10)), capabilities(), session(2)).ptt.fresh).toBe(false);
    expect(project(withPtt(field(12), true), capabilities(), session(2)).ptt)
      .toMatchObject({ value: true, fresh: true, marker: { pttObservationSeq: 4 } });
  });
});
