export type TxPhase = 'idle' | 'audio-start-pending' | 'key-confirm-pending' | 'active' | 'releasing' | 'failed';
export type TxIntent = 'momentary' | 'latched' | null;
export type TxTarget = { receiver: 'MAIN' | 'SUB'; slot: 'A' | 'B' | null; frequencyHz: number } | null;
export type PttMarker = { authorityEpoch: number; pttObservationSeq: number | null; pttLastObservedMonotonic: number | null };
export type PttObservation = { value: boolean; observed: boolean; fresh: boolean; source: 'radio-readback' | 'backend-observation' | 'other'; marker: PttMarker };
export type Eligibility = { catPtt: boolean; browserTxAudio: boolean; controlLive: boolean; permit: 'allowed' | 'denied' | 'unknown'; target: TxTarget };
export type TxGuard = { leaseId: string; generation: number; authorityEpoch: number };
export type PendingOff = { commandId: string; leaseId: string; generation: number; originalEpoch: number; deliveryEpoch: number | null; deliveryPttBarrier: PttMarker | null; deliveryRebound: boolean };
export type TxFault = 'not-eligible' | 'audio-failed' | 'audio-timeout' | 'ptt-on-rejected' | 'on-command-failed' | 'on-timeout' | 'release-not-confirmed' | 'backend-dekeyed' | null;
export interface TxState {
  phase: TxPhase; intent: TxIntent; sourceId: string | null; leaseId: string | null; generation: number; guard: TxGuard | null;
  cleanupGuard: TxGuard | null; timerRevision: { audio: number; on: number; off: number };
  authorityEpoch: number; epochBaseline: PttMarker; pttMarker: PttMarker; leaseTarget: TxTarget;
  startPttBaseline: PttMarker | null; modBarrier: PttMarker | null; onCommandId: string | null; onDispatch: PttMarker | null; onConfirmed: PttMarker | null;
  localAudio: 'stopped' | 'starting' | 'streaming'; radioTx: 'off' | 'on' | 'unknown'; txRisk: 'none' | 'uncertain' | 'confirmed-on';
  mayOwnKey: boolean; modRestorePending: boolean; pendingOff: PendingOff | null; fault: TxFault; }
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
  | { type: 'epoch'; epoch: number; baseline: PttMarker; offCommandId: string };
export type TxTransition = { state: TxState; effects: TxEffect[] };
export function initialTxState(authorityEpoch: number, baseline: PttMarker): TxState {
  return { phase: 'idle', intent: null, sourceId: null, leaseId: null, generation: 0, guard: null, cleanupGuard: null, timerRevision: { audio: 0, on: 0, off: 0 }, authorityEpoch, epochBaseline: baseline, pttMarker: baseline, leaseTarget: null, startPttBaseline: null, modBarrier: null, onCommandId: null, onDispatch: null, onConfirmed: null, localAudio: 'stopped', radioTx: 'unknown', txRisk: 'none', mayOwnKey: false, modRestorePending: false, pendingOff: null, fault: null };
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
    const ok = ready(event.eligibility) && event.ptt.value === false && authoritative(event.ptt) && newer(state.pttMarker, event.ptt) && event.ptt.marker.authorityEpoch === state.authorityEpoch;
    if (!ok) return { state: { ...state, phase: 'failed', fault: 'not-eligible', txRisk: 'none', sourceId: null, leaseId: null, guard: null }, effects: [] };
    const generation = state.generation + 1;
    const guard = { leaseId: event.leaseId, generation, authorityEpoch: state.authorityEpoch };
    const next = { ...clearLease(state), phase: 'audio-start-pending' as const, intent: event.intent, sourceId: event.sourceId, leaseId: event.leaseId, generation, guard, cleanupGuard: guard, timerRevision: { audio: 1, on: 0, off: 0 }, leaseTarget: event.eligibility.target, startPttBaseline: event.ptt.marker, modBarrier: event.ptt.marker, pttMarker: event.ptt.marker, localAudio: 'starting' as const, radioTx: 'off' as const, txRisk: 'none' as const, modRestorePending: true, fault: null };
    return { state: next, effects: [effect('start-audio', next), armEffect('arm-audio-timeout', next, next.timerRevision.audio)] };
  }
  if (event.type === 'intent') return sameGuard(state, event.guard) && event.sourceId === state.sourceId && state.phase !== 'releasing' && state.phase !== 'failed' ? { state: { ...state, intent: event.intent }, effects: [] } : { state, effects: [] };
  if (event.type === 'release') return sameGuard(state, event.guard) && (event.sourceId === undefined || event.sourceId === state.sourceId) ? release(state, event.commandId) : { state, effects: [] };
  if (event.type === 'reset-fault' && state.phase === 'failed' && !state.pendingOff && !state.modRestorePending && !state.mayOwnKey && !state.cleanupGuard) return { state: { ...clearLease(state), phase: 'idle', txRisk: 'none', fault: null }, effects: [] };
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
      const barrier = state.pendingOff ? state.pendingOff.deliveryPttBarrier : state.modBarrier;
      if (barrier && newer(barrier, event.ptt)) {
        const cleanupGuard = state.cleanupGuard ?? state.guard;
        const discharged = state.phase === 'releasing' ? { ...clearLease(next), phase: 'idle' as const, txRisk: 'none' as const, modRestorePending: false, fault: null } : state.fault === 'release-not-confirmed' ? { ...clearLease(next), phase: 'failed' as const, txRisk: 'none' as const, modRestorePending: false, fault: state.fault } : { ...next, cleanupGuard: null, modRestorePending: false };
        return { state: discharged, effects: [effect('cancel-timers', state, undefined, undefined, cleanupGuard), effect('restore-mod', state, undefined, barrier, cleanupGuard)] };
      }
    }
    if (state.guard && (!ready(event.eligibility) || !sameTarget(state.leaseTarget, event.eligibility.target))) return release(next, event.offCommandId);
    return { state: next, effects: [] };
  }
  if (event.type === 'authority' && event.epoch === state.authorityEpoch && state.guard && (!ready(event.eligibility) || !sameTarget(state.leaseTarget, event.eligibility.target))) return release(state, event.offCommandId);
  return { state, effects: [] };
}
