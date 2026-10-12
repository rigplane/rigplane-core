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

export function hasOnlyLiveMetadataChanges(current: object | null, next: object): boolean {
  return false;
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
