/**
 * Layout manifest v1 schema, runtime validator, and compiled registry
 * (MOR-1066) — the other side of the design-language handshake in
 * `../languages/contract.ts` (`./compatibility.ts` composes the two).
 * Declares identity, zones mounting semantic surfaces, compatible radio
 * topologies, the MOR-1160 stage-sizing axis, and a safe fallback — never
 * executable radio behavior, capability objects, or component module paths
 * (v3 ADR "Composition contract"; MOR-988/983 doctrine). Lint-enforced
 * presentation/ zone (MOR-1061) bans runtime/capability/transport/command
 * imports here and in every manifest.
 */
import type { Component } from 'svelte';
import { isValidLanguageId as isValidProductId } from '../languages/contract';

/** Semantic surfaces a layout may mount (MOR-1062/1065 reference vertical;
 *  `txAux` added by MOR-1265, `meters` by MOR-1273). Adding a name makes it
 *  DECLARABLE — it does not mount anything, and no manifest declares a
 *  `txAux` or `meters` zone yet. Distinct from the design-language renderer
 *  slot of the same name (`languages/contract.ts`'s `RENDERER_SLOT_NAMES`):
 *  that one says how a language DRAWS a meter, this one says which layout
 *  zone may HOST the surface. */
export const SEMANTIC_SURFACE_NAMES = ['vfo', 'rxTx', 'txAux', 'meters', 'rxAudio'] as const;
export type SemanticSurfaceName = (typeof SEMANTIC_SURFACE_NAMES)[number];

/**
 * The four canonical topology classes from the MOR-1062 fixtures
 * (`semantic/fixtures/topologies.ts`), `${receiverCount}/${vfoScheme}`.
 * Mirrored here as a standalone literal union instead of importing the
 * fixtures module — fixtures are a test asset, not a production dependency.
 */
export const TOPOLOGY_CLASSES = ['1/single', '1/ab', '2/ab_shared', '2/main_sub'] as const;
export type TopologyClass = (typeof TOPOLOGY_CLASSES)[number];

export interface LayoutZone {
  readonly id: string;
  readonly surfaces: readonly SemanticSurfaceName[];
}

/**
 * Sizing axis (MOR-1160, frozen 2026-07-29): `fluid` reflows on declared
 * breakpoints; `fixed-native` scales as one block by
 * `min(w/nativeW, h/nativeH)`, letterboxed, falling back below `minScale`.
 * Structurally exclusive at the type level (`FixedNativeSizing` has no
 * `responsiveBreakpoints` slot); the runtime validator enforces the same
 * exclusion against untyped input. See `LayoutManifest.stageSizing` below for
 * what this policy is scoped to.
 */
export interface FluidSizing {
  readonly mode: 'fluid';
  readonly responsiveBreakpoints: readonly number[];
}
export interface FixedNativeSizing {
  readonly mode: 'fixed-native';
  readonly nativeW: number;
  readonly nativeH: number;
  readonly minScale: number;
}
export type SizingPolicy = FluidSizing | FixedNativeSizing;

export interface LayoutManifest {
  readonly schemaVersion: 1;
  readonly id: string;
  readonly displayName: string;
  readonly loader: () => Promise<{ default: Component }>;
  readonly zones: readonly LayoutZone[];
  readonly compatibleTopologies: readonly TopologyClass[];
  readonly requiredSemanticSurfaces: readonly SemanticSurfaceName[];
  /**
   * The INSTRUMENT STAGE's sizing policy (MOR-1160/1247) — not the whole
   * layout. Chrome (nav, control columns, sidebars — anything that is not
   * the instrument glass) is fluid by doctrine and renders as a sibling of
   * the future `ScaledStage` primitive, which owns the stage's mechanical
   * enforcement of this policy (MOR-1160 constraint 1); until that primitive
   * exists this field stays declaration-only (guard test: `__tests__/
   * stage-sizing-boundary.test.ts`). A `fixed-native` value here does NOT
   * mean the whole layout is fixed-native — only its instrument stage is.
   *
   * MOR-1261 (owner decision, 2026-08-04): until a layout has an instrument
   * stage, this field's fluid `responsiveBreakpoints` may ALSO carry CHROME
   * reflow thresholds declaratively — legitimizing mobile's `[500]`
   * (MOR-1094) and the cockpit's `[768, 1024]` (MOR-1069) — the cockpit's
   * pair pinned by a CSS↔manifest agreement test, mobile's declared value
   * pinned at the manifest only. Interim carriage only: the field's primary
   * semantics stay the instrument stage's policy above, runtime still must
   * not read it (MOR-1247 declaration-only guard unchanged), and a per-zone
   * superset may supersede this later.
   */
  readonly stageSizing: SizingPolicy;
  readonly fallbackLayoutId: string | null;
}

const TOP_LEVEL_KEYS: readonly PropertyKey[] = [
  'schemaVersion', 'id', 'displayName', 'loader', 'zones',
  'compatibleTopologies', 'requiredSemanticSurfaces', 'stageSizing', 'fallbackLayoutId',
];

export class LayoutValidationError extends Error {}

/** Exact-OWN-keys discipline (MOR-1072 review precedent; radio-view-model.ts
 *  idiom): rejects a class-instance prototype leak and any extra own/symbol
 *  key `Object.keys` would miss. Extras-only, matching `../languages/
 *  contract.ts` (a TS-typed-author boundary, not a full untyped-JSON check). */
function hasExactPlainKeys(value: object, keys: readonly PropertyKey[]): boolean {
  const proto = Object.getPrototypeOf(value);
  if (proto !== Object.prototype && proto !== null) return false;
  return Reflect.ownKeys(value).every((k) => keys.includes(k));
}

function allIn<T>(values: readonly T[], allowed: readonly T[]): boolean {
  return values.every((v) => allowed.includes(v));
}

/** JSON.stringify-replacer idiom mirrored from the design-language contract's
 *  findCapabilityLikeKey: walks every nested key, skips function values — the
 *  `loader` closure's import specifier is invisible to it, by design. */
const FORBIDDEN_MANIFEST_KEY_MARKERS = ['capability', 'capabilities', 'radiomodel', 'vendor', 'manufacturer', 'firmware'];
function findCapabilityLikeKey(manifest: LayoutManifest): string | null {
  let hit: string | null = null;
  JSON.stringify(manifest, (key, value) => {
    if (!hit && FORBIDDEN_MANIFEST_KEY_MARKERS.some((m) => key.toLowerCase().includes(m))) hit = key;
    return value;
  });
  return hit;
}

/** Same idiom applied to VALUES: rejects a string shaped like a relative
 *  import (`./`, `../`), the `$lib/` alias, a bare `src/` reference, or an
 *  absolute path — the forms a real component specifier takes in this
 *  codebase. Not exhaustive (any string could theoretically resolve as a
 *  module elsewhere), but the compiled `loader` closure is the only field
 *  meant to carry one, and the scan never sees inside a function value. */
const MODULE_PATH_VALUE_PATTERN = /^(\.{1,2}\/|\$lib\/|src\/|\/)/;
function findModulePathLikeValue(manifest: LayoutManifest): string | null {
  let hit: string | null = null;
  JSON.stringify(manifest, (key, value) => {
    if (!hit && typeof value === 'string' && MODULE_PATH_VALUE_PATTERN.test(value)) hit = key;
    return value;
  });
  return hit;
}

function validateSizing(id: string, sizing: SizingPolicy): string | null {
  if (sizing.mode === 'fluid') {
    if (!hasExactPlainKeys(sizing, ['mode', 'responsiveBreakpoints'])
      || !Array.isArray(sizing.responsiveBreakpoints)
      || !sizing.responsiveBreakpoints.every((b) => typeof b === 'number')) {
      return `Layout "${id}" fluid stageSizing must declare only mode/responsiveBreakpoints (number array).`;
    }
    return null;
  }
  if (sizing.mode === 'fixed-native') {
    if (!hasExactPlainKeys(sizing, ['mode', 'nativeW', 'nativeH', 'minScale'])) {
      return `Layout "${id}" fixed-native stageSizing must declare only mode/nativeW/nativeH/minScale — ` +
        'no responsiveBreakpoints (MOR-1160: the two are mutually exclusive).';
    }
    const { nativeW, nativeH, minScale } = sizing;
    if (![nativeW, nativeH, minScale].every((n) => typeof n === 'number' && Number.isFinite(n) && n > 0)) {
      return `Layout "${id}" fixed-native stageSizing requires positive finite nativeW/nativeH/minScale.`;
    }
    return null;
  }
  return `Layout "${id}" stageSizing.mode must be 'fluid' | 'fixed-native'.`;
}

function validateZones(id: string, zones: readonly LayoutZone[]): string | null {
  for (const zone of zones) {
    if (typeof zone.id !== 'string' || zone.id.length === 0) {
      return `Layout "${id}" has a zone with an empty id.`;
    }
    if (!Array.isArray(zone.surfaces) || zone.surfaces.length === 0 || !allIn(zone.surfaces, SEMANTIC_SURFACE_NAMES)) {
      return `Layout "${id}" zone "${zone.id}" must declare a non-empty subset of [${SEMANTIC_SURFACE_NAMES.join(', ')}].`;
    }
  }
  return null;
}

/** Throws with a descriptive message if `manifest` violates the v1 contract. */
export function validateLayoutManifest(manifest: LayoutManifest): void {
  const id = manifest.id;
  const capabilityHit = findCapabilityLikeKey(manifest);
  const modulePathHit = findModulePathLikeValue(manifest);
  // Capability-fork and module-path rejection run FIRST, ahead of every
  // structural check below: "no capability objects, no module paths in any
  // manifest" is the doctrine violation (MOR-988/983), not a schema-shape
  // nicety, so it must not be masked by a more specific structural error
  // (e.g. an unknown top-level key, or a zone/surface mismatch) that
  // happens to fire on the same poisoned manifest. This only holds if
  // nothing below touches `manifest.zones`/etc. EAGERLY — a manifest with a
  // capability key AND a missing `zones` field must still die on the
  // capability message, not a raw TypeError from an early, unguarded read.
  // So every later check that needs derived data (e.g. which surfaces are
  // mounted) computes it lazily, inline, only once short-circuiting proves
  // every earlier term was clean.
  const problem =
    (capabilityHit && `Layout "${id}" references a capability-shaped key "${capabilityHit}".`) ||
    (modulePathHit && `Layout "${id}" references a module-path-shaped value at key "${modulePathHit}" — manifests hold stable IDs, not paths.`) ||
    (!hasExactPlainKeys(manifest, TOP_LEVEL_KEYS) &&
      `Layout "${id}" has unknown top-level key(s) — only [${TOP_LEVEL_KEYS.join(', ')}] are allowed.`) ||
    (manifest.schemaVersion !== 1 && `Layout "${id}" schemaVersion must be 1.`) ||
    (!isValidProductId(id) && `Layout id "${id}" fails naming policy: kebab-case, no vendor/geographic marker.`) ||
    (typeof manifest.loader !== 'function' && `Layout "${id}" must declare a compiled Svelte loader function.`) ||
    validateZones(id, manifest.zones) ||
    (manifest.compatibleTopologies.length === 0 &&
      `Layout "${id}" must declare at least one compatible topology class.`) ||
    (!allIn(manifest.compatibleTopologies, TOPOLOGY_CLASSES) &&
      `Layout "${id}" compatibleTopologies must be a subset of [${TOPOLOGY_CLASSES.join(', ')}].`) ||
    (manifest.requiredSemanticSurfaces.length === 0 &&
      `Layout "${id}" must declare at least one required semantic surface.`) ||
    (manifest.requiredSemanticSurfaces.some((s) => !manifest.zones.some((z) => z.surfaces.includes(s))) &&
      `Layout "${id}" requires a semantic surface that no zone mounts.`) ||
    validateSizing(id, manifest.stageSizing) ||
    (manifest.fallbackLayoutId !== null && !isValidProductId(manifest.fallbackLayoutId) &&
      `Layout "${id}" fallbackLayoutId "${manifest.fallbackLayoutId}" fails naming policy.`);
  if (problem) throw new LayoutValidationError(problem);
}

// ── Compiled registry. Count-agnostic: any manifest passing validation
// registers, same as the design-language registry (MOR-1072 precedent).

const registry = new Map<string, LayoutManifest>();

/**
 * Validates then registers `manifest`. Rejects a duplicate ID — unlike the
 * design-language registry's overwrite semantics, a silently swapped layout
 * loader can replace what is already resolved on screen, so MOR-1066 treats
 * "duplicate IDs" as its own rejection class rather than an overwrite.
 */
export function registerLayout(manifest: LayoutManifest): void {
  validateLayoutManifest(manifest);
  if (registry.has(manifest.id)) {
    throw new LayoutValidationError(`Layout id "${manifest.id}" is already registered.`);
  }
  registry.set(manifest.id, manifest);
}
export function getLayout(id: string): LayoutManifest | undefined {
  return registry.get(id);
}
export function listLayoutIds(): readonly string[] {
  return Array.from(registry.keys());
}

/** Resolves `manifest.fallbackLayoutId` and re-applies `criterion` to the
 *  fallback itself — a fallback that also fails the criterion (or points
 *  back at `manifest`) is not returned; the caller gets `undefined`, a typed
 *  "unresolvable" signal, not a layout that silently fails what it was
 *  fetched to satisfy (review cycle 1, F1: a self-referential or two-hop
 *  fallback, or a fixed-native fallback that itself fails minScale, used to
 *  come back unvalidated). v1 takes exactly one hop — it does not chase a
 *  chain — but that one hop must still pass. */
function resolveFallback(
  manifest: LayoutManifest,
  criterion: (candidate: LayoutManifest) => boolean,
): LayoutManifest | undefined {
  if (!manifest.fallbackLayoutId || manifest.fallbackLayoutId === manifest.id) return undefined;
  const fallback = getLayout(manifest.fallbackLayoutId);
  return fallback && criterion(fallback) ? fallback : undefined;
}

export function supportsTopology(manifest: LayoutManifest, topology: TopologyClass): boolean {
  return manifest.compatibleTopologies.includes(topology);
}

/**
 * MOR-1313 — every semantic surface `manifest`'s DECLARED ZONES mount, as one
 * flat set. The zone schema is untouched (risk R3): this derives entirely from
 * `zone.surfaces`, so a layout expresses "this area is semantic now" by
 * declaring the zone, never by a per-zone config field.
 *
 * This is what a shared v2 shell reads to decide, PER AREA, whether the
 * semantic vertical owns a surface or its legacy twin still renders — see
 * `components-v2/layout/RadioLayout.svelte`. Deliberately the MANIFEST and not
 * the resolved `SurfacePlan` (`../workspace/resolution`): the manifest is the
 * ceiling, and a workspace subtraction (which may only ever hide) must not be
 * able to bring a legacy twin back on screen.
 *
 * `undefined` — an id no manifest is registered under — yields the empty set,
 * i.e. "nothing is declared, keep every legacy presentation". That is the
 * fail-safe direction: an unresolvable layout renders the shipped v2 panels
 * rather than a screen the semantic vertical was never asked to fill.
 */
export function declaredSurfaces(
  manifest: LayoutManifest | undefined,
): ReadonlySet<SemanticSurfaceName> {
  const declared = new Set<SemanticSurfaceName>();
  for (const zone of manifest?.zones ?? []) {
    for (const surface of zone.surfaces) declared.add(surface);
  }
  return declared;
}

/** Resolves `id` against `topology`, falling back to `fallbackLayoutId` only
 *  when the fallback itself also supports `topology`. */
export function resolveLayoutForTopology(id: string, topology: TopologyClass): LayoutManifest | undefined {
  const manifest = getLayout(id);
  if (!manifest) return undefined;
  if (supportsTopology(manifest, topology)) return manifest;
  return resolveFallback(manifest, (candidate) => supportsTopology(candidate, topology));
}

export interface Viewport { readonly width: number; readonly height: number }

/** `fluid` always fits — breakpoints are reflow hints, not a hard gate in
 *  v1. `fixed-native` compares the achievable uniform scale against
 *  `minScale` (MOR-1160) instead of matching a breakpoint: a portrait
 *  mobile viewport fails this arithmetically (small height vs. a wide
 *  native design), with no separate mobile-detection branch. */
export function fitsViewport(manifest: LayoutManifest, viewport: Viewport): boolean {
  if (manifest.stageSizing.mode === 'fluid') return true;
  const { nativeW, nativeH, minScale } = manifest.stageSizing;
  return Math.min(viewport.width / nativeW, viewport.height / nativeH) >= minScale;
}

/** Same fallback path as `resolveLayoutForTopology` — falling below
 *  `minScale` triggers the normal fallback resolution (re-checked against
 *  the same viewport), not a special case and not an unvalidated return. */
export function resolveLayoutForViewport(id: string, viewport: Viewport): LayoutManifest | undefined {
  const manifest = getLayout(id);
  if (!manifest) return undefined;
  if (fitsViewport(manifest, viewport)) return manifest;
  return resolveFallback(manifest, (candidate) => fitsViewport(candidate, viewport));
}
