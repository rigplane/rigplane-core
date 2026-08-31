/**
 * Layout preference — since MOR-1081 a thin façade over the single workspace
 * store (`presentation/workspace/store.svelte.ts`), which owns the selection,
 * its validation and its persistence.
 *
 * SINGLE WRITER (MOR-1081). This module no longer reads or writes
 * `rigplane-layout` / `rigplane-skin`. The legacy keys are neither written nor
 * deleted: MOR-1079's repository froze them at their migration-time value so
 * an older build can still READ them during the rollback window, while the
 * workspace object is the only thing this build writes. Reconciliation is
 * therefore "the workspace wins, once migrated" by construction — the legacy
 * keys are simply off the selection path.
 *
 * The layout-mode vocabulary (`LayoutMode`, `CanonicalLayoutMode`,
 * `normalizeLayoutMode`) moved to `presentation/layout-mode.ts` (MOR-2059):
 * it is pure and store-free, so a skin author now imports the real thing
 * instead of editing a file `skins/**` is eslint-banned from importing. This
 * module re-exports those names — it cannot be deleted, since
 * `lib/runtime/adapters/layout-mode-adapter.ts` is itself eslint-banned from
 * importing `presentation/**` directly and reaches the vocabulary only
 * through this shim.
 */
import {
  getWorkspace,
  setLayout as setWorkspaceLayout,
} from '../../presentation/workspace/store.svelte';
import { normalizeLayoutMode, type LayoutMode } from '../../presentation/layout-mode';

export { normalizeLayoutMode, type LayoutMode, type CanonicalLayoutMode } from '../../presentation/layout-mode';

/** Reactive: the workspace store's `$state` is the backing cell. */
export function getLayoutMode(): LayoutMode {
  return getWorkspace().layout;
}

export function setLayoutMode(m: LayoutMode): void {
  setWorkspaceLayout(normalizeLayoutMode(m));
}

// useLcdLayout() removed — layout resolution now handled by skins/registry.ts resolveSkinId()
