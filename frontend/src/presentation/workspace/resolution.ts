/**
 * MOR-1082 — the workspace → PRESENTATION resolution seam.
 *
 * The sibling of `activation.ts` (MOR-1081) for the workspace's remaining
 * coarse fields: `density`, `visibleSurfaces` and `zoneOrder`. Same shape,
 * same reasons — one place that decides what a validated preference MEANS on
 * screen, taking the manifests the caller already resolved, so the workspace
 * zone never imports a registry, a component or a store.
 *
 * Pure: no storage, no DOM, no registry lookup. The only non-type import
 * beyond `activation.ts` is Svelte's context pair, which is how the
 * composition root hands the resolved plan to the semantic vertical without
 * the vertical importing a layout manifest (that would close the
 * manifest → loader → skin → wiring cycle the MOR-1068 wiring documents).
 *
 * WHAT THE WORKSPACE MAY DO, AND WHAT IT MAY NOT.
 *
 *   MAY   further hide a surface inside a zone that already declares it;
 *         reorder the surfaces inside one zone.
 *   MAY NOT  force-show — the manifest's `zone.surfaces` is the ceiling, so
 *         an id the zone does not declare is ignored rather than mounted;
 *         move a surface across zones (the v1 contract already refuses a
 *         duplicate across zones on read, and this must not open a second
 *         door); strip a `requiredSemanticSurfaces` entry from every zone —
 *         a persisted preference may not break the invariant a manifest is
 *         not allowed to declare its way out of, and for `rxTx` that
 *         invariant is also the only unkey affordance on screen.
 *
 * Self-gating is untouched (S0 doctrine): a surface whose view-model group is
 * absent still does not render, whatever the workspace says. The plan can
 * only take a surface away from the vertical, never give it one.
 */
import { getContext, setContext } from 'svelte';
import type { DensityLevel, DesignLanguageManifest } from '../languages/contract';
import type { LayoutManifest, LayoutZone, SemanticSurfaceName } from '../layouts/contract';
import { designLanguageActivation } from './activation';
import type { WorkspaceV1 } from './contract';

/**
 * The density that is actually in force, or `null` for "no density active".
 *
 * Hybrid, per the MOR-1076 freeze: the language's own default unless the
 * operator overrode it, and the override is honoured only INSIDE the ACTIVE
 * language's `DensityClamp` — read live off the manifest handed in, never off
 * `workspace.designLanguage` and never off the pinned `WORKSPACE_DENSITY_CLAMP`
 * mirror. `fieldline` has no `dense`, so a workspace carrying `dense` resolves
 * to `comfortable` wherever fieldline is the language on screen.
 *
 * Gated by `designLanguageActivation`, so density can never activate on a
 * layout the language has not declared: every shipped v2 skin keeps exactly
 * the density-free presentation it has today until the cutover
 * (MOR-1048/MOR-1263).
 */
export function densityActivation(
  manifest: DesignLanguageManifest | undefined,
  layoutId: string,
  override: DensityLevel,
): DensityLevel | null {
  if (manifest === undefined || designLanguageActivation(manifest, layoutId) === null) return null;
  const clamp = manifest.density;
  if (clamp.kind === 'not-applicable') return override;
  if (clamp.supported.includes(override)) return override;
  return clamp.supported[0] ?? null;
}

/** Zone id → the ordered surfaces that may mount there, after the workspace. */
export type SurfacePlan = ReadonlyMap<string, readonly SemanticSurfaceName[]>;

/** `WorkspaceV1`'s zone maps are keyed by the `WorkspaceZoneId` union while a
 *  manifest zone id is an open `string`; this is the one place the two id
 *  spaces meet. A miss is `undefined` = "the operator expressed nothing". */
function forZone(
  map: WorkspaceV1['visibleSurfaces'],
  id: string,
): readonly SemanticSurfaceName[] | undefined {
  return (map as unknown as Record<string, readonly SemanticSurfaceName[] | undefined>)[id];
}

const NOTHING_FORCED: ReadonlySet<SemanticSurfaceName> = new Set();

function resolveZone(
  zone: LayoutZone,
  workspace: WorkspaceV1,
  forced: ReadonlySet<SemanticSurfaceName>,
): SemanticSurfaceName[] {
  const allowed = forZone(workspace.visibleSurfaces, zone.id);
  // Intersection with the declared set, in declared order: this single
  // expression is what makes force-show and cross-zone structurally
  // impossible — nothing outside `zone.surfaces` can survive it.
  const kept = allowed === undefined
    ? [...zone.surfaces]
    : zone.surfaces.filter((surface) => allowed.includes(surface) || forced.has(surface));
  const sequence = forZone(workspace.zoneOrder, zone.id);
  if (sequence === undefined) return kept;
  // A partial order leads; whatever it did not name keeps its DECLARED
  // relative position behind it, so an incomplete write is normalized rather
  // than treated as a hide.
  const rank = (surface: SemanticSurfaceName): number => {
    const named = sequence.indexOf(surface);
    return named === -1 ? sequence.length + zone.surfaces.indexOf(surface) : named;
  };
  return kept.sort((a, b) => rank(a) - rank(b));
}

/**
 * The active layout's zones, resolved against an ALREADY VALIDATED workspace.
 * Validation is the store's boundary (MOR-1077/1079) and is deliberately not
 * repeated here: an unknown zone or surface id never reaches this function.
 */
export function resolveSurfacePlan(manifest: LayoutManifest, workspace: WorkspaceV1): SurfacePlan {
  const build = (forced: ReadonlySet<SemanticSurfaceName>): SemanticSurfaceName[][] =>
    manifest.zones.map((zone) => resolveZone(zone, workspace, forced));
  const first = build(NOTHING_FORCED);
  const mounted = new Set(first.flat());
  const uncovered = manifest.requiredSemanticSurfaces.filter((surface) => !mounted.has(surface));
  const resolved = uncovered.length === 0 ? first : build(new Set(uncovered));
  return new Map(manifest.zones.map((zone, index) => [zone.id, resolved[index]]));
}

/**
 * The plan as ONE ordered region — what the wiring's single composition
 * renders, since it mounts everything the layout declares without binding a
 * zone element (MOR-1069). Falls back whenever there is no plan to read (a
 * standalone mount with no composition root above it) or when the plan would
 * compose to nothing at all: the vertical must never resolve to a screen with
 * no surfaces, least of all no RX/TX surface.
 */
export function compositionSurfaces(
  plan: SurfacePlan | null,
  fallback: readonly SemanticSurfaceName[],
): readonly SemanticSurfaceName[] {
  if (plan === null) return fallback;
  const composed: SemanticSurfaceName[] = [];
  for (const surfaces of plan.values()) {
    for (const surface of surfaces) if (!composed.includes(surface)) composed.push(surface);
  }
  return composed.length === 0 ? fallback : composed;
}

/**
 * The composition root's channel to the semantic vertical. A Svelte context
 * rather than a module singleton so nothing leaks between mounts, and a
 * GETTER rather than a value so the consumer's `$derived` re-runs when the
 * workspace or the resolved layout changes.
 *
 * Absent by design in a standalone component mount: `useSurfacePlan()` then
 * yields `null` and every consumer renders its declared composition
 * unchanged. Exported key so a test (or the MOR-1070 fixture harness) can
 * supply a plan through `mount`'s `context` option.
 */
export const SURFACE_PLAN_CONTEXT_KEY = Symbol('WorkspaceSurfacePlan');
export type SurfacePlanSource = () => SurfacePlan | null;
const NO_PLAN: SurfacePlanSource = () => null;

export function provideSurfacePlan(source: SurfacePlanSource): void {
  setContext(SURFACE_PLAN_CONTEXT_KEY, source);
}

export function useSurfacePlan(): SurfacePlanSource {
  return getContext<SurfacePlanSource | undefined>(SURFACE_PLAN_CONTEXT_KEY) ?? NO_PLAN;
}

/** Is `surface` still mounted in `zoneId`? "No plan" and "the active layout
 *  declares no such zone" both mean UNCHANGED — this can only ever hide. */
export function zoneShowsSurface(
  plan: SurfacePlan | null,
  zoneId: string,
  surface: SemanticSurfaceName,
): boolean {
  const zone = plan?.get(zoneId);
  return zone === undefined || zone.includes(surface);
}
