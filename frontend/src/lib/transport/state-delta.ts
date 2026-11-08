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

import type { StateUpdateEnvelope } from '../types/state';

/** One leaf `set` operation, as `scripts/gen_state_types.py` generates it. */
type LeafSetOperation = NonNullable<StateUpdateEnvelope['set']>[number];
export type StatePath = LeafSetOperation[0];

export interface StateDeltaOps {
  set: Array<readonly [StatePath, unknown]>;
  unset: StatePath[];
}

/** The delta fields of a parsed `StateUpdateEnvelope`. */
type DeltaFields = Pick<StateUpdateEnvelope, 'changed' | 'removed' | 'deltaVersion' | 'set' | 'unset'>;

const LEAF_DELTA_VERSION = 2;

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function isPath(value: unknown): value is StatePath {
  return Array.isArray(value) && value.length > 0 && value.every((key) => typeof key === 'string');
}

function isSetOperation(value: unknown): value is LeafSetOperation {
  return Array.isArray(value) && value.length === 2 && isPath(value[0]);
}

function isListOf<T>(value: unknown, isItem: (item: unknown) => item is T): value is T[] {
  return Array.isArray(value) && value.every(isItem);
}

const isKey = (value: unknown): value is string => typeof value === 'string';

/** Parse the delta fields against the generated envelope contract. */
function hasDeltaFields(envelope: Record<string, unknown>): envelope is Record<string, unknown> & DeltaFields {
  const { changed, removed, deltaVersion, set, unset } = envelope;
  return (changed == null || isRecord(changed))
    && (removed == null || isListOf(removed, isKey))
    && (deltaVersion == null || deltaVersion === LEAF_DELTA_VERSION)
    && (set == null || isListOf(set, isSetOperation))
    && (unset == null || isListOf(unset, isPath));
}

/** The operations a delta envelope carries, or null when it is malformed. */
export function stateDeltaOps(envelope: Record<string, unknown>): StateDeltaOps | null {
  if (!hasDeltaFields(envelope)) return null;
  if (envelope.deltaVersion == null) {
    if (!envelope.changed) return null;
    return {
      set: Object.entries(envelope.changed).map(([key, value]) => [[key], value] as const),
      unset: (envelope.removed ?? []).map((key) => [key]),
    };
  }
  if (!envelope.set) return null;
  return { set: envelope.set, unset: envelope.unset ?? [] };
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
