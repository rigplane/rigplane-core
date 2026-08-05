/**
 * MOR-1079 — the workspace persistence boundary, as pure functions.
 *
 * Storage contact is confined to this module and every read is routed through
 * MOR-1077's `readWorkspace` / MOR-1078's `readLegacyWorkspace` — there is no
 * path here that constructs a `WorkspaceV1` any other way. The Svelte-facing
 * store (`store.svelte.ts`) owns reactivity and the semantic actions; this
 * file owns the bytes.
 *
 * Three rules the tests pin:
 *  - ONE key. `persistWorkspace` serializes first and then does a single
 *    `setItem`, so a throwing storage (quota) leaves the previous stored
 *    value byte-for-byte intact — there is no partial multi-key write.
 *  - Legacy keys are NEVER written or deleted. The type is
 *    `getItem`+`setItem` only, and the migration reads legacy data through
 *    `readLegacyWorkspaceFromStorage`, whose own parameter type is
 *    `getItem`-only. The rollback window stays readable.
 *  - Migration runs ONCE, decided by a versioned sentinel key rather than by
 *    "is the workspace key empty" — clearing the workspace key must not
 *    resurrect a legacy layout the operator has since changed.
 */
import {
  DEFAULT_WORKSPACE, WORKSPACE_SCHEMA_VERSION,
  readWorkspace, readWorkspaceJson, serializeWorkspace,
  type WorkspaceReadResult,
} from './contract';
import { buildWorkspaceInput, readLegacyWorkspace, snapshotLegacyStorage } from './legacy-readers';

/** Unversioned on purpose: the schema version lives INSIDE the object, so a
 *  newer build writes its own version to this same key and an older build can
 *  still forward-read it (contract N=2 window). */
export const WORKSPACE_STORAGE_KEY = 'rigplane:workspace';
/** Versioned on purpose: it records WHICH migration generation already ran. */
export const WORKSPACE_MIGRATION_SENTINEL_KEY = 'rigplane:workspace-migrated:v1';

/** No `removeItem`/`clear`: deleting anything would not type-check. */
export type WorkspaceStorage = Pick<Storage, 'getItem' | 'setItem'>;

/** `stored` = the workspace key was present. `migrated` = first run, legacy data
 *  folded in. `absent` = already migrated, nothing stored (a cleared key, not a
 *  discard — the store must not raise a "settings lost" notice for it). */
export type WorkspaceLoadSource = 'stored' | 'migrated' | 'absent';
export interface WorkspaceLoad {
  readonly result: WorkspaceReadResult;
  readonly source: WorkspaceLoadSource;
  /** `false` = a newer stored object this build could not fully represent. The
   *  verdict belongs to the LOAD and must be latched for the session: once the
   *  unrepresentable value has been repaired away, a patched result validates
   *  cleanly and would silently overwrite the newer data. */
  readonly writable: boolean;
  /**
   * MOR-1081 — explicitness-via-presence. `true` when the loaded INPUT actually
   * carried a `theme` key, i.e. the operator has a theme of their own; `false`
   * when it did not, i.e. they never chose and a skin's own default applies.
   *
   * It has to be read off the input: `readWorkspace` folds an absent `theme`
   * to the schema default WITHOUT a rejection, so `'default'` in the validated
   * result is ambiguous between "explicitly picked Default Dark" and "never
   * chose" — exactly the conflation v2's separate `rigplane:theme-user-choice`
   * key existed to prevent. `persistWorkspace` keeps the distinction alive by
   * omitting the field again when it was never chosen, so the answer survives
   * a reload with no schema change.
   */
  readonly themeChosen: boolean;
}

const EMPTY_WORKSPACE_JSON = JSON.stringify({ version: WORKSPACE_SCHEMA_VERSION });

function readKey(storage: WorkspaceStorage, key: string): string | null {
  try {
    return storage.getItem(key);
  } catch {
    return null;
  }
}

/** Did the stored text carry a `theme` key at all? Never throws. */
function storedThemeChosen(text: string): boolean {
  try {
    const raw: unknown = JSON.parse(text);
    return typeof raw === 'object' && raw !== null && !Array.isArray(raw) && 'theme' in raw;
  } catch {
    return false;
  }
}

function load(
  result: WorkspaceReadResult, source: WorkspaceLoadSource, themeChosen: boolean,
): WorkspaceLoad {
  return { result, source, writable: canPersistWorkspace(result), themeChosen };
}

/** Total: every storage state yields a validated result, nothing throws. */
export function loadWorkspace(storage: WorkspaceStorage): WorkspaceLoad {
  const stored = readKey(storage, WORKSPACE_STORAGE_KEY);
  if (stored !== null) {
    return load(readWorkspaceJson(stored), 'stored', storedThemeChosen(stored));
  }
  if (readKey(storage, WORKSPACE_MIGRATION_SENTINEL_KEY) !== null) {
    // A cleared key is "back to defaults", never "resurrect a legacy choice".
    return load(readWorkspaceJson(EMPTY_WORKSPACE_JSON), 'absent', false);
  }
  // First run. `buildWorkspaceInput` sets `theme` only when a legacy theme key
  // existed, so its presence IS the migrated operator's explicitness.
  const snapshot = snapshotLegacyStorage(storage);
  return load(readLegacyWorkspace(snapshot), 'migrated', 'theme' in buildWorkspaceInput(snapshot));
}

/**
 * Forward-write policy (MOR-1076 N=2). `contract.ts` keeps `version` as read
 * and preserves unknown top-level fields verbatim, so writing a forward-read
 * object back is explicitly supported and does NOT downgrade it. What the
 * contract canNOT preserve is an unknown VALUE in a KNOWN field (a v2 theme
 * id repairs to `default`), and those rejections are exactly what a lossy
 * forward read reports. So: write back an un-downgraded forward read when it
 * was lossless, and refuse to write at all when it was not — an older build
 * never overwrites newer data it could not fully represent.
 */
export function canPersistWorkspace(result: WorkspaceReadResult): boolean {
  return result.outcome !== 'forward-read' || result.rejections.length === 0;
}

/** Atomic: serialize fully, then one `setItem`. `false` = nothing was written.
 *  The `canPersistWorkspace` re-check below is belt-and-braces for a direct
 *  caller — it is NOT the protection. Re-derived from a PATCHED result it
 *  always says "writable", because the unrepresentable value has been repaired
 *  away by then; the real gate is the `blocked` latch in `store.svelte.ts`,
 *  which holds the verdict from the LOAD. */
export function persistWorkspace(
  storage: WorkspaceStorage,
  result: WorkspaceReadResult,
  themeChosen = true,
): boolean {
  if (!canPersistWorkspace(result)) return false;
  let payload: string;
  try {
    const object = serializeWorkspace(result);
    // Explicitness-via-presence (MOR-1081). The field is omitted in exactly one
    // case: it was never chosen AND it carries nothing beyond the schema
    // default, i.e. it holds no information at all. Writing it anyway would
    // make the next load indistinguishable from an explicit Default Dark, and
    // a skin's own default (amber-lcd → lcd-warm) would be silently
    // unreachable forever. Any non-default value is still persisted, chosen or
    // not, so nothing can be lost. Omitting is not a schema change:
    // `readWorkspace` already folds an absent `theme` to the default without a
    // rejection.
    if (!themeChosen && object.theme === DEFAULT_WORKSPACE.theme) delete object.theme;
    payload = JSON.stringify(object);
  } catch {
    return false;
  }
  try {
    storage.setItem(WORKSPACE_STORAGE_KEY, payload);
    return true;
  } catch {
    return false;
  }
}

/** Written only AFTER a successful workspace write, so a failed migration retries. */
export function markWorkspaceMigrated(storage: WorkspaceStorage): boolean {
  try {
    storage.setItem(WORKSPACE_MIGRATION_SENTINEL_KEY, String(WORKSPACE_SCHEMA_VERSION));
    return true;
  } catch {
    return false;
  }
}

/** The only mutation path: re-validate the whole object, never mutate in place.
 *  Unknown preserved fields survive because `serializeWorkspace` re-emits them. */
export function applyWorkspacePatch(
  current: WorkspaceReadResult,
  patch: Readonly<Record<string, unknown>>,
): WorkspaceReadResult {
  return readWorkspace({ ...serializeWorkspace(current), ...patch });
}
