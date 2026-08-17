export type TxPhase = 'idle' | 'audio-start-pending' | 'key-confirm-pending' | 'active' | 'releasing' | 'failed';
export type TxIntent = 'momentary' | 'latched' | null;
export type TxTarget = { receiver: 'MAIN' | 'SUB'; slot: 'A' | 'B' | null; frequencyHz: number } | null;
export type PttMarker = { authorityEpoch: number; pttObservationSeq: number | null; pttLastObservedMonotonic: number | null };
export type PttObservation = { value: boolean; observed: boolean; fresh: boolean; source: 'radio-readback' | 'backend-observation' | 'other'; marker: PttMarker };
export type Eligibility = { catPtt: boolean; browserTxAudio: boolean; controlLive: boolean; permit: 'allowed' | 'denied' | 'unknown'; target: TxTarget };
export type TxGuard = { leaseId: string; generation: number; authorityEpoch: number };
export type PendingOff = { commandId: string; leaseId: string; generation: number; originalEpoch: number; deliveryEpoch: number | null; deliveryPttBarrier: PttMarker | null; deliveryRebound: boolean };
export type TxFault = 'not-eligible' | 'audio-failed' | 'audio-timeout' | 'ptt-on-rejected' | 'on-command-failed' | 'on-timeout' | 'release-not-confirmed' | 'backend-dekeyed' | null;
/**
 * MOR-1792 — the individual conjuncts of the `start` eligibility predicate,
 * reported alongside `fault: 'not-eligible'` so a refusal names WHICH leg
 * failed. Observability only: every code below is a read of the same term the
 * predicate already evaluates, and `ineligibility()` returning empty is
 * pinned to `ok` being true by an exhaustive test matrix. Nothing here gates
 * anything — the predicate on the `start` branch is untouched.
 */
export type TxIneligibility =
  | 'cat-ptt-unavailable' | 'browser-tx-audio-unavailable' | 'control-not-live'
  | 'tx-permit-not-allowed' | 'tx-target-unknown' | 'ptt-not-off'
  | 'ptt-not-authoritative' | 'no-confirmed-ptt-off' | 'authority-epoch-mismatch';
export interface TxState {
  phase: TxPhase; intent: TxIntent; sourceId: string | null; leaseId: string | null; generation: number; guard: TxGuard | null;
  cleanupGuard: TxGuard | null; timerRevision: { audio: number; on: number; off: number };
  authorityEpoch: number; epochBaseline: PttMarker; pttMarker: PttMarker; leaseTarget: TxTarget;
  startPttBaseline: PttMarker | null; modBarrier: PttMarker | null; onCommandId: string | null; onDispatch: PttMarker | null; onConfirmed: PttMarker | null;
  localAudio: 'stopped' | 'starting' | 'streaming'; radioTx: 'off' | 'on' | 'unknown'; txRisk: 'none' | 'uncertain' | 'confirmed-on';
  mayOwnKey: boolean; modRestorePending: boolean; pendingOff: PendingOff | null; fault: TxFault; faultDetail: readonly TxIneligibility[] | null; }
export type TxEffectType = 'start-audio' | 'dispatch-on' | 'dispatch-off' | 'stop-local-audio' | 'restore-mod' | 'arm-audio-timeout' | 'arm-on-timeout' | 'arm-off-timeout' | 'cancel-timers';
export type TxEffect = { type: TxEffectType; guard?: TxGuard; commandId?: string; barrier?: PttMarker; armRevision?: number };
export type TxCorrelation = { leaseId: string; generation: number; originalEpoch: number; eventEpoch: number; offCommandId: string };
type TxCommandIdentity = TxCorrelation & { commandId: string | null };
export type TxEvent =
  | { type: 'start'; sourceId: string; leaseId: string; intent: Exclude<TxIntent, null>; eligibility: Eligibility; ptt: PttObservation }
  | { type: 'intent'; sourceId: string; guard: TxGuard; intent: Exclude<TxIntent, null> }
  | { type: 'audio-ready'; guard: TxGuard; commandId: string }
  | { type: 'fail'; guard: TxGuard; fault: 'audio-failed' | 'ptt-on-rejected'; offCommandId: string }
  | { type: 'on-sent'; guard: TxGuard; commandId: string; barrier: PttMarker }
  | { type: 'release'; sourceId?: string; guard: TxGuard; commandId: string }
  | { type: 'off-sent'; commandId: string; leaseId: string; generation: number; originalEpoch: number; eventEpoch: number; barrier: PttMarker }
  | { type: 'authority'; epoch: number; ptt: PttObservation; eligibility: Eligibility; offCommandId: string }
  | ({ type: 'timer-fired'; timer: 'audio-start' | 'on-confirmation' | 'off-confirmation'; commandId: string | null; armRevision: number } & TxCorrelation)
  | ({ type: 'command-result'; command: 'on' | 'off'; outcome: 'sent' | 'ack' | 'response-ok' | 'response-error' | 'transport-error'; commandId: string; barrier: PttMarker | null } & TxCorrelation)
  | { type: 'reset-fault' }
  | { type: 'audio-died'; guard: TxGuard; offCommandId: string }
  | { type: 'epoch'; epoch: number; baseline: PttMarker; offCommandId: string };
export type TxTransition = { state: TxState; effects: TxEffect[] };
export function initialTxState(authorityEpoch: number, baseline: PttMarker): TxState {
  return { phase: 'idle', intent: null, sourceId: null, leaseId: null, generation: 0, guard: null, cleanupGuard: null, timerRevision: { audio: 0, on: 0, off: 0 }, authorityEpoch, epochBaseline: baseline, pttMarker: baseline, leaseTarget: null, startPttBaseline: null, modBarrier: null, onCommandId: null, onDispatch: null, onConfirmed: null, localAudio: 'stopped', radioTx: 'unknown', txRisk: 'none', mayOwnKey: false, modRestorePending: false, pendingOff: null, fault: null, faultDetail: null };
}
/**
 * MOR-1784 — the obligation that stands between a latched fault and the
 * operator dismissing it, or `null` when nothing does.
 *
 * This IS the `reset-fault` guard below, lifted out and named so an operator
 * surface can offer the reset exactly when the reducer would accept it, and say
 * which obligation is outstanding when it would not. A surface re-deriving this
 * list would eventually disagree with the reducer and either dead-end the
 * operator or promise a reset that silently no-ops; there is one list, and the
 * transition consults the same function the UI does.
 *
 * Reading it changes nothing and keys nothing: dismissing still goes through
 * `transition`, which re-checks this guard at the moment of the event. The
 * order is by consequence — an unconfirmed de-key first — and each condition
 * keeps its own name rather than collapsing into the one before it, so a state
 * only reachable in future never degrades to a generic refusal.
 */
export type TxFaultObligation = 'dekey-pending' | 'key-held' | 'mod-restore' | 'cleanup';
export type TxFaultObligationInputs = Readonly<{
  pendingOff: object | null; mayOwnKey: boolean; modRestorePending: boolean; cleanupGuard: object | null;
}>;
export function txFaultObligation(state: TxFaultObligationInputs): TxFaultObligation | null {
  if (state.pendingOff) return 'dekey-pending';
  if (state.mayOwnKey) return 'key-held';
  if (state.modRestorePending) return 'mod-restore';
  if (state.cleanupGuard) return 'cleanup';
  return null;
}
const sameGuard = (state: TxState, guard: TxGuard) => state.guard?.leaseId === guard.leaseId && state.guard.generation === guard.generation && state.guard.authorityEpoch === guard.authorityEpoch;
const sameTarget = (a: TxTarget, b: TxTarget) => a !== null && b !== null && a.receiver === b.receiver && a.slot === b.slot && a.frequencyHz === b.frequencyHz;
const newer = (barrier: PttMarker, observation: PttObservation) => {
  if (observation.marker.authorityEpoch !== barrier.authorityEpoch) return false;
  const { pttObservationSeq: seq, pttLastObservedMonotonic: at } = observation.marker;
  if (barrier.pttObservationSeq !== null || seq !== null) return barrier.pttObservationSeq !== null && seq !== null && seq > barrier.pttObservationSeq;
  return barrier.pttLastObservedMonotonic !== null && at !== null && at > barrier.pttLastObservedMonotonic;
};
const sameMarker = (marker: PttMarker, observation: PttObservation) =>
  marker.authorityEpoch === observation.marker.authorityEpoch
  && marker.pttObservationSeq === observation.marker.pttObservationSeq
  && marker.pttLastObservedMonotonic === observation.marker.pttLastObservedMonotonic;
const authoritative = (observation: PttObservation) => observation.observed && observation.fresh && (observation.source === 'radio-readback' || observation.source === 'backend-observation');
const ready = (eligibility: Eligibility) => eligibility.catPtt && eligibility.browserTxAudio && eligibility.controlLive && eligibility.permit === 'allowed' && eligibility.target !== null;
/**
 * MOR-1792 — every failing conjunct of the `start` predicate, in predicate
 * order and WITHOUT short-circuiting, so one refusal reports every leg rather
 * than only the first. Reporting-only: this reads the same terms the `ok`
 * expression below evaluates and never feeds back into it. The empty-result
 * ⇔ `ok` equivalence is the invariant a reviewer should check, and it is
 * pinned exhaustively in `__tests__/not-eligible-legs.test.ts`.
 */
function ineligibility(state: TxState, eligibility: Eligibility, ptt: PttObservation, currentConfirmedOff: boolean): TxIneligibility[] {
  const legs: TxIneligibility[] = [];
  if (!eligibility.catPtt) legs.push('cat-ptt-unavailable');
  if (!eligibility.browserTxAudio) legs.push('browser-tx-audio-unavailable');
  if (!eligibility.controlLive) legs.push('control-not-live');
  if (eligibility.permit !== 'allowed') legs.push('tx-permit-not-allowed');
  if (eligibility.target === null) legs.push('tx-target-unknown');
  if (ptt.value !== false) legs.push('ptt-not-off');
  if (!authoritative(ptt)) legs.push('ptt-not-authoritative');
  if (!newer(state.pttMarker, ptt) && !currentConfirmedOff) legs.push('no-confirmed-ptt-off');
  if (ptt.marker.authorityEpoch !== state.authorityEpoch) legs.push('authority-epoch-mismatch');
  return legs;
}
const effect = (type: TxEffectType, state: TxState, commandId?: string, barrier?: PttMarker, guard: TxGuard | null = state.guard): TxEffect => ({ type, ...(guard ? { guard } : {}), ...(commandId !== undefined ? { commandId } : {}), ...(barrier ? { barrier } : {}) });
const armEffect = (type: 'arm-audio-timeout' | 'arm-on-timeout' | 'arm-off-timeout', state: TxState, armRevision: number, commandId?: string, barrier?: PttMarker): TxEffect => ({ ...effect(type, state, commandId, barrier), armRevision });
const clearLease = (state: TxState): TxState => ({ ...state, intent: null, sourceId: null, leaseId: null, guard: null, cleanupGuard: null, timerRevision: { audio: 0, on: 0, off: 0 }, leaseTarget: null, startPttBaseline: null, modBarrier: null, onCommandId: null, onDispatch: null, onConfirmed: null, localAudio: 'stopped', mayOwnKey: false, pendingOff: null });
const matchesCurrent = (state: TxState, event: TxCorrelation) => state.guard?.leaseId === event.leaseId && state.guard.generation === event.generation && state.guard.authorityEpoch === event.originalEpoch && state.authorityEpoch === event.eventEpoch;
const matchesOff = (state: TxState, event: TxCommandIdentity) => state.pendingOff?.commandId === event.commandId && event.offCommandId === event.commandId && state.pendingOff.leaseId === event.leaseId && state.pendingOff.generation === event.generation && state.pendingOff.originalEpoch === event.originalEpoch && state.authorityEpoch === event.eventEpoch;

function failLocal(state: TxState, fault: 'audio-failed' | 'audio-timeout' | 'ptt-on-rejected'): TxTransition {
  const cleanupGuard = state.cleanupGuard ?? state.guard;
  const next = { ...state, phase: 'failed' as const, intent: null, sourceId: null, leaseId: null, generation: state.generation + 1, guard: null, timerRevision: { audio: state.timerRevision.audio + 1, on: state.timerRevision.on + 1, off: state.timerRevision.off + 1 }, localAudio: 'stopped' as const, fault };
  return { state: next, effects: [effect('cancel-timers', state, undefined, undefined, cleanupGuard), effect('stop-local-audio', state, undefined, undefined, cleanupGuard)] };
}
function failRelease(state: TxState): TxTransition {
  const next = { ...state, phase: 'failed' as const, timerRevision: { ...state.timerRevision, off: state.timerRevision.off + 1 }, fault: 'release-not-confirmed' as const };
  return { state: next, effects: [effect('cancel-timers', state)] };
}
function bindOn(state: TxState, commandId: string, barrier: PttMarker): TxTransition {
  const next = { ...state, phase: state.onConfirmed ? 'active' as const : 'key-confirm-pending' as const, onDispatch: barrier, timerRevision: { ...state.timerRevision, on: state.timerRevision.on + 1 } };
  return { state: next, effects: [armEffect('arm-on-timeout', next, next.timerRevision.on, commandId)] };
}
function bindOff(state: TxState, event: TxCommandIdentity, barrier: PttMarker): TxTransition {
  const off = state.pendingOff!;
  const next = { ...state, pendingOff: { ...off, deliveryEpoch: event.eventEpoch, deliveryPttBarrier: barrier, deliveryRebound: event.eventEpoch !== off.originalEpoch }, timerRevision: { ...state.timerRevision, off: state.timerRevision.off + 1 } };
  return { state: next, effects: [armEffect('arm-off-timeout', next, next.timerRevision.off, off.commandId)] };
}

function release(state: TxState, commandId: string): TxTransition {
  if (!state.guard || state.phase === 'releasing' || state.pendingOff) return { state, effects: [] };
  const generation = state.generation + 1;
  const guard = { ...state.guard, generation };
  const cleanupGuard = state.cleanupGuard ?? state.guard;
  const next: TxState = { ...state, phase: 'releasing', intent: null, generation, guard, cleanupGuard, timerRevision: { audio: state.timerRevision.audio + 1, on: state.timerRevision.on + 1, off: state.timerRevision.off + (state.mayOwnKey ? 1 : 0) }, localAudio: 'stopped' };
  const effects = [effect('cancel-timers', next, undefined, undefined, cleanupGuard)];
  if (state.mayOwnKey) {
    next.pendingOff = { commandId, leaseId: guard.leaseId, generation, originalEpoch: state.authorityEpoch, deliveryEpoch: null, deliveryPttBarrier: null, deliveryRebound: false };
    next.txRisk = state.radioTx === 'on' ? 'confirmed-on' : 'uncertain';
    effects.push(effect('dispatch-off', next, commandId), armEffect('arm-off-timeout', next, next.timerRevision.off, commandId));
  }
  effects.push(effect('stop-local-audio', next, undefined, undefined, cleanupGuard));
  return { state: next, effects };
}

export function transition(state: TxState, event: TxEvent): TxTransition {
  if (('offCommandId' in event && typeof event.offCommandId !== 'string') || ((event.type === 'audio-ready' || event.type === 'release' || event.type === 'on-sent') && typeof event.commandId !== 'string')) return { state, effects: [] };
  if (event.type === 'start') {
    if (state.phase !== 'idle') return { state, effects: [] };
    const currentConfirmedOff = state.radioTx === 'off' && sameMarker(state.pttMarker, event.ptt);
    const ok = ready(event.eligibility) && event.ptt.value === false && authoritative(event.ptt) && (newer(state.pttMarker, event.ptt) || currentConfirmedOff) && event.ptt.marker.authorityEpoch === state.authorityEpoch;
    if (!ok) return { state: { ...state, phase: 'failed', fault: 'not-eligible', faultDetail: ineligibility(state, event.eligibility, event.ptt, currentConfirmedOff), txRisk: 'none', sourceId: null, leaseId: null, guard: null }, effects: [] };
    const generation = state.generation + 1;
    const guard = { leaseId: event.leaseId, generation, authorityEpoch: state.authorityEpoch };
    const next = { ...clearLease(state), phase: 'audio-start-pending' as const, intent: event.intent, sourceId: event.sourceId, leaseId: event.leaseId, generation, guard, cleanupGuard: guard, timerRevision: { audio: 1, on: 0, off: 0 }, leaseTarget: event.eligibility.target, startPttBaseline: event.ptt.marker, modBarrier: event.ptt.marker, pttMarker: event.ptt.marker, localAudio: 'starting' as const, radioTx: 'off' as const, txRisk: 'none' as const, modRestorePending: true, fault: null, faultDetail: null };
    return { state: next, effects: [effect('start-audio', next), armEffect('arm-audio-timeout', next, next.timerRevision.audio)] };
  }
  if (event.type === 'intent') return sameGuard(state, event.guard) && event.sourceId === state.sourceId && state.phase !== 'releasing' && state.phase !== 'failed' ? { state: { ...state, intent: event.intent }, effects: [] } : { state, effects: [] };
  if (event.type === 'release') return sameGuard(state, event.guard) && (event.sourceId === undefined || event.sourceId === state.sourceId) ? release(state, event.commandId) : { state, effects: [] };
  if (event.type === 'reset-fault' && state.phase === 'failed' && txFaultObligation(state) === null) return { state: { ...clearLease(state), phase: 'idle', txRisk: 'none', fault: null, faultDetail: null }, effects: [] };
  if (event.type === 'audio-ready' && sameGuard(state, event.guard) && state.phase === 'audio-start-pending' && state.onCommandId === null) {
    const next = { ...state, localAudio: 'streaming' as const, onCommandId: event.commandId, onDispatch: state.pttMarker, mayOwnKey: true, txRisk: 'uncertain' as const, timerRevision: { ...state.timerRevision, audio: state.timerRevision.audio + 1, on: state.timerRevision.on + 1 } };
    return { state: next, effects: [effect('cancel-timers', next), effect('dispatch-on', next, event.commandId), armEffect('arm-on-timeout', next, next.timerRevision.on, event.commandId)] };
  }
  if (event.type === 'timer-fired') {
    if (event.timer === 'audio-start' && event.armRevision === state.timerRevision.audio && event.commandId === null && matchesCurrent(state, event) && state.phase === 'audio-start-pending' && state.onCommandId === null) return failLocal(state, 'audio-timeout');
    if (event.timer === 'on-confirmation' && event.armRevision === state.timerRevision.on && matchesCurrent(state, event) && event.commandId === state.onCommandId && state.mayOwnKey && (state.phase === 'audio-start-pending' || state.phase === 'key-confirm-pending')) return release({ ...state, fault: 'on-timeout' }, event.offCommandId);
    if (event.timer === 'off-confirmation' && event.armRevision === state.timerRevision.off && matchesOff(state, event) && state.phase === 'releasing') return failRelease(state);
    return { state, effects: [] };
  }
  if (event.type === 'command-result') {
    if (event.command === 'on' && state.onCommandId !== null && state.mayOwnKey && matchesCurrent(state, event) && event.commandId === state.onCommandId) {
      if (event.outcome === 'sent' && event.barrier?.authorityEpoch === event.eventEpoch && state.phase === 'audio-start-pending') return bindOn(state, event.commandId, event.barrier);
      if ((event.outcome === 'response-error' || event.outcome === 'transport-error') && state.mayOwnKey && state.phase !== 'releasing' && state.phase !== 'failed') return release({ ...state, fault: 'on-command-failed' }, event.offCommandId);
    }
    if (event.command === 'off' && matchesOff(state, event)) {
      if (event.outcome === 'sent' && state.pendingOff?.deliveryEpoch === null && event.barrier?.authorityEpoch === event.eventEpoch) return bindOff(state, event, event.barrier);
      if ((event.outcome === 'response-error' || event.outcome === 'transport-error') && state.phase === 'releasing') return failRelease(state);
    }
    return { state, effects: [] };
  }
  if (event.type === 'fail' && sameGuard(state, event.guard)) {
    if (state.mayOwnKey) return release({ ...state, fault: event.fault }, event.offCommandId);
    return failLocal(state, event.fault);
  }
  if (event.type === 'audio-died' && sameGuard(state, event.guard)
    && (state.phase === 'audio-start-pending' || state.phase === 'key-confirm-pending' || state.phase === 'active')) {
    // MOR-1796: mid-lease local capture death. With the key owed, de-key
    // through the normal release path; pre-key it is the same local failure an
    // arm error is. Releasing/failed are excluded so a late echo can neither
    // overwrite the standing fault nor disturb an obligation in flight, and
    // sameGuard means a foreign lease can never be de-keyed from here.
    if (state.mayOwnKey) return release({ ...state, fault: 'audio-failed' }, event.offCommandId);
    return failLocal(state, 'audio-failed');
  }
  if (event.type === 'on-sent' && state.onCommandId !== null && state.mayOwnKey && sameGuard(state, event.guard) && state.phase === 'audio-start-pending' && state.onCommandId === event.commandId && event.barrier.authorityEpoch === state.authorityEpoch) {
    return bindOn(state, event.commandId, event.barrier);
  }
  if (event.type === 'off-sent') {
    const off = state.pendingOff;
    if (!off || off.deliveryEpoch !== null || off.commandId !== event.commandId || off.leaseId !== event.leaseId || off.generation !== event.generation || off.originalEpoch !== event.originalEpoch || event.eventEpoch !== state.authorityEpoch || event.barrier.authorityEpoch !== event.eventEpoch) return { state, effects: [] };
    return bindOff(state, { ...event, offCommandId: event.commandId }, event.barrier);
  }
  if (event.type === 'epoch' && event.epoch > state.authorityEpoch && event.baseline.authorityEpoch === event.epoch) {
    const released = state.guard ? release(state, event.offCommandId) : { state, effects: [] };
    return { state: { ...released.state, authorityEpoch: event.epoch, epochBaseline: event.baseline, pttMarker: event.baseline, modBarrier: released.state.modRestorePending && !released.state.pendingOff ? event.baseline : released.state.modBarrier, radioTx: 'unknown' }, effects: released.effects };
  }
  if (event.type === 'authority' && event.epoch === state.authorityEpoch && authoritative(event.ptt) && newer(state.pttMarker, event.ptt)) {
    let next: TxState = { ...state, pttMarker: event.ptt.marker, radioTx: event.ptt.value ? 'on' : 'off' };
    if (state.phase === 'audio-start-pending' && !state.mayOwnKey && event.ptt.value) return release(next, event.offCommandId);
    if (event.ptt.value && state.mayOwnKey) next = { ...next, ...(state.phase === 'key-confirm-pending' ? { phase: 'active' as const } : {}), txRisk: 'confirmed-on', onConfirmed: event.ptt.marker };
    if (state.phase !== 'releasing' && state.phase !== 'failed' && state.mayOwnKey && !event.ptt.value && state.onConfirmed && newer(state.onConfirmed, event.ptt)) {
      const cleanupGuard = state.cleanupGuard ?? state.guard;
      next = { ...clearLease(next), phase: 'failed', generation: state.generation + 1, txRisk: 'none', modRestorePending: false, fault: 'backend-dekeyed' };
      return { state: next, effects: [effect('cancel-timers', state, undefined, undefined, cleanupGuard), effect('stop-local-audio', state, undefined, undefined, cleanupGuard), effect('restore-mod', state, undefined, state.onConfirmed, cleanupGuard)] };
    }
    if ((state.phase === 'releasing' || state.phase === 'failed') && !event.ptt.value && state.modRestorePending) {
      const bound = state.pendingOff ? state.pendingOff.deliveryPttBarrier : state.modBarrier;
      // A barrier stamped at a superseded authority epoch can never be crossed by an
      // observation on the live epoch, so an obligation a reconnect caught mid-flight
      // would be stranded forever (MOR-1205). Fall back to the current epoch's own
      // baseline: the authoritative reading past it is the source of truth. Stale
      // commands and timers stay dead, and discharging issues no further command.
      const barrier = bound && bound.authorityEpoch < state.authorityEpoch ? state.epochBaseline : bound;
      if (barrier && newer(barrier, event.ptt)) {
        const cleanupGuard = state.cleanupGuard ?? state.guard;
        const discharged = state.phase === 'releasing' ? { ...clearLease(next), phase: 'idle' as const, txRisk: 'none' as const, modRestorePending: false, fault: null, faultDetail: null } : state.fault === 'release-not-confirmed' ? { ...clearLease(next), phase: 'failed' as const, txRisk: 'none' as const, modRestorePending: false, fault: state.fault } : { ...next, cleanupGuard: null, modRestorePending: false };
        return { state: discharged, effects: [effect('cancel-timers', state, undefined, undefined, cleanupGuard), effect('restore-mod', state, undefined, barrier, cleanupGuard)] };
      }
    }
    if (state.guard && (!ready(event.eligibility) || !sameTarget(state.leaseTarget, event.eligibility.target))) return release(next, event.offCommandId);
    return { state: next, effects: [] };
  }
  if (event.type === 'authority' && event.epoch === state.authorityEpoch && state.guard && (!ready(event.eligibility) || !sameTarget(state.leaseTarget, event.eligibility.target))) return release(state, event.offCommandId);
  return { state, effects: [] };
}
