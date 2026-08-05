/**
 * Design-language token and renderer contract (MOR-1072): stable ID,
 * required token groups, renderer slots, the structural view-model shape
 * renderers may consume, and the registry that validates and stores
 * declared families. Contract only — no visual family here (MOR-1073
 * `studioline` / MOR-1074 `fieldline`, registered in `./declarations`).
 * Themes vary token *values*; languages vary token *groups and renderers*.
 * Neither imports runtime/capability/transport/command code — enforced at
 * lint time by the presentation/ zone (MOR-1061). See v3 ADR and MOR-977
 * §4 (frozen 2026-08-03).
 */

/**
 * Naming policy (MOR-977 §4.6, MOR-1071): kebab-case, no vendor/model
 * marker substring (matches inside a segment too, e.g. `icom7610` — review
 * cycle 1, B2). Advisory only for geography: this is a denylist, not a
 * classifier — a real place name the list doesn't enumerate (e.g.
 * `nippon-line`) still passes. Catching that class is a human review call,
 * not a string match; this list only blocks the markers it explicitly names.
 * No bare `ic`/`ft`/`ts` word marker (review cycle 2, N3) — those 2-3 letter
 * strings occur inside ordinary words (`classic-instrument`, `atomic-rail`)
 * far more often than as a vendor prefix. Model numbers (ic-7610, ft-991,
 * ts890, …) are instead anchored by MODEL_NUMBER_PATTERN below, which
 * requires a trailing digit — MOR-977 §4.4 preserves `meridian`
 * (`classic-instrument`) as a revivable future family, so that id must stay
 * valid.
 */
const FORBIDDEN_ID_MARKERS = [
  'icom', 'yaesu', 'kenwood', 'elecraft', 'flex', 'xiegu', 'alinco',
  'japanese', 'japan', 'chinese', 'china', 'korean', 'american', 'german',
] as const;
const MODEL_NUMBER_PATTERN = /(^|-)(ic|ft|ts)-?\d/;
const ID_PATTERN = /^[a-z][a-z0-9]*(-[a-z0-9]+)*$/;

export function isValidLanguageId(id: string): boolean {
  if (!ID_PATTERN.test(id)) return false;
  const flat = id.toLowerCase();
  if (MODEL_NUMBER_PATTERN.test(flat)) return false;
  return !(FORBIDDEN_ID_MARKERS as readonly string[]).some((m) => flat.includes(m));
}

/** Required groups: typography, geometry, meters, frequency, motion, focus ring, RX/TX. Density and renderer slots are manifest-level, not tokens. */
export interface StateFeedbackTokens { readonly idle: string; readonly active: string; readonly tuning: string }

export const REQUIRED_TOKEN_GROUPS = [
  'typography', 'geometry', 'meters', 'frequency', 'motion', 'focusRing', 'rx', 'tx',
] as const;
export type TokenGroupName = (typeof REQUIRED_TOKEN_GROUPS)[number];

export interface DesignLanguageTokens {
  readonly typography: { readonly fontFamily: string; readonly weight: number; readonly fontVariantNumeric: 'tabular-nums' }; // mandatory, MOR-977 §4.5.3
  readonly geometry: { readonly radius: string; readonly borderWidth: string };
  readonly meters: { readonly trackWidth: string; readonly segmentGap: string };
  readonly frequency: { readonly digitWeight: number; readonly rankedGroups: boolean };
  readonly motion: { readonly durationMs: number; readonly reducedMotionSafe: boolean };
  readonly focusRing: string; // mandatory, non-empty — MOR-1232 token half
  readonly rx: StateFeedbackTokens; // MOR-1231, symmetric to tx
  readonly tx: StateFeedbackTokens;
}

/** Density is workspace-owned with a per-language clamp (MOR-1072 review note). */
export type DensityLevel = 'comfortable' | 'compact' | 'dense';
export type DensityClamp =
  | { readonly kind: 'not-applicable' }
  | { readonly kind: 'clamped'; readonly supported: readonly DensityLevel[] };

/** Layout compatibility is a manifest declaration, not a capability check. */
export interface LayoutCompatibilityDeclaration { readonly layoutId: string; readonly compatible: boolean; readonly reason?: string }

/**
 * Renderers receive a RendererViewModel plus tokens — nothing else.
 * `fields` is flat primitives only, so a capability object (mode lists,
 * nested scope/antenna descriptors, a bundled radio-model string) cannot
 * satisfy this type: the signature has no slot to receive one. Exact-keys
 * checked at runtime by `isRendererViewModel` (review cycle 1, B1) — a
 * third top-level key (e.g. a smuggled `capabilities` payload riding
 * alongside valid `kind`/`fields`) is rejected, not silently ignored.
 *
 * Seam with MOR-1062: this is NOT the semantic-UI view model
 * (`RadioViewModel`, landing in `frontend/src/semantic/radio-view-model.ts`).
 * The roles are distinct — `RadioViewModel` is the adapter→semantic-UI
 * contract and may carry `{status:'unknown'}` unions; `RendererViewModel`
 * is the flat projection a design-language renderer consumes. Projecting
 * one into the other — and preserving `'unknown'` rather than collapsing
 * it to a default — is owned by MOR-1243, not this file.
 */
export const RENDERER_SLOT_NAMES = ['meters', 'frequencyDisplay', 'stateFeedback'] as const;
export type RendererSlotName = (typeof RENDERER_SLOT_NAMES)[number];

export interface RendererViewModel { readonly kind: string; readonly fields: Readonly<Record<string, string | number | boolean | null>> }
const RENDERER_VIEW_MODEL_KEYS: readonly PropertyKey[] = ['kind', 'fields'];

export type Renderer<TViewModel extends RendererViewModel = RendererViewModel> =
  (viewModel: TViewModel, tokens: DesignLanguageTokens) => unknown;

/** Runtime-enforced twin of the RendererViewModel type check — exact keys, not just present ones. */
export function isRendererViewModel(value: unknown): value is RendererViewModel {
  if (typeof value !== 'object' || value === null) return false;
  const proto = Object.getPrototypeOf(value); // N1: reject class instances — a prototype getter can carry an extra key that Object.keys/ownKeys on the instance itself would never see
  if (proto !== Object.prototype && proto !== null) return false;
  if (!Reflect.ownKeys(value).every((k) => RENDERER_VIEW_MODEL_KEYS.includes(k))) return false; // B1/N1: exact OWN keys incl. non-enumerable/symbol, not just enumerable string keys
  const v = value as Record<string, unknown>;
  const fieldsOk = typeof v.fields === 'object' && v.fields !== null && !Array.isArray(v.fields);
  return typeof v.kind === 'string' && fieldsOk && Object.values(v.fields as Record<string, unknown>).every(
    (val) => val === null || ['string', 'number', 'boolean'].includes(typeof val),
  );
}

export interface DesignLanguageManifest {
  /**
   * The language's stable id (naming policy: `isValidLanguageId`).
   *
   * MOR-1278 (owner decision, 2026-08-04): `[data-design-language]` is the
   * canonical — and only — design-language activation mechanism. Its DOM
   * value MUST equal this registered `id`, and it is placed on the
   * semantic-vertical root: the document root that scopes the whole
   * semantic radio UI vertical (`document.documentElement` — see the
   * MOR-1070 fixture harness, `fixtures/main.ts`, and `studioline`'s own
   * stylesheet, which keys every rule off this same attribute). No
   * alternative activation path — a CSS class, a component prop, a Svelte
   * context — may be introduced, by `studioline`, by `fieldline`
   * (MOR-1074), or by the renderer wiring that consumes it (MOR-1275): the
   * whole design-language CSS half assumes a single attribute selector it
   * can scope every rule under.
   */
  readonly id: string;
  readonly displayName: string;
  readonly tokens: DesignLanguageTokens;
  readonly density: DensityClamp;
  readonly layoutCompatibility: readonly LayoutCompatibilityDeclaration[];
  readonly renderers: Partial<Record<RendererSlotName, Renderer>>; // "semantic renderer slots"
}

// ── Validation + registry. `registerDesignLanguage` does not hardcode a
// family count — any manifest passing naming policy, required token
// groups, and the capability-fork check registers, same as `studioline`/
// `fieldline` in ./declarations (MOR-977 §4.4, §4.6).

export class DesignLanguageValidationError extends Error {}
export class RendererInputError extends Error {}

/** Manufacturer inspiration must never become a capability fork (MOR-977 §4.4) — no manifest key may reference one, anywhere, including inside renderer closures (JSON.stringify's replacer walks every nested key and skips function values). */
const FORBIDDEN_MANIFEST_KEY_MARKERS = ['capability', 'capabilities', 'radiomodel', 'vendor', 'manufacturer', 'firmware'];

function findCapabilityLikeKey(manifest: DesignLanguageManifest): string | null {
  let hit: string | null = null;
  JSON.stringify(manifest, (key, value) => {
    if (!hit && FORBIDDEN_MANIFEST_KEY_MARKERS.some((m) => key.toLowerCase().includes(m))) hit = key;
    return value;
  });
  return hit;
}

/** Throws with a descriptive message if `manifest` violates the contract. */
export function validateManifest(manifest: DesignLanguageManifest): void {
  const id = manifest.id;
  const missing = REQUIRED_TOKEN_GROUPS.filter((g) => manifest.tokens[g] === undefined);
  const capabilityHit = findCapabilityLikeKey(manifest);
  const problem =
    (!isValidLanguageId(id) && `Design-language id "${id}" fails naming policy: kebab-case, no vendor/geographic marker.`) ||
    (missing.length > 0 && `Design language "${id}" is missing required token group(s): ${missing.join(', ')}.`) ||
    (!manifest.tokens.focusRing && `Design language "${id}" must declare a non-empty focusRing token (MOR-1232).`) ||
    (manifest.tokens.typography.fontVariantNumeric !== 'tabular-nums' &&
      `Design language "${id}" must declare tabular figures (font-variant-numeric: tabular-nums).`) ||
    (capabilityHit && `Design language "${id}" references a capability-shaped key "${capabilityHit}" (MOR-977 §4.4).`);
  if (problem) throw new DesignLanguageValidationError(problem);
}

const registry = new Map<string, DesignLanguageManifest>();

/** Validates then registers `manifest`. Re-registering an ID overwrites it. */
export function registerDesignLanguage(manifest: DesignLanguageManifest): void {
  validateManifest(manifest);
  registry.set(manifest.id, manifest);
}
export function getDesignLanguage(id: string): DesignLanguageManifest | undefined {
  return registry.get(id);
}
export function listDesignLanguageIds(): readonly string[] {
  return Array.from(registry.keys());
}

/** Safe no-op used when a language has not registered a renderer for a slot yet. */
const FALLBACK_RENDERER: Renderer = () => null;

/**
 * Missing renderers fall back safely — a language may declare zero
 * renderers before its visual slice lands (MOR-1073/1074). Returns a
 * *gated* wrapper, not the raw renderer/fallback: calling the returned
 * function always goes through `invokeRenderer`, so the structural check
 * cannot be bypassed by calling the resolve-path result directly
 * (review cycle 1, B1).
 */
export function resolveRenderer(manifest: DesignLanguageManifest, slot: RendererSlotName): Renderer {
  const renderer = manifest.renderers[slot] ?? FALLBACK_RENDERER;
  return (viewModel, tokens) => invokeRenderer(renderer, viewModel, tokens);
}

/** Structural gate: only a RendererViewModel (exact keys) may reach a renderer. */
export function invokeRenderer(renderer: Renderer, viewModel: unknown, tokens: DesignLanguageTokens): unknown {
  if (!isRendererViewModel(viewModel)) {
    throw new RendererInputError(
      'Renderer input must be a RendererViewModel (exactly {kind, fields}, fields flat-primitive-only); ' +
        'capability objects and per-radio data structurally cannot reach a renderer.',
    );
  }
  return renderer(viewModel as RendererViewModel, tokens);
}
