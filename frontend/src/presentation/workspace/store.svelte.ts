/**
 * MOR-1079 — the single workspace store.
 *
 * Svelte 5 runes over the pure `repository.ts` layer, in the module-level
 * `$state` + exported accessors idiom the `lib/stores/*` modules already use.
 * Module load is PURE (MOR-1077 idiom): storage is touched only by an explicit
 * `initWorkspaceStore()` call, never at import.
 *
 * The notice is the "visible discard" signal the owner asked for: when a
 * stored object is discarded on version mismatch, or repaired, or could not be
 * written, that fact is published reactively instead of being swallowed. The
 * future settings UI (MOR-1080) reads `getWorkspaceNotice()` from a `$derived`
 * and calls `dismissWorkspaceNotice()`.
 *
 * Updates are semantic and typed. There is deliberately no raw-object setter:
 * every mutation goes through `applyWorkspacePatch`, i.e. through
 * `readWorkspace`, so an invalid value can never reach state or storage.
 */
import { DEFAULT_WORKSPACE, readWorkspace } from './contract';
import {
  applyWorkspacePatch, loadWorkspace, markWorkspaceMigrated, persistWorkspace,
  type WorkspaceStorage,
} from './repository';
import type {
  WorkspaceDesignLanguageId, WorkspaceLayoutId, WorkspaceReadResult, WorkspaceRejection,
  WorkspaceThemeId, WorkspaceV1, WorkspaceZoneId,
} from './contract';
import type { SemanticSurfaceName } from '../layouts/contract';
import type { DensityLevel } from '../languages/contract';

export type WorkspaceNoticeKind =
  /** A stored object outside the readable version window was NOT recovered. */
  | 'version-discarded'
  /** Stored bytes were unusable (not an object / bad JSON) and reset to defaults. */
  | 'reset'
  /** Recovered, but individual fields fell back — the operator lost those. */
  | 'repaired'
  /** A newer stored object this build cannot fully represent; writes are refused. */
  | 'forward-read-only'
  /** The last write failed (quota, private mode); the previous stored state stands. */
  | 'persist-failed';

export interface WorkspaceNotice {
  readonly kind: WorkspaceNoticeKind;
  readonly discardedVersion?: unknown;
  readonly rejections: readonly WorkspaceRejection[];
}

const INITIAL: WorkspaceReadResult = readWorkspace(DEFAULT_WORKSPACE);

let current = $state<WorkspaceReadResult>(INITIAL);
let notice = $state<WorkspaceNotice | null>(null);
let backing: WorkspaceStorage | null = null;
/** Set at init when the load was NOT writable (see `WorkspaceLoad.writable`),
 *  and never re-derived per update: once the unrepresentable value has been
 *  repaired away a patched result validates cleanly, so re-deriving would
 *  re-enable the write and destroy the newer data. */
let blocked: WorkspaceNotice | null = null;
/**
 * MOR-1081 — the theme-explicitness latch, the same shape as `blocked` above:
 * a verdict taken from the LOAD and then carried for the session.
 *
 * `WorkspaceV1.theme` is total — it always resolves to something — so the
 * validated value cannot say whether the operator ever chose one. v2 answered
 * that with a second key (`rigplane:theme-user-choice`); adoption answers it
 * with the PRESENCE of the `theme` field in the stored object, which
 * `repository.ts` reports on load and preserves on write. Read it through
 * `isThemeExplicit()`. `$state` so a `$derived` consumer re-runs on a pick.
 */
let themeChosen = $state(false);

function defaultStorage(): WorkspaceStorage | null {
  try {
    return typeof localStorage === 'undefined' ? null : localStorage;
  } catch {
    return null;
  }
}

function noticeFor(result: WorkspaceReadResult): WorkspaceNotice | null {
  if (result.outcome === 'version-discarded') {
    return { kind: 'version-discarded', discardedVersion: result.discardedVersion, rejections: result.rejections };
  }
  if (result.outcome === 'reset') return { kind: 'reset', rejections: result.rejections };
  if (result.outcome === 'forward-read' && result.rejections.length > 0) {
    return { kind: 'forward-read-only', rejections: result.rejections };
  }
  if (result.rejections.length > 0) return { kind: 'repaired', rejections: result.rejections };
  return null;
}

/**
 * Explicit, idempotent boot step. Migration is decided by the repository's
 * sentinel, so calling this twice re-reads the stored object and never
 * re-applies legacy data.
 */
export function initWorkspaceStore(storage: WorkspaceStorage | null = defaultStorage()): void {
  backing = storage;
  blocked = null;
  themeChosen = false;
  if (storage === null) {
    current = INITIAL;
    notice = null;
    return;
  }
  const load = loadWorkspace(storage);
  current = load.result;
  notice = noticeFor(load.result);
  blocked = load.writable ? null : notice;
  themeChosen = load.themeChosen;
  if (load.source === 'migrated' && persistWorkspace(storage, load.result, themeChosen)) {
    markWorkspaceMigrated(storage);
  }
}

export function getWorkspace(): WorkspaceV1 {
  return current.workspace;
}

/** The discard/repair/write-failure signal. Reactive: read it from `$derived`. */
export function getWorkspaceNotice(): WorkspaceNotice | null {
  return notice;
}

export function dismissWorkspaceNotice(): void {
  notice = null;
}

/**
 * MOR-1081 — has the operator a theme of their OWN, as opposed to whatever the
 * schema default resolved to? The signal a skin needs before it may apply its
 * own default (amber-lcd → lcd-warm) without stomping a real choice, and the
 * successor to v2's `rigplane:theme-user-choice` key presence.
 *
 * Explicitly picking `default` counts: `setTheme(id, true)` latches it and the
 * field is then persisted, so Default Dark stays Default Dark across a reload.
 */
export function isThemeExplicit(): boolean {
  return themeChosen;
}

/**
 * `unlatch` = an explicit whole-object replacement (reset/import), which
 * carries no unrepresentable remainder for `blocked` to protect — so it
 * clears the latch instead of being silently swallowed by it, and clears
 * a stale notice rather than leaving it to contradict the fresh result.
 */
function commit(next: WorkspaceReadResult, unlatch = false): void {
  if (unlatch) {
    blocked = null;
    notice = null;
  }
  current = next;
  const published = noticeFor(next);
  if (published !== null) notice = published;
  if (backing === null) return;
  if (blocked !== null) {
    notice = blocked;
    return;
  }
  if (!persistWorkspace(backing, next, themeChosen)) {
    notice = published ?? { kind: 'persist-failed', rejections: next.rejections };
  }
}

function update(patch: Readonly<Record<string, unknown>>): void {
  commit(applyWorkspacePatch(current, patch));
}

/**
 * MOR-1080 — the safe override binding carry-forward 1 asks for: a latched
 * forward-read-only notice can only be escaped by discarding down to the
 * frozen defaults, never by a partial merge that could still be lossy.
 * Latches `themeChosen` (the reset IS the operator's choice, not a re-apply
 * — mirrors `setTheme(id, true)`). Returns the pre-reset snapshot so the
 * caller can offer an in-session undo through the existing typed setters —
 * no new persistence path.
 */
export function resetWorkspace(): WorkspaceV1 {
  const previous = current.workspace;
  themeChosen = true;
  commit(readWorkspace(DEFAULT_WORKSPACE), true);
  return previous;
}

export function setLayout(layout: WorkspaceLayoutId): void {
  update({ layout });
}

export function setDesignLanguage(designLanguage: WorkspaceDesignLanguageId): void {
  update({ designLanguage });
}

/**
 * `explicit` = this came from the operator picking a theme, not from a host
 * re-applying the one already selected. It latches `isThemeExplicit()` and is
 * what makes the field persist — mirroring v2's `setTheme` /
 * `setThemeUserChoice` split onto one field plus one latch.
 */
export function setTheme(theme: WorkspaceThemeId, explicit = false): void {
  if (explicit) themeChosen = true;
  update({ theme });
}

export function setDensity(density: DensityLevel): void {
  update({ density });
}

export function setZoneVisibleSurfaces(zone: WorkspaceZoneId, surfaces: readonly SemanticSurfaceName[]): void {
  update({ visibleSurfaces: { ...current.workspace.visibleSurfaces, [zone]: surfaces } });
}

export function setZoneOrder(zone: WorkspaceZoneId, surfaces: readonly SemanticSurfaceName[]): void {
  update({ zoneOrder: { ...current.workspace.zoneOrder, [zone]: surfaces } });
}

export function setPinnedCommands(pinnedCommands: readonly string[]): void {
  update({ pinnedCommands });
}
