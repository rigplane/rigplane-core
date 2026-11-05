/**
 * MOR-4206: one applier for both `state_update` delta renderings
 * (`StateUpdateEnvelope` in `src/rigplane/web/state_schema.py`).
 *
 * A leaf delta (`deltaVersion: 2`) carries `set: [path, value][]` and
 * `unset: path[]`; a top-level delta carries `changed` / `removed`, which is
 * the same thing with every path one key long. The applier copies only the
 * objects along a written path, so every untouched subtree of the previous
 * state keeps its object identity.
 */

export type StatePath = readonly string[];

export interface StateDeltaOps {
  set: Array<readonly [StatePath, unknown]>;
  unset: StatePath[];
}

const LEAF_DELTA_VERSION = 2;

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function isPath(value: unknown): value is StatePath {
  return Array.isArray(value) && value.length > 0 && value.every((key) => typeof key === 'string');
}

/** The operations a delta envelope carries, or null when it is malformed. */
export function stateDeltaOps(envelope: Record<string, unknown>): StateDeltaOps | null {
  if (envelope.deltaVersion === undefined) {
    const { changed, removed } = envelope;
    if (!isRecord(changed)) return null;
    if (removed !== undefined && (!Array.isArray(removed) || !removed.every((key) => typeof key === 'string'))) return null;
    return {
      set: Object.entries(changed).map(([key, value]) => [[key], value] as const),
      unset: ((removed ?? []) as string[]).map((key) => [key]),
    };
  }
  if (envelope.deltaVersion !== LEAF_DELTA_VERSION) return null;
  const { set, unset } = envelope;
  if (!Array.isArray(set)) return null;
  if (!set.every((op) => Array.isArray(op) && op.length === 2 && isPath(op[0]))) return null;
  if (unset !== undefined && (!Array.isArray(unset) || !unset.every(isPath))) return null;
  return {
    set: set.map((op) => [op[0] as StatePath, op[1]] as const),
    unset: (unset ?? []) as StatePath[],
  };
}

/**
 * Apply `ops` to `state` without mutating it. Returns null when a path runs
 * through a missing or non-object value.
 */
export function applyStateDelta(
  state: Record<string, unknown>,
  ops: StateDeltaOps,
): Record<string, unknown> | null {
  const root: Record<string, unknown> = { ...state };
  const fresh = new Set<object>([root]);
  const parentFor = (path: StatePath): Record<string, unknown> | null => {
    let node = root;
    for (const key of path.slice(0, -1)) {
      const child = node[key];
      if (!isRecord(child)) return null;
      if (fresh.has(child)) {
        node = child;
        continue;
      }
      const copy = { ...child };
      node[key] = copy;
      fresh.add(copy);
      node = copy;
    }
    return node;
  };
  for (const [path, value] of ops.set) {
    const parent = parentFor(path);
    if (!parent) return null;
    parent[path[path.length - 1]] = value;
  }
  for (const path of ops.unset) {
    const parent = parentFor(path);
    if (!parent) return null;
    delete parent[path[path.length - 1]];
  }
  return root;
}
