const LIVE_METADATA_KEYS = new Set([
  'connection',
  'fieldStatus',
  'healthRevision',
  'publicStateSeq',
  'radioHealth',
  'transportSeq',
  'updatedAt',
  'wsClients',
]);

function valuesEqual(left: unknown, right: unknown): boolean {
  if (Object.is(left, right)) return true;
  try {
    return JSON.stringify(left) === JSON.stringify(right);
  } catch {
    return false;
  }
}

/**
 * The counters a stored state is compared against when deciding whether a
 * pushed state is an advance. `state` is the stored state itself, kept so
 * the delivery-sequence path can compare values, not key names.
 */
export interface StateAdvanceBaseline {
  stateRevision: number;
  freshnessRevision: number;
  observationSeq: number;
  healthRevision: number;
  deliverySeq: number;
  state: object;
}

/**
 * Whether `next` advances over `baseline` under the store's rule: a null
 * baseline accepts, otherwise the semantic revision must advance, or be
 * equal with a freshness, observation or health advance, or be equal with
 * a delivery advance that changes only live-metadata values.
 */
export function isStateAdvance(baseline: StateAdvanceBaseline | null, next: object): boolean {
  if (baseline === null) return true;
  const record = next as Record<string, unknown>;
  const nextRevision = (record.stateRevision as number | undefined)
    ?? (record.revision as number | undefined);
  if (nextRevision === undefined) return false;
  if (nextRevision > baseline.stateRevision) return true;
  if (nextRevision !== baseline.stateRevision) return false;
  const nextFreshness = (record.freshnessRevision as number | undefined) ?? 0;
  const nextObservation = (record.observationSeq as number | undefined) ?? 0;
  const nextHealth = (record.healthRevision as number | undefined) ?? 0;
  const nextDelivery = Math.max(
    (record.publicStateSeq as number | undefined) ?? 0,
    (record.transportSeq as number | undefined) ?? 0,
  );
  return nextFreshness > baseline.freshnessRevision
    || nextObservation > baseline.observationSeq
    || nextHealth > baseline.healthRevision
    || (nextDelivery > baseline.deliverySeq && hasOnlyLiveMetadataChanges(baseline.state, next));
}

export function hasOnlyLiveMetadataChanges(current: object | null, next: object): boolean {
  if (!current) return false;
  const currentRecord = current as unknown as Record<string, unknown>;
  const nextRecord = next as unknown as Record<string, unknown>;
  const keys = new Set([...Object.keys(current), ...Object.keys(next)]);
  for (const key of keys) {
    if (valuesEqual(currentRecord[key], nextRecord[key])) {
      continue;
    }
    if (!LIVE_METADATA_KEYS.has(key)) {
      return false;
    }
  }
  return true;
}
