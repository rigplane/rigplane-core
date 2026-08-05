/**
 * Workspace v1 schema, validator, defaults and safe fallback (MOR-1077) —
 * the code form of the MOR-1076 owner freeze (2026-08-04). Pure and
 * store-free: nothing here reads or writes storage, mounts UI, or resolves a
 * component. Persistence + settings UI are MOR-1079's boundary; adopting
 * this object as the layout source of truth is MOR-1081's.
 *
 * ID sources. `SEMANTIC_SURFACE_NAMES` is imported LIVE from the layout
 * contract — a pure module. The other id spaces are PINNED literals with a
 * registry-sync test (`__tests__/contract.test.ts`), because every module
 * that owns them is unusable from here: `lib/stores/layout.svelte.ts` reads
 * `localStorage` at module scope and is `$lib/stores/*` (lint-banned in this
 * zone), the two `declarations.ts` barrels fire `registerLayout`/
 * `registerDesignLanguage` as import side effects, and
 * `components-v2/theme/theme-switcher.ts` is banned by the workspace zone
 * (v3 ADR invariant 6). The sync test imports all three and fails on drift.
 */
import { SEMANTIC_SURFACE_NAMES, type SemanticSurfaceName } from '../layouts/contract';
import type { DensityLevel } from '../languages/contract';

export const WORKSPACE_SCHEMA_VERSION = 1;
/** MOR-1076: an app up to 2 minors older must still READ a newer object. */
export const WORKSPACE_FORWARD_READ_WINDOW = 2;

/** Decision 1: `CanonicalLayoutMode` including `auto`, MOR-1042 aliases applied on read. */
export const WORKSPACE_LAYOUT_IDS = ['auto', 'lcd-cockpit', 'lcd-scope', 'standard', 'sdr-test'] as const;
export type WorkspaceLayoutId = (typeof WORKSPACE_LAYOUT_IDS)[number];
const LAYOUT_ALIASES: Readonly<Record<string, WorkspaceLayoutId>> = { lcd: 'lcd-cockpit', 'amber-lcd': 'lcd-cockpit', spectrum: 'standard', 'desktop-v2': 'standard' };
/** Workspace id space → layout-manifest id space. `auto` is resolved by the existing
 *  `skins/registry.ts::resolveSkinId()` (it needs live scope facts this module must not
 *  see), so it maps to null — "defer", not "unknown". */
const LAYOUT_MANIFEST_ID: Readonly<Record<WorkspaceLayoutId, string | null>> = { auto: null, 'lcd-cockpit': 'lcd-cockpit', 'lcd-scope': 'lcd-scope', standard: 'desktop-v2', 'sdr-test': 'sdr-test' };

/** Decision 2: frozen by MOR-977 §4.6. */
export const WORKSPACE_DESIGN_LANGUAGE_IDS = ['studioline', 'fieldline'] as const;
export type WorkspaceDesignLanguageId = (typeof WORKSPACE_DESIGN_LANGUAGE_IDS)[number];
/** Decision 4: each language's `DensityClamp.supported`; index 0 is that language's default. */
export const WORKSPACE_DENSITY_CLAMP: Readonly<Record<WorkspaceDesignLanguageId, readonly DensityLevel[]>> = {
  studioline: ['comfortable', 'compact', 'dense'],
  fieldline: ['comfortable', 'compact'],
};

/** Decision 3: the flat 21-id theme list, allow-list validated ON READ. */
export const WORKSPACE_THEME_IDS = [
  'default', 'dracula', 'nord', 'catppuccin-mocha', 'solarized-dark', 'gruvbox-dark', 'tokyo-night',
  'one-dark', 'ayu-dark', 'amoled-black', 'high-contrast', 'solarized-light', 'catppuccin-latte',
  'nord-light', 'gruvbox-light', 'github-light', 'custom-ic7610', 'nixie-tube', 'lcd-blue', 'lcd-warm', 'crt-green',
] as const;
export type WorkspaceThemeId = (typeof WORKSPACE_THEME_IDS)[number];

/** Every zone id declared by a registered layout manifest (decisions 5 and 6). */
export const WORKSPACE_ZONE_IDS = ['main', 'receiver-deck', 'rx-tx', 'primary-vfo', 'secondary-vfo', 'global', 'portrait-deck', 'control-column'] as const;
export type WorkspaceZoneId = (typeof WORKSPACE_ZONE_IDS)[number];

/** Decision 7: command-bus intent names (`set_compressor`), never module paths. */
const PINNED_COMMAND_PATTERN = /^[a-z][a-z0-9]*(_[a-z0-9]+)*$/;

type ZoneSurfaceMap = Readonly<Partial<Record<WorkspaceZoneId, readonly SemanticSurfaceName[]>>>;

export interface WorkspaceV1 {
  /** Kept as read, so a forward-read object is written back un-downgraded. */
  readonly version: number;
  readonly layout: WorkspaceLayoutId;
  readonly designLanguage: WorkspaceDesignLanguageId;
  readonly theme: WorkspaceThemeId;
  readonly density: DensityLevel;
  /** Decision 5: ids of the surfaces that are VISIBLE in a zone (allow-list). */
  readonly visibleSurfaces: ZoneSurfaceMap;
  /** Decision 6: per-zone order. A surface may appear in at most one zone — the shape
   *  carries no move operation and a duplicate across zones is rejected, so cross-zone
   *  moves are structurally out of v1. */
  readonly zoneOrder: ZoneSurfaceMap;
  readonly pinnedCommands: readonly string[];
}

export const DEFAULT_WORKSPACE: WorkspaceV1 = {
  version: WORKSPACE_SCHEMA_VERSION, layout: 'auto', designLanguage: 'studioline', theme: 'default',
  density: 'comfortable', visibleSurfaces: {}, zoneOrder: {}, pinnedCommands: [],
};

/** The classes MOR-1076 forbids outright. A hit is refused with this reason, never persisted. */
export type WorkspaceForbiddenClass =
  | 'capabilities' | 'runtime-state' | 'manufacturer-policy'
  | 'component-module-path' | 'transport-session' | 'tx-resource-safety';
export type WorkspaceRejectionReason = WorkspaceForbiddenClass | 'unknown-id' | 'out-of-clamp' | 'cross-zone' | 'malformed';
export interface WorkspaceRejection { readonly field: string; readonly reason: WorkspaceRejectionReason }

/** Matched against a lowercased, separator-stripped key (`tx_power` → `txpower`). */
const FORBIDDEN_KEY_MARKERS: readonly (readonly [WorkspaceForbiddenClass, RegExp])[] = [
  ['capabilities', /capabilit/],
  ['transport-session', /transport|session|token|auth|credential|websocket|socket|endpoint/],
  ['tx-resource-safety', /^tx|ptt|interlock|safety|inhibit|powerlimit|resource|lockout/],
  ['runtime-state', /^freq|smeter|swr|runtimestate|radiostate|livestate|rawstate/],
  ['manufacturer-policy', /icom|yaesu|kenwood|elecraft|xiegu|alinco|vendor|manufacturer|radiomodel|firmware/],
];
/** The forms a component specifier takes here (mirrors the layout contract's value scan). */
const MODULE_PATH_VALUE = /^(\.{1,2}\/|\$lib\/|src\/|\/)|\.(svelte|ts|js)$/;

function forbiddenClassOf(key: string): WorkspaceForbiddenClass | null {
  const k = key.toLowerCase().replace(/[^a-z0-9]/g, '');
  return FORBIDDEN_KEY_MARKERS.find(([, re]) => re.test(k))?.[0] ?? null;
}

/** Deep scan of one unknown field's subtree — preserving it verbatim would persist it. */
function scanForbidden(value: unknown, key: string): WorkspaceForbiddenClass | null {
  const keyHit = key ? forbiddenClassOf(key) : null;
  if (keyHit) return keyHit;
  if (typeof value === 'string') return MODULE_PATH_VALUE.test(value) ? 'component-module-path' : null;
  if (Array.isArray(value)) return value.map((v) => scanForbidden(v, '')).find(Boolean) ?? null;
  if (typeof value === 'object' && value !== null) return Object.entries(value).map(([k, v]) => scanForbidden(v, k)).find(Boolean) ?? null;
  return null;
}

function pickId<T extends string>(value: unknown, allowed: readonly T[], fallback: T, field: string, out: WorkspaceRejection[]): T {
  if (typeof value === 'string' && (allowed as readonly string[]).includes(value)) return value as T;
  if (value !== undefined) out.push({ field, reason: 'unknown-id' });
  return fallback;
}

/** MOR-1042 alias normalization; anything unrecognized falls to `auto`, never throws. */
export function normalizeWorkspaceLayoutId(value: unknown): WorkspaceLayoutId {
  if (typeof value !== 'string') return 'auto';
  if (Object.hasOwn(LAYOUT_ALIASES, value)) return LAYOUT_ALIASES[value];
  return (WORKSPACE_LAYOUT_IDS as readonly string[]).includes(value) ? (value as WorkspaceLayoutId) : 'auto';
}

/** Layout-manifest id for a workspace layout id; `null` = defer to `resolveSkinId()`. */
export function workspaceLayoutManifestId(id: WorkspaceLayoutId): string | null {
  return LAYOUT_MANIFEST_ID[id];
}

/** Decision 4 resolution point: the override is honoured only inside the ACTIVE language's clamp. */
function pickDensity(value: unknown, language: WorkspaceDesignLanguageId, out: WorkspaceRejection[]): DensityLevel {
  const clamp = WORKSPACE_DENSITY_CLAMP[language];
  if (typeof value === 'string' && (clamp as readonly string[]).includes(value)) return value as DensityLevel;
  if (value !== undefined) {
    const known = (WORKSPACE_DENSITY_CLAMP.studioline as readonly string[]).includes(value as string);
    out.push({ field: 'density', reason: known ? 'out-of-clamp' : 'unknown-id' });
  }
  return clamp[0];
}

function pickZoneMap(value: unknown, field: string, out: WorkspaceRejection[]): ZoneSurfaceMap {
  const result: Partial<Record<WorkspaceZoneId, readonly SemanticSurfaceName[]>> = {};
  if (value === undefined) return result;
  if (typeof value !== 'object' || value === null || Array.isArray(value)) { out.push({ field, reason: 'malformed' }); return result; }
  const claimed = new Set<string>();
  for (const [zone, list] of Object.entries(value)) {
    if (!(WORKSPACE_ZONE_IDS as readonly string[]).includes(zone) || !Array.isArray(list)) { out.push({ field: `${field}.${zone}`, reason: 'unknown-id' }); continue; }
    const kept: SemanticSurfaceName[] = [];
    for (const surface of list) {
      if (typeof surface !== 'string' || !(SEMANTIC_SURFACE_NAMES as readonly string[]).includes(surface)) { out.push({ field: `${field}.${zone}`, reason: 'unknown-id' }); continue; }
      if (claimed.has(surface)) { out.push({ field: `${field}.${zone}`, reason: 'cross-zone' }); continue; }
      claimed.add(surface);
      kept.push(surface as SemanticSurfaceName);
    }
    result[zone as WorkspaceZoneId] = kept;
  }
  return result;
}

function pickCommands(value: unknown, out: WorkspaceRejection[]): readonly string[] {
  if (value === undefined) return [];
  if (!Array.isArray(value)) { out.push({ field: 'pinnedCommands', reason: 'malformed' }); return []; }
  const kept: string[] = [];
  for (const name of value) {
    if (typeof name !== 'string' || !PINNED_COMMAND_PATTERN.test(name)) { out.push({ field: 'pinnedCommands', reason: 'malformed' }); continue; }
    const forbidden = forbiddenClassOf(name);
    if (forbidden) { out.push({ field: 'pinnedCommands', reason: forbidden }); continue; }
    if (!kept.includes(name)) kept.push(name);
  }
  return kept;
}

/**
 * `repaired` = read, with per-field fallbacks applied. `forward-read` = a newer object
 * inside the N=2 window. `version-discarded` / `reset` are the two signals a future store
 * surfaces to the operator — the stored object was NOT recovered. Never throws, never
 * blocks boot (the `normalizeLayoutMode` doctrine).
 */
export type WorkspaceOutcome = 'ok' | 'repaired' | 'forward-read' | 'version-discarded' | 'reset';
interface WorkspaceResultBase {
  readonly workspace: WorkspaceV1;
  /** Unknown top-level fields, kept verbatim so a round trip cannot strip them. */
  readonly preserved: Readonly<Record<string, unknown>>;
  readonly rejections: readonly WorkspaceRejection[];
}
export type WorkspaceReadResult =
  | (WorkspaceResultBase & { readonly outcome: 'ok' | 'repaired' | 'forward-read' })
  | (WorkspaceResultBase & { readonly outcome: 'version-discarded'; readonly discardedVersion: unknown })
  | (WorkspaceResultBase & { readonly outcome: 'reset' });

const KNOWN_FIELDS: readonly string[] = ['version', 'layout', 'designLanguage', 'theme', 'density', 'visibleSurfaces', 'zoneOrder', 'pinnedCommands'];

function reset(field: string): WorkspaceReadResult {
  return { outcome: 'reset', workspace: DEFAULT_WORKSPACE, preserved: {}, rejections: [{ field, reason: 'malformed' }] };
}

/** Validates arbitrary input into a usable workspace. Total — every input returns a result. */
export function readWorkspace(input: unknown): WorkspaceReadResult {
  if (typeof input !== 'object' || input === null || Array.isArray(input)) return reset('');
  const raw = input as Record<string, unknown>;
  const version = raw.version;
  const readable = typeof version === 'number' && Number.isInteger(version)
    && version >= WORKSPACE_SCHEMA_VERSION && version <= WORKSPACE_SCHEMA_VERSION + WORKSPACE_FORWARD_READ_WINDOW;
  if (!readable) {
    return { outcome: 'version-discarded', discardedVersion: version, workspace: DEFAULT_WORKSPACE, preserved: {}, rejections: [{ field: 'version', reason: 'malformed' }] };
  }

  const rejections: WorkspaceRejection[] = [];
  const layout = normalizeWorkspaceLayoutId(raw.layout);
  if (raw.layout !== undefined && raw.layout !== 'auto' && layout === 'auto') rejections.push({ field: 'layout', reason: 'unknown-id' });
  const designLanguage = pickId(raw.designLanguage, WORKSPACE_DESIGN_LANGUAGE_IDS, 'studioline', 'designLanguage', rejections);

  const preserved: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(raw)) {
    if (KNOWN_FIELDS.includes(key)) continue;
    const forbidden = scanForbidden(value, key);
    if (forbidden) rejections.push({ field: key, reason: forbidden });
    else preserved[key] = value;
  }

  const workspace: WorkspaceV1 = {
    version, layout, designLanguage,
    theme: pickId(raw.theme, WORKSPACE_THEME_IDS, 'default', 'theme', rejections),
    density: pickDensity(raw.density, designLanguage, rejections),
    visibleSurfaces: pickZoneMap(raw.visibleSurfaces, 'visibleSurfaces', rejections),
    zoneOrder: pickZoneMap(raw.zoneOrder, 'zoneOrder', rejections),
    pinnedCommands: pickCommands(raw.pinnedCommands, rejections),
  };
  const outcome = version > WORKSPACE_SCHEMA_VERSION ? 'forward-read' : rejections.length > 0 ? 'repaired' : 'ok';
  return { outcome, workspace, preserved, rejections };
}

/** Whole-object JSON import (decision 9). Malformed text resets, never throws. */
export function readWorkspaceJson(text: string): WorkspaceReadResult {
  try { return readWorkspace(JSON.parse(text) as unknown); } catch { return reset(''); }
}

/** Whole-object export: the validated fields plus every preserved unknown field. */
export function serializeWorkspace(result: Pick<WorkspaceResultBase, 'workspace' | 'preserved'>): Record<string, unknown> {
  return { ...result.preserved, ...result.workspace };
}
