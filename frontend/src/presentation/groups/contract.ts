/**
 * Instrument group v1 schema, runtime validator, and compiled registry
 * (MOR-2253 slice 1) — the separate `InstrumentGroup` node the instrument-
 * group ADR's decided shape (b) calls for
 * (`docs/plans/2026-09-02-instrument-group-adr.md` §5/§10.2), mirroring
 * `../layouts/contract.ts` (MOR-1066). A group declares the canvas an
 * instrument's fixed-native stage is authored against and how that canvas
 * scales into whatever mounts it.
 *
 * `members`, `look` and `zone` from the ADR's illustrative schema (§4) are
 * NOT declared here: the owner ruling this slice implements is "a field is
 * declared only if it has a production reader", and none of those three has
 * one yet (see the PR body for why each is deferred).
 */
import { isValidLanguageId as isValidProductId } from '../languages/contract';

export interface GroupCanvas {
  readonly w: number;
  readonly h: number;
}

/** `fixed-native` mirrors `../layouts/contract.ts`'s `FixedNativeSizing` axis
 *  (MOR-1160) at the group level — the shell mounts the instrument's
 *  `ScaledStage` at `canvas.w`x`canvas.h`. `reflow` means no stage at all:
 *  the mounting container's own CSS decides (ADR §4, owner decision 6). */
export type GroupScaling =
  | { readonly mode: 'fixed-native'; readonly minScale: number }
  | { readonly mode: 'reflow' };

export interface InstrumentGroup {
  readonly schemaVersion: 1;
  readonly id: string;
  readonly canvas: GroupCanvas;
  readonly scaling: GroupScaling;
}

/** A registered group's id — kebab-case, same naming policy as a layout id.
 *  Used by `../layouts/contract.ts: LayoutZone.group` to reference a group
 *  by id rather than importing it. */
export type GroupId = string;

const TOP_LEVEL_KEYS: readonly PropertyKey[] = ['schemaVersion', 'id', 'canvas', 'scaling'];
const CANVAS_KEYS: readonly PropertyKey[] = ['w', 'h'];
const FIXED_NATIVE_SCALING_KEYS: readonly PropertyKey[] = ['mode', 'minScale'];
const REFLOW_SCALING_KEYS: readonly PropertyKey[] = ['mode'];

export class GroupValidationError extends Error {}

/** Exact-OWN-keys discipline — same idiom as `../layouts/contract.ts:
 *  hasExactPlainKeys` (MOR-1072 review precedent). Not imported from there:
 *  that function is module-private, and this codebase already carries one
 *  independent copy of the idiom per contract module (`../languages/
 *  contract.ts` has its own inlined copy too) rather than a shared helper. */
function hasExactPlainKeys(value: object, keys: readonly PropertyKey[]): boolean {
  const proto = Object.getPrototypeOf(value);
  if (proto !== Object.prototype && proto !== null) return false;
  return Reflect.ownKeys(value).every((k) => keys.includes(k));
}

/**
 * Same JSON.stringify-replacer idiom as `../layouts/contract.ts:
 * findCapabilityLikeKey` — required here because that scan only ever runs
 * over a `LayoutManifest` (ADR §3's boundary-sentence guard table: "No
 * capability facts in a declaration" needs a new guard once a group is a
 * separate node).
 */
const FORBIDDEN_KEY_MARKERS = ['capability', 'capabilities', 'radiomodel', 'vendor', 'manufacturer', 'firmware'];
function findCapabilityLikeKey(group: InstrumentGroup): string | null {
  let hit: string | null = null;
  JSON.stringify(group, (key, value) => {
    if (!hit && FORBIDDEN_KEY_MARKERS.some((m) => key.toLowerCase().includes(m))) hit = key;
    return value;
  });
  return hit;
}

/** Same idiom as `../layouts/contract.ts: findModulePathLikeValue` — the
 *  ADR §3 guard table's "No module paths in a declaration" row, also needing
 *  a new guard for the same reason as the capability scan above. */
const MODULE_PATH_VALUE_PATTERN = /^(\.{1,2}\/|\$lib\/|src\/|\/)/;
function findModulePathLikeValue(group: InstrumentGroup): string | null {
  let hit: string | null = null;
  JSON.stringify(group, (key, value) => {
    if (!hit && typeof value === 'string' && MODULE_PATH_VALUE_PATTERN.test(value)) hit = key;
    return value;
  });
  return hit;
}

function validateCanvas(id: string, canvas: GroupCanvas): string | null {
  if (!hasExactPlainKeys(canvas, CANVAS_KEYS)) {
    return `Group "${id}" canvas must declare only w/h.`;
  }
  if (!(typeof canvas.w === 'number' && Number.isFinite(canvas.w) && canvas.w > 0
    && typeof canvas.h === 'number' && Number.isFinite(canvas.h) && canvas.h > 0)) {
    return `Group "${id}" canvas requires positive finite w/h.`;
  }
  return null;
}

function validateScaling(id: string, scaling: GroupScaling): string | null {
  if (scaling.mode === 'reflow') {
    if (!hasExactPlainKeys(scaling, REFLOW_SCALING_KEYS)) {
      return `Group "${id}" reflow scaling must declare only mode.`;
    }
    return null;
  }
  if (scaling.mode === 'fixed-native') {
    if (!hasExactPlainKeys(scaling, FIXED_NATIVE_SCALING_KEYS)) {
      return `Group "${id}" fixed-native scaling must declare only mode/minScale.`;
    }
    if (!(typeof scaling.minScale === 'number' && Number.isFinite(scaling.minScale) && scaling.minScale > 0)) {
      return `Group "${id}" fixed-native scaling requires a positive finite minScale.`;
    }
    return null;
  }
  return `Group "${id}" scaling.mode must be 'fixed-native' | 'reflow'.`;
}

/** Throws with a descriptive message if `group` violates the v1 contract. */
export function validateInstrumentGroup(group: InstrumentGroup): void {
  const id = group.id;
  const capabilityHit = findCapabilityLikeKey(group);
  const modulePathHit = findModulePathLikeValue(group);
  // Same ordering discipline as `../layouts/contract.ts:
  // validateLayoutManifest` — the capability/module-path scan runs first, so
  // a poisoned group dies on that message even when another field is also
  // malformed.
  const problem =
    (capabilityHit && `Group "${id}" references a capability-shaped key "${capabilityHit}".`) ||
    (modulePathHit && `Group "${id}" references a module-path-shaped value at key "${modulePathHit}" — groups hold stable IDs, not paths.`) ||
    (!hasExactPlainKeys(group, TOP_LEVEL_KEYS) &&
      `Group "${id}" has unknown top-level key(s) — only [${TOP_LEVEL_KEYS.join(', ')}] are allowed.`) ||
    (group.schemaVersion !== 1 && `Group "${id}" schemaVersion must be 1.`) ||
    (!isValidProductId(id) && `Group id "${id}" fails naming policy: kebab-case, no vendor/geographic marker.`) ||
    validateCanvas(id, group.canvas) ||
    validateScaling(id, group.scaling);
  if (problem) throw new GroupValidationError(problem);
}

// ── Compiled registry. Count-agnostic: any group passing validation
// registers, same as ../layouts/contract.ts.

const registry = new Map<string, InstrumentGroup>();

/** Validates then registers `group`. Rejects a duplicate ID, same rationale
 *  as `../layouts/contract.ts: registerLayout`. */
export function registerGroup(group: InstrumentGroup): void {
  validateInstrumentGroup(group);
  if (registry.has(group.id)) {
    throw new GroupValidationError(`Group id "${group.id}" is already registered.`);
  }
  registry.set(group.id, group);
}

export function getGroup(id: string): InstrumentGroup | undefined {
  return registry.get(id);
}

export function listGroupIds(): readonly string[] {
  return Array.from(registry.keys());
}
