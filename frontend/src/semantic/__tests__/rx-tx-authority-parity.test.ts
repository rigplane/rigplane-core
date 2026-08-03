/**
 * MOR-1064 — parity between the semantic RX/TX surface and the real App TX
 * authority.
 *
 * The component tests exercise hand-written snapshots; this file proves those
 * snapshots are not fiction. Every state below is produced by the ACTUAL TX
 * reducer (`lib/runtime/tx-controller/model`), so the surface's vocabulary is
 * pinned to states the authority can really reach — not to a convenient
 * subset. Production code never imports the reducer (ADR invariant 11, and
 * the semantic eslint zone forbids the app-host); only this test does.
 */
import { describe, expect, it } from 'vitest';
import {
  initialTxState, transition,
  type Eligibility, type PttMarker, type PttObservation, type TxFault, type TxState,
} from '$lib/runtime/tx-controller/model';
import {
  keyBlockedReasons, rfState, txOrigin, txSessionState,
  type TxAuthoritySnapshot,
} from '../rx-tx-surface';
import { topologyFixtures } from '../fixtures/topologies';

const marker = (seq: number, authorityEpoch = 1): PttMarker => ({
  authorityEpoch, pttObservationSeq: seq, pttLastObservedMonotonic: seq,
});
const target = { receiver: 'MAIN', slot: 'A', frequencyHz: 14_195_000 } as const;
const eligible: Eligibility = {
  catPtt: true, browserTxAudio: true, controlLive: true, permit: 'allowed', target,
};
const ptt = (value: boolean, seq: number): PttObservation => ({
  value, observed: true, fresh: true, source: 'radio-readback', marker: marker(seq),
});
const authority = (state: TxState, value: boolean, seq: number): TxState =>
  transition(state, { type: 'authority', epoch: 1, ptt: ptt(value, seq), eligibility: eligible, offCommandId: 'off' }).state;

const fresh = () => initialTxState(1, marker(1));
const started = (): TxState => transition(fresh(), {
  type: 'start', sourceId: 'semantic', leaseId: 'lease', intent: 'momentary', eligibility: eligible, ptt: ptt(false, 2),
}).state;
const dispatched = (): TxState => {
  const begun = started();
  return transition(begun, { type: 'audio-ready', guard: begun.guard!, commandId: 'on' }).state;
};
const confirmPending = (): TxState => {
  const on = dispatched();
  return transition(on, { type: 'on-sent', guard: on.guard!, commandId: 'on', barrier: marker(2) }).state;
};
const keyed = (): TxState => authority(confirmPending(), true, 3);
const releasing = (): TxState => {
  const live = keyed();
  return transition(live, { type: 'release', guard: live.guard!, commandId: 'off' }).state;
};
const notEligible = (): TxState => transition(fresh(), {
  type: 'start', sourceId: 'semantic', leaseId: 'lease', intent: 'momentary',
  eligibility: { ...eligible, permit: 'denied' }, ptt: ptt(false, 2),
}).state;
const externalTx = (): TxState => authority(fresh(), true, 5);
const observedRx = (): TxState => authority(fresh(), false, 5);

/**
 * Structural parity, checked by the compiler (`npm run check` type-checks
 * tests): if the reducer ever renames a field the surface reads, or widens a
 * union member the surface switches on, this assignment stops compiling.
 * Kill-mutation: rename `txRisk` to `risk` in `TxAuthoritySnapshot` — the
 * surface would silently read `undefined` and render RX forever.
 */
const asSnapshot = (state: TxState): TxAuthoritySnapshot => state;

const REAL_STATES: readonly (readonly [string, () => TxState])[] = [
  ['fresh (no PTT observed yet)', fresh],
  ['observed RX', observedRx],
  ['audio start pending', started],
  ['ON dispatched, unconfirmed', dispatched],
  ['key confirmation pending', confirmPending],
  ['keyed', keyed],
  ['releasing', releasing],
  ['not eligible', notEligible],
  ['external TX', externalTx],
];

describe('surface vocabulary against real reducer states', () => {
  it.each(REAL_STATES)('%s is a structurally complete authority snapshot', (_name, build) => {
    const snapshot = asSnapshot(build());
    for (const key of ['phase', 'intent', 'radioTx', 'txRisk', 'mayOwnKey', 'fault'] as const) {
      expect(snapshot).toHaveProperty(key);
    }
  });

  it.each([
    ['fresh (no PTT observed yet)', fresh, 'unknown', 'idle'],
    ['observed RX', observedRx, 'receiving', 'idle'],
    ['audio start pending', started, 'receiving', 'pending'],
    ['ON dispatched, unconfirmed', dispatched, 'uncertain', 'pending'],
    ['key confirmation pending', confirmPending, 'uncertain', 'pending'],
    ['keyed', keyed, 'transmitting', 'keyed'],
    ['releasing', releasing, 'transmitting', 'releasing'],
    // A rejected start never observed the radio, so its RF state stays unknown
    // rather than collapsing to RX — the fail-closed direction.
    ['not eligible', notEligible, 'unknown', 'failed'],
    ['external TX', externalTx, 'transmitting', 'idle'],
  ] as const)('%s reads as rf=%s session=%s', (_name, build, rf, session) => {
    const state = asSnapshot(build());
    expect(rfState(state)).toBe(rf);
    expect(txSessionState(state)).toBe(session);
  });

  it('never reads RX while the authority says the browser may own the key', () => {
    // The reducer guarantees mayOwnKey => txRisk in {uncertain, confirmed-on}.
    // This is the single most important safety property of the surface:
    // kill-mutation — reorder `rfState` so the radioTx check wins over txRisk,
    // and every unconfirmed key-down (radioTx still 'off') renders as RX.
    const owning = REAL_STATES.map(([, build]) => build()).filter((s) => s.mayOwnKey);
    expect(owning.length).toBeGreaterThan(0);
    for (const state of owning) {
      expect(state.txRisk === 'uncertain' || state.txRisk === 'confirmed-on').toBe(true);
      expect(rfState(asSnapshot(state))).not.toBe('receiving');
      expect(txOrigin(asSnapshot(state))).toBe('local');
    }
  });

  it('blocks keying in every non-idle real state, even on a fully permitted view model', () => {
    const permitted = topologyFixtures['1/single'];
    expect(keyBlockedReasons(permitted, asSnapshot(observedRx()))).toEqual([]);
    for (const [name, build] of REAL_STATES) {
      if (name === 'observed RX') continue;
      expect(keyBlockedReasons(permitted, asSnapshot(build())).length).toBeGreaterThan(0);
    }
  });

  it('classifies every fault the reducer can raise as a fault, none as idle', () => {
    // Enumerated against the reducer's own union: the `satisfies` clause fails
    // to compile if `TxFault` gains a member this list does not cover.
    const faults = [
      'not-eligible', 'audio-failed', 'audio-timeout', 'ptt-on-rejected',
      'on-command-failed', 'on-timeout', 'release-not-confirmed', 'backend-dekeyed',
    ] as const satisfies readonly Exclude<TxFault, null>[];
    type MissingFault = Exclude<Exclude<TxFault, null>, (typeof faults)[number]>;
    const everyFaultCovered: [MissingFault] extends [never] ? true : false = true;
    expect(everyFaultCovered).toBe(true);
    for (const fault of faults) {
      const state: TxAuthoritySnapshot = {
        phase: 'failed', intent: null, radioTx: 'off', txRisk: 'none', mayOwnKey: false, fault,
      };
      expect(txSessionState(state)).toBe('failed');
      expect(keyBlockedReasons(topologyFixtures['1/single'], state)).toContain('tx-fault');
    }
  });
});
