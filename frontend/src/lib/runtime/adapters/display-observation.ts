import type { Capabilities } from '$lib/types/capabilities';
import type { FieldStatus, ServerState } from '$lib/types/state';
import type { DisplayObservation, ReceiverId } from '../../../semantic/radio-view-model';

type Scalar = number | string | boolean;

function hasObservation(status: FieldStatus): boolean {
  return status.observed === true;
}

function validEvidence(status: FieldStatus): boolean {
  return (status.freshness === 'fresh' || status.freshness === 'stale')
    && (status.availability === 'available' || status.availability === 'stale')
    && typeof status.lastObservedMonotonic === 'number'
    && Number.isFinite(status.lastObservedMonotonic) && status.lastObservedMonotonic >= 0;
}

export function qualifyDisplayObservation<T extends Scalar>({
  state, caps, receiver, path, structural, value,
}: {
  state: ServerState | null;
  caps: Capabilities | null;
  receiver: ReceiverId;
  path: string;
  structural: boolean;
  value: T | undefined;
}): DisplayObservation<T> {
  if (!structural) return { state: 'unsupported' };
  const generation = state?.providerGeneration;
  const receiverKey = receiver === 'MAIN' ? 'main' : 'sub';
  if (!state || !caps || state.stateContractVersion !== 1 || caps.stateContractVersion !== 1
    || typeof generation !== 'number' || !Number.isSafeInteger(generation) || generation < 0
    || caps.providerGeneration !== generation
    || (caps.receivers !== 1 && caps.receivers !== 2)
    || (receiver === 'SUB' && caps.receivers !== 2)
    || (state.active === 'SUB' && caps.receivers !== 2)
    || !state[receiverKey] || !path.startsWith(`${receiverKey}.`)) {
    return { state: 'unknown', reason: 'identity-unresolved' };
  }
  const leaf = state.fieldStatus?.[path];
  if (!leaf || !hasObservation(leaf)) return { state: 'unknown', reason: 'not-observed' };
  const statuses = [leaf];
  let prefix = path;
  for (let dot = prefix.lastIndexOf('.'); dot > 0; dot = prefix.lastIndexOf('.')) {
    prefix = prefix.slice(0, dot);
    const parent = state.fieldStatus?.[prefix];
    if (!parent) continue;
    if (!hasObservation(parent)) return { state: 'unknown', reason: 'not-observed' };
    statuses.push(parent);
  }
  if (!statuses.every(validEvidence)) return { state: 'unknown', reason: 'invalid-evidence' };
  if ((typeof value !== 'number' && typeof value !== 'boolean' && typeof value !== 'string')
    || (typeof value === 'number' && !Number.isFinite(value)) || value === '') {
    return { state: 'unknown', reason: 'invalid-value' };
  }
  const stale = statuses.some((status) => status.freshness === 'stale' || status.availability === 'stale');
  return { state: stale ? 'stale' : 'current', value };
}
