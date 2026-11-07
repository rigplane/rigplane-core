/**
 * MOR-4213 test support: change a radio state snapshot the way the client
 * does, never in place.
 *
 * Production snapshots are immutable (`toRadioViewModel` and other
 * projections memoize on their identity): the WebSocket client turns every
 * delta into a NEW root object whose changed branches are new and whose
 * untouched branches keep their identity (`applyStateDelta`). Tests that
 * edited a fixture in place modelled a writer that does not exist.
 *
 * `editState(state, draft => { draft.main.freqHz = 7_074_000; })` runs the
 * edit on a scratch copy, records every path it wrote or deleted, and builds
 * the result with the client's own applier, so the new snapshot has exactly
 * the identity pattern a real delivery has.
 *
 * Capabilities are replaced as a whole in production (`setCapabilities`
 * stores the body `fetchCapabilities` parsed), so `editCaps` returns an
 * edited deep copy.
 */
import { applyStateDelta, type StatePath } from '../../state-delta';

type Recorded = string[];

/** Recording wrapper → the scratch object it wraps, so a wrapper is never stored. */
const scratchOf = new WeakMap<object, object>();
/**
 * A written value may carry wrappers inside it (`{ ...draft.main }` copies the
 * wrapped `vfoA`); swap each for its scratch object. Scratch objects never hold
 * a wrapper, because every write goes through here.
 */
function unwrap(value: unknown, seen = new Set<object>()): unknown {
  if (!isContainer(value)) return value;
  const scratch = scratchOf.get(value);
  if (scratch !== undefined) return scratch;
  if (seen.has(value)) return value;
  seen.add(value);
  for (const key of Object.keys(value)) {
    const child = (value as Record<string, unknown>)[key];
    const plain = unwrap(child, seen);
    if (plain !== child) (value as Record<string, unknown>)[key] = plain;
  }
  return value;
}

function isContainer(value: unknown): value is object {
  return value !== null && typeof value === 'object' && !ArrayBuffer.isView(value);
}

/**
 * Wrap `target` so every write records the state path it changes. Anything
 * inside an array records the array's own path: the applier copies records
 * along a path, so an array is always written as a whole.
 */
function record<T extends object>(
  target: T, path: Recorded, written: Recorded[], pinned: Recorded | null = null,
): T {
  const wrapper = new Proxy(target, {
    get(object, key, receiver) {
      const value = Reflect.get(object, key, receiver);
      if (typeof key !== 'string' || !isContainer(value)) return value;
      const childPath = [...path, key];
      return record(value, childPath, written, pinned ?? (Array.isArray(value) ? childPath : null));
    },
    set(object, key, value, receiver) {
      if (typeof key === 'string') written.push(pinned ?? [...path, key]);
      return Reflect.set(object, key, unwrap(value), receiver);
    },
    deleteProperty(object, key) {
      if (typeof key === 'string') written.push(pinned ?? [...path, key]);
      return Reflect.deleteProperty(object, key);
    },
  });
  scratchOf.set(wrapper, target);
  return wrapper;
}

function lookup(root: unknown, path: Recorded): { found: boolean; value: unknown } {
  let node = root;
  for (const key of path) {
    if (!isContainer(node) || !Object.hasOwn(node, key)) return { found: false, value: undefined };
    node = (node as Record<string, unknown>)[key];
  }
  return { found: true, value: node };
}

const covers = (ancestor: Recorded, path: Recorded) =>
  ancestor.length < path.length && ancestor.every((key, index) => path[index] === key);

/** A new snapshot of `state` with `edit` applied, built by the client's delta applier. */
export function editState<S extends object>(state: S, edit: (draft: S) => void): S {
  const draft = structuredClone(state);
  const written: Recorded[] = [];
  edit(record(draft, [], written));
  const unique = [...new Map(written.map((path) => [JSON.stringify(path), path])).values()];
  const paths = unique.filter((path) => path.length > 0
    && !unique.some((other) => covers(other, path)));
  const set: Array<readonly [StatePath, unknown]> = [];
  const unset: StatePath[] = [];
  for (const path of paths) {
    const { found, value } = lookup(draft, path);
    if (found) set.push([path as StatePath, value]);
    else if (lookup(state, path).found) unset.push(path as StatePath);
  }
  const next = applyStateDelta(state as Record<string, unknown>, { set, unset });
  if (next === null) throw new Error('editState: an edited path runs through a missing branch');
  return next as S;
}

/** A new capabilities body with `edit` applied (production replaces the whole body). */
export function editCaps<C extends object>(caps: C, edit: (draft: C) => void): C {
  const draft = structuredClone(caps);
  edit(draft);
  return draft;
}
