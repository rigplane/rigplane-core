import { modInputStateKey } from '$lib/radio/mod-input';
import { deriveTxCapabilities, type ModInputSource, type TxCapabilityFacts } from '$lib/runtime/adapters/tx-capabilities';
import type { Capabilities } from '$lib/types/capabilities';
import type { ServerState, UnknownTxTargetPublic } from '$lib/types/state';
import type { ControlSessionTransition } from '$lib/transport/ws-client';
import type { Eligibility, PttObservation, TxTarget } from './model';
export interface AppAuthorityProjection {
  epoch: number; facts: TxCapabilityFacts | null; modInputSource: ModInputSource;
  eligibility: Eligibility; ptt: PttObservation;
}
type AuthoritySource = PttObservation['source'];
function authoritySource(source: unknown): AuthoritySource {
  switch (source) {
    case 'civ_unsolicited': return 'backend-observation';
    case 'poll_response': case 'hamlib_response': case 'yaesu_poll_response': return 'radio-readback';
    default: return 'other';
  }
}
function available(state: ServerState, key: string): boolean {
  const status = state.fieldStatus?.[key];
  return status?.observed === true && status.freshness === 'fresh'
    && status.availability === 'available';
}
function projectInputs(state: ServerState | null) {
  if (!state) {
    return {
      txTarget: { status: 'unknown', reason: 'not-observed' } as UnknownTxTargetPublic,
      modInputSource: { status: 'unknown' } as ModInputSource,
    };
  }
  const txTarget = available(state, 'txTarget') ? state.txTarget : {
    status: 'unknown' as const,
    reason: state.fieldStatus?.txTarget?.availability === 'stale' ? 'stale' as const : 'not-observed' as const,
  };
  const receiver = state.active === 'SUB' ? state.sub : state.main;
  const key = modInputStateKey(receiver?.dataMode ?? 0);
  const source = state[key];
  const modInputSource: ModInputSource = available(state, key)
    && typeof source === 'number' && Number.isSafeInteger(source)
    ? { status: 'known', source }
    : { status: 'unknown' };
  return { txTarget, modInputSource };
}
function controllerTarget(facts: TxCapabilityFacts | null): TxTarget {
  const target = facts?.txTarget;
  return target?.status === 'known' && typeof target.frequencyHz === 'number'
    && Number.isFinite(target.frequencyHz)
    ? { receiver: target.receiver, slot: target.slot, frequencyHz: target.frequencyHz }
    : null;
}
export function createAppAuthorityProjector() {
  let epoch = -1; let ordinal = 0;
  let lastPttAt: number | null = null;
  let needsBaseline = true;
  return (state: ServerState | null, capabilities: Capabilities | null,
    session: ControlSessionTransition): AppAuthorityProjection => {
    const validEpoch = Number.isSafeInteger(session.epoch) && session.epoch >= 0;
    const lowerEpoch = !validEpoch || session.epoch < epoch;
    if (!lowerEpoch && session.epoch > epoch) {
      epoch = session.epoch;
      lastPttAt = null;
      needsBaseline = true;
    }
    const currentSession = validEpoch && session.epoch === epoch;
    const controlLive = currentSession && session.state === 'connected';
    const inputs = projectInputs(state);
    const facts = capabilities ? deriveTxCapabilities(capabilities, inputs) : null;
    const target = controllerTarget(facts);
    const eligibility: Eligibility = {
      catPtt: facts?.catPttAvailable ?? false, browserTxAudio: facts?.browserTxAudioAvailable ?? false,
      controlLive, permit: facts?.frequencyPermit.status ?? 'unknown', target,
    };
    const status = state?.fieldStatus?.ptt;
    const source = authoritySource(status?.source?.source);
    const at = status?.lastObservedMonotonic;
    const qualifies = controlLive
      && typeof state?.ptt === 'boolean'
      && status?.observed === true
      && status.freshness === 'fresh'
      && status.availability === 'available'
      && typeof at === 'number'
      && Number.isFinite(at)
      && source !== 'other'
      && (lastPttAt === null || at > lastPttAt);
    const baseline = qualifies && needsBaseline;
    if (qualifies) { ordinal += 1; lastPttAt = at; needsBaseline = false; }
    return {
      epoch, facts, modInputSource: inputs.modInputSource, eligibility,
      ptt: {
        value: typeof state?.ptt === 'boolean' ? state.ptt : false,
        observed: status?.observed === true, fresh: qualifies && !baseline, source,
        marker: {
          authorityEpoch: epoch,
          pttObservationSeq: ordinal,
          pttLastObservedMonotonic: lastPttAt,
        },
      },
    };
  };
}
