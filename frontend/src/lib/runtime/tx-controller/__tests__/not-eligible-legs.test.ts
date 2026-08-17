/**
 * MOR-1792 — the `not-eligible` refusal must name WHICH eligibility leg failed.
 *
 * Bench context: three owner-present IC-7300 sessions on 2026-08-16 had the
 * first `Key transmitter` press refused as a bare `not-eligible` while every
 * SERVER-observable input was green (ptt=false fresh/confirmed, target known,
 * permit allowed, capabilities true, one stable control session). The failing
 * conjunct was controller-internal and therefore invisible, and three separate
 * diagnoses stalled there. This file pins the legs into the fault detail.
 *
 * SAFETY: this is observability ONLY. The `ok` predicate on the `start` branch
 * is untouched, and the mutation this file exists to kill is a future edit that
 * lets `ineligibility()` and `ok` drift apart — hence the exhaustive
 * equivalence matrix below, which asserts "reported no legs" ⇔ "the start was
 * accepted" over every combination of the predicate's inputs. If that ever
 * fails, the reporting is lying about the gate, which is worse than no
 * reporting at all.
 *
 * Host-independent: pure reducer, no DOM, no timers, no transport.
 */
import { describe, expect, it } from 'vitest';
import {
  initialTxState, transition,
  type Eligibility, type PttMarker, type PttObservation, type TxIneligibility, type TxState, type TxTarget,
} from '../model';

const marker = (seq: number, authorityEpoch = 1): PttMarker => ({
  authorityEpoch, pttObservationSeq: seq, pttLastObservedMonotonic: seq,
});
const target: Exclude<TxTarget, null> = { receiver: 'MAIN', slot: 'A', frequencyHz: 14_194_000 };
const eligible: Eligibility = {
  catPtt: true, browserTxAudio: true, controlLive: true, permit: 'allowed', target,
};
const observation = (over: Partial<PttObservation> = {}): PttObservation => ({
  value: false, observed: true, fresh: true, source: 'radio-readback', marker: marker(2), ...over,
});

/** `radioTx: 'unknown'`, marker at seq 1 — the state a freshly built controller is in. */
const armed = (): TxState => initialTxState(1, marker(1));
/**
 * `radioTx: 'off'` with `pttMarker` at seq 2 — the state after ONE authoritative
 * PTT-off observation, i.e. the only state from which `currentConfirmedOff` can
 * hold. Built through the real reducer so it is a state the authority reaches.
 */
const confirmedOff = (): TxState => transition(armed(), {
  type: 'authority', epoch: 1, ptt: observation(), eligibility: eligible, offCommandId: 'off',
}).state;

const start = (state: TxState, eligibility: Eligibility, ptt: PttObservation) =>
  transition(state, { type: 'start', sourceId: 'test', leaseId: 'lease', intent: 'momentary', eligibility, ptt });
const legsOf = (state: TxState): readonly TxIneligibility[] => state.faultDetail ?? [];

describe('the accepted start carries no fault detail', () => {
  it('accepts a strictly newer authoritative PTT-off observation', () => {
    const accepted = start(armed(), eligible, observation());
    expect(accepted.state).toMatchObject({ phase: 'audio-start-pending', fault: null, faultDetail: null });
  });

  it('accepts the already-confirmed-off observation the controller has already applied', () => {
    const accepted = start(confirmedOff(), eligible, observation());
    expect(accepted.state).toMatchObject({ phase: 'audio-start-pending', fault: null, faultDetail: null });
  });
});

describe('each failing conjunct is named in isolation', () => {
  const cases: ReadonlyArray<readonly [string, () => TxState, TxIneligibility]> = [
    ['CAT PTT unavailable', () => start(armed(), { ...eligible, catPtt: false }, observation()).state, 'cat-ptt-unavailable'],
    ['browser TX audio unavailable', () => start(armed(), { ...eligible, browserTxAudio: false }, observation()).state, 'browser-tx-audio-unavailable'],
    ['control session not live', () => start(armed(), { ...eligible, controlLive: false }, observation()).state, 'control-not-live'],
    ['permit denied', () => start(armed(), { ...eligible, permit: 'denied' }, observation()).state, 'tx-permit-not-allowed'],
    ['permit unknown', () => start(armed(), { ...eligible, permit: 'unknown' }, observation()).state, 'tx-permit-not-allowed'],
    ['TX target unknown', () => start(armed(), { ...eligible, target: null }, observation()).state, 'tx-target-unknown'],
    ['radio reports PTT on', () => start(armed(), eligible, observation({ value: true })).state, 'ptt-not-off'],
    ['observation never observed', () => start(armed(), eligible, observation({ observed: false })).state, 'ptt-not-authoritative'],
    ['observation not fresh', () => start(armed(), eligible, observation({ fresh: false })).state, 'ptt-not-authoritative'],
    ['observation from a non-authoritative source', () => start(armed(), eligible, observation({ source: 'other' })).state, 'ptt-not-authoritative'],
    ['no newer observation and no confirmed off', () => start(armed(), eligible, observation({ marker: marker(1) })).state, 'no-confirmed-ptt-off'],
    ['observation from another authority epoch', () => start(armed(), eligible, observation({ marker: marker(2, 2) })).state, 'authority-epoch-mismatch'],
  ];
  for (const [name, run, leg] of cases) {
    it(`names ${leg} when ${name}`, () => {
      const state = run();
      expect(state).toMatchObject({ phase: 'failed', fault: 'not-eligible' });
      expect(legsOf(state)).toContain(leg);
    });
  }

  /**
   * The MOR-1792 bench signature itself: every server-observable input green,
   * a marker the controller has already confirmed OFF, and the ONLY failing
   * conjunct is that the held observation is not `fresh`. Before this change
   * that refusal was indistinguishable from a denied permit or a dead session.
   */
  it('names exactly the stale-observation leg on the observed bench signature', () => {
    const state = start(confirmedOff(), eligible, observation({ fresh: false })).state;
    expect(state.fault).toBe('not-eligible');
    expect(legsOf(state)).toEqual(['ptt-not-authoritative']);
  });

  it('reports every failing leg, not only the first', () => {
    const state = start(armed(), { ...eligible, catPtt: false, permit: 'denied' }, observation({ value: true, marker: marker(1) })).state;
    expect(legsOf(state)).toEqual([
      'cat-ptt-unavailable', 'tx-permit-not-allowed', 'ptt-not-off', 'no-confirmed-ptt-off',
    ]);
  });
});

describe('the reported legs are exactly the gate', () => {
  /**
   * Exhaustive over every input the `start` predicate reads — each capability
   * flag, both permit failure modes, PTT value, all three authoritativeness
   * knobs, marker ordering (older/equal/newer) and epoch agreement — across two
   * base states (radioTx unknown vs already-confirmed-off). "No legs
   * reported" must mean "accepted", and every
   * refusal must name at least one leg — otherwise the fault detail is either
   * over-reporting (an operator chasing a leg that did not block them) or
   * under-reporting (the eight wasted bench minutes, again).
   */
  it('reports an empty leg set if and only if the start is accepted', () => {
    const bases: ReadonlyArray<readonly [string, () => TxState]> = [['armed', armed], ['confirmed-off', confirmedOff]];
    let accepted = 0; let refused = 0;
    for (const [baseName, base] of bases)
      for (const catPtt of [true, false])
        for (const browserTxAudio of [true, false])
          for (const controlLive of [true, false])
            for (const permit of ['allowed', 'denied', 'unknown'] as const)
              for (const slot of [target, null])
                for (const value of [false, true])
                  for (const observed of [true, false])
                    for (const fresh of [true, false])
                      for (const source of ['radio-readback', 'backend-observation', 'other'] as const)
                        for (const epoch of [1, 2])
                          for (const seq of [1, 2, 3]) {
                            const state = base();
                            const result = start(
                              state,
                              { catPtt, browserTxAudio, controlLive, permit, target: slot },
                              { value, observed, fresh, source, marker: marker(seq, epoch) },
                            );
                            const legs = legsOf(result.state);
                            const ok = result.state.phase === 'audio-start-pending';
                            const where = `${baseName} ${JSON.stringify({ catPtt, browserTxAudio, controlLive, permit, slot: slot !== null, value, observed, fresh, source, epoch, seq })}`;
                            expect(legs.length === 0, where).toBe(ok);
                            expect(result.state.fault, where).toBe(ok ? null : 'not-eligible');
                            if (ok) accepted += 1; else refused += 1;
                          }
    // Both outcomes must actually occur, or the matrix proves nothing.
    expect(accepted).toBeGreaterThan(0);
    expect(refused).toBeGreaterThan(0);
  });
});

describe('the fault detail does not outlive the refusal', () => {
  it('clears on reset-fault', () => {
    const refused = start(armed(), { ...eligible, permit: 'denied' }, observation()).state;
    expect(legsOf(refused).length).toBeGreaterThan(0);
    const reset = transition(refused, { type: 'reset-fault' }).state;
    expect(reset).toMatchObject({ phase: 'idle', fault: null, faultDetail: null });
  });

  it('clears on the next accepted start', () => {
    const refused = start(armed(), { ...eligible, permit: 'denied' }, observation()).state;
    const reset = transition(refused, { type: 'reset-fault' }).state;
    const accepted = start(reset, eligible, observation({ marker: marker(3) })).state;
    expect(accepted).toMatchObject({ phase: 'audio-start-pending', fault: null, faultDetail: null });
  });
});
