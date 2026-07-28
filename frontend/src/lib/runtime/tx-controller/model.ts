export type TxPhase = 'idle' | 'audio-start-pending' | 'key-confirm-pending' | 'active' | 'releasing' | 'failed';
export type TxIntent = 'momentary' | 'latched' | null;
export type TxTarget = { receiver: 'MAIN' | 'SUB'; slot: 'A' | 'B' | null; frequencyHz: number } | null;
export type PttMarker = { authorityEpoch: number; pttObservationSeq: number | null; pttLastObservedMonotonic: number | null };
export type PttObservation = { value: boolean; observed: boolean; fresh: boolean; source: 'radio-readback' | 'backend-observation' | 'other'; marker: PttMarker };
export type Eligibility = { catPtt: boolean; browserTxAudio: boolean; controlLive: boolean; permit: 'allowed' | 'denied' | 'unknown'; target: TxTarget };
export type TxGuard = { leaseId: string; generation: number; authorityEpoch: number };
export type PendingOff = { commandId: string; leaseId: string; generation: number; originalEpoch: number; deliveryEpoch: number | null; deliveryPttBarrier: PttMarker | null; deliveryRebound: boolean };
export type TxFault = 'not-eligible' | 'audio-failed' | 'ptt-on-rejected' | 'backend-dekeyed' | null;
export interface TxState {
  phase: TxPhase; intent: TxIntent; sourceId: string | null; leaseId: string | null; generation: number; guard: TxGuard | null;
  authorityEpoch: number; epochBaseline: PttMarker; pttMarker: PttMarker; leaseTarget: TxTarget;
  startPttBaseline: PttMarker | null; modBarrier: PttMarker | null; onCommandId: string | null; onDispatch: PttMarker | null; onConfirmed: PttMarker | null;
  localAudio: 'stopped' | 'starting' | 'streaming'; radioTx: 'off' | 'on' | 'unknown'; txRisk: 'none' | 'uncertain' | 'confirmed-on';
  mayOwnKey: boolean; modRestorePending: boolean; pendingOff: PendingOff | null; fault: TxFault; }
export type TxEffectType = 'start-audio' | 'dispatch-on' | 'dispatch-off' | 'stop-local-audio' | 'restore-mod' | 'arm-audio-timeout' | 'arm-on-timeout' | 'arm-off-timeout' | 'cancel-timers';
export type TxEffect = { type: TxEffectType; guard?: TxGuard; commandId?: string; barrier?: PttMarker };
export type TxEvent =
  | { type: 'start'; sourceId: string; leaseId: string; intent: Exclude<TxIntent, null>; eligibility: Eligibility; ptt: PttObservation }
  | { type: 'intent'; sourceId: string; guard: TxGuard; intent: Exclude<TxIntent, null> }
  | { type: 'audio-ready'; guard: TxGuard; commandId: string }
  | { type: 'fail'; guard: TxGuard; fault: 'audio-failed' | 'ptt-on-rejected'; offCommandId: string }
  | { type: 'on-sent'; guard: TxGuard; commandId: string; barrier: PttMarker }
  | { type: 'release'; sourceId?: string; guard: TxGuard; commandId: string }
  | { type: 'off-sent'; commandId: string; leaseId: string; generation: number; originalEpoch: number; eventEpoch: number; barrier: PttMarker }
  | { type: 'authority'; epoch: number; ptt: PttObservation; eligibility: Eligibility; offCommandId: string }
  | { type: 'reset-fault' }
  | { type: 'epoch'; epoch: number; baseline: PttMarker; offCommandId: string };
export type TxTransition = { state: TxState; effects: TxEffect[] };
export function initialTxState(authorityEpoch: number, baseline: PttMarker): TxState {
  return { phase: 'idle', intent: null, sourceId: null, leaseId: null, generation: 0, guard: null, authorityEpoch, epochBaseline: baseline, pttMarker: baseline, leaseTarget: null, startPttBaseline: null, modBarrier: null, onCommandId: null, onDispatch: null, onConfirmed: null, localAudio: 'stopped', radioTx: 'unknown', txRisk: 'none', mayOwnKey: false, modRestorePending: false, pendingOff: null, fault: null };
}
const sameGuard = (state: TxState, guard: TxGuard) => state.guard?.leaseId === guard.leaseId && state.guard.generation === guard.generation && state.guard.authorityEpoch === guard.authorityEpoch;
const sameTarget = (a: TxTarget, b: TxTarget) => a !== null && b !== null && a.receiver === b.receiver && a.slot === b.slot && a.frequencyHz === b.frequencyHz;
const newer = (barrier: PttMarker, observation: PttObservation) => {
  if (observation.marker.authorityEpoch !== barrier.authorityEpoch) return false;
  const { pttObservationSeq: seq, pttLastObservedMonotonic: at } = observation.marker;
  if (barrier.pttObservationSeq !== null || seq !== null) return barrier.pttObservationSeq !== null && seq !== null && seq > barrier.pttObservationSeq;
  return barrier.pttLastObservedMonotonic !== null && at !== null && at > barrier.pttLastObservedMonotonic;
};
const authoritative = (observation: PttObservation) => observation.observed && observation.fresh && (observation.source === 'radio-readback' || observation.source === 'backend-observation');
const ready = (eligibility: Eligibility) => eligibility.catPtt && eligibility.browserTxAudio && eligibility.controlLive && eligibility.permit === 'allowed' && eligibility.target !== null;
const effect = (type: TxEffectType, state: TxState, commandId?: string, barrier?: PttMarker): TxEffect => ({ type, ...(state.guard ? { guard: state.guard } : {}), ...(commandId ? { commandId } : {}), ...(barrier ? { barrier } : {}) });
const clearLease = (state: TxState): TxState => ({ ...state, intent: null, sourceId: null, leaseId: null, guard: null, leaseTarget: null, startPttBaseline: null, modBarrier: null, onCommandId: null, onDispatch: null, onConfirmed: null, localAudio: 'stopped', mayOwnKey: false, pendingOff: null });

function release(state: TxState, commandId: string): TxTransition {
  if (!state.guard || state.phase === 'releasing' || state.pendingOff) return { state, effects: [] };
  const generation = state.generation + 1;
  const guard = { ...state.guard, generation };
  const next = { ...state, phase: 'releasing' as const, intent: null, generation, guard, localAudio: 'stopped' as const };
  const effects = [effect('cancel-timers', next)];
  if (state.mayOwnKey) {
    next.pendingOff = { commandId, leaseId: guard.leaseId, generation, originalEpoch: state.authorityEpoch, deliveryEpoch: null, deliveryPttBarrier: null, deliveryRebound: false };
    next.txRisk = state.radioTx === 'on' ? 'confirmed-on' : 'uncertain';
    effects.push(effect('dispatch-off', next, commandId), effect('arm-off-timeout', next, commandId));
  }
  effects.push(effect('stop-local-audio', next));
  return { state: next, effects };
}

export function transition(state: TxState, event: TxEvent): TxTransition {
  if (event.type === 'start') {
    if (state.phase !== 'idle') return { state, effects: [] };
    const ok = ready(event.eligibility) && event.ptt.value === false && authoritative(event.ptt) && newer(state.pttMarker, event.ptt) && event.ptt.marker.authorityEpoch === state.authorityEpoch;
    if (!ok) return { state: { ...state, phase: 'failed', fault: 'not-eligible', txRisk: 'none', sourceId: null, leaseId: null, guard: null }, effects: [] };
    const generation = state.generation + 1;
    const guard = { leaseId: event.leaseId, generation, authorityEpoch: state.authorityEpoch };
    const next = { ...clearLease(state), phase: 'audio-start-pending' as const, intent: event.intent, sourceId: event.sourceId, leaseId: event.leaseId, generation, guard, leaseTarget: event.eligibility.target, startPttBaseline: event.ptt.marker, modBarrier: event.ptt.marker, pttMarker: event.ptt.marker, localAudio: 'starting' as const, radioTx: 'off' as const, txRisk: 'none' as const, modRestorePending: true, fault: null };
    return { state: next, effects: [effect('start-audio', next), effect('arm-audio-timeout', next)] };
  }
  if (event.type === 'intent') return sameGuard(state, event.guard) && event.sourceId === state.sourceId && state.phase !== 'releasing' && state.phase !== 'failed' ? { state: { ...state, intent: event.intent }, effects: [] } : { state, effects: [] };
  if (event.type === 'release') return sameGuard(state, event.guard) && (event.sourceId === undefined || event.sourceId === state.sourceId) ? release(state, event.commandId) : { state, effects: [] };
  if (event.type === 'reset-fault' && state.phase === 'failed' && !state.pendingOff && !state.modRestorePending && !state.mayOwnKey) return { state: { ...clearLease(state), phase: 'idle', txRisk: 'none', fault: null }, effects: [] };
  if (event.type === 'audio-ready' && sameGuard(state, event.guard) && state.phase === 'audio-start-pending' && state.onCommandId === null) {
    const next = { ...state, localAudio: 'streaming' as const, onCommandId: event.commandId, onDispatch: state.pttMarker, mayOwnKey: true, txRisk: 'uncertain' as const };
    return { state: next, effects: [effect('cancel-timers', next), effect('dispatch-on', next, event.commandId), effect('arm-on-timeout', next, event.commandId)] };
  }
  if (event.type === 'fail' && sameGuard(state, event.guard)) {
    if (state.mayOwnKey) return release({ ...state, fault: event.fault }, event.offCommandId);
    const next = { ...state, phase: 'failed' as const, intent: null, sourceId: null, leaseId: null, generation: state.generation + 1, guard: null, localAudio: 'stopped' as const, fault: event.fault };
    return { state: next, effects: [effect('cancel-timers', state), effect('stop-local-audio', state)] };
  }
  if (event.type === 'on-sent' && sameGuard(state, event.guard) && state.phase === 'audio-start-pending' && state.onCommandId === event.commandId && event.barrier.authorityEpoch === state.authorityEpoch) {
    const next = { ...state, phase: state.onConfirmed ? 'active' as const : 'key-confirm-pending' as const, onDispatch: event.barrier };
    return { state: next, effects: [effect('arm-on-timeout', next, event.commandId)] };
  }
  if (event.type === 'off-sent') {
    const off = state.pendingOff;
    if (!off || off.deliveryEpoch !== null || off.commandId !== event.commandId || off.leaseId !== event.leaseId || off.generation !== event.generation || off.originalEpoch !== event.originalEpoch || event.eventEpoch !== state.authorityEpoch || event.barrier.authorityEpoch !== event.eventEpoch) return { state, effects: [] };
    const next = { ...state, pendingOff: { ...off, deliveryEpoch: event.eventEpoch, deliveryPttBarrier: event.barrier, deliveryRebound: event.eventEpoch !== off.originalEpoch } };
    return { state: next, effects: [effect('arm-off-timeout', next, event.commandId)] };
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
      next = { ...clearLease(next), phase: 'failed', generation: state.generation + 1, txRisk: 'none', modRestorePending: false, fault: 'backend-dekeyed' };
      return { state: next, effects: [effect('cancel-timers', state), effect('stop-local-audio', state), effect('restore-mod', state, undefined, state.onConfirmed)] };
    }
    if ((state.phase === 'releasing' || state.phase === 'failed') && !event.ptt.value && state.modRestorePending) {
      const barrier = state.pendingOff ? state.pendingOff.deliveryPttBarrier : state.modBarrier;
      if (barrier && newer(barrier, event.ptt)) return { state: state.phase === 'releasing' ? { ...clearLease(next), phase: 'idle', txRisk: 'none', modRestorePending: false, fault: null } : { ...next, modRestorePending: false }, effects: [effect('cancel-timers', state), effect('restore-mod', state, undefined, barrier)] };
    }
    if (state.guard && (!ready(event.eligibility) || !sameTarget(state.leaseTarget, event.eligibility.target))) return release(next, event.offCommandId);
    return { state: next, effects: [] };
  }
  if (event.type === 'authority' && event.epoch === state.authorityEpoch && state.guard && (!ready(event.eligibility) || !sameTarget(state.leaseTarget, event.eligibility.target))) return release(state, event.offCommandId);
  return { state, effects: [] };
}
