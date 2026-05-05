/**
 * One-shot migration of v1.x icom-lan localStorage keys to v2.x rigplane keys.
 *
 * **Boot-order critical.** Several stores read localStorage at module init
 * (e.g. `layout.svelte.ts`, `theme-switcher.ts`, `lcd-contrast.svelte.ts`,
 * `lcd-display-mode.svelte.ts`). Per ES module spec, all transitive imports
 * of `main.ts` evaluate BEFORE main.ts's body runs — which means a function
 * call from main.ts's body would race the stores' reads.
 *
 * The fix is to run migration as a **top-level side-effect of THIS module**
 * (see the bottom of the file). Then `main.ts` only needs to import this
 * file *first*, before any other import: source-order of sibling imports is
 * preserved during module evaluation, so this module's body (including the
 * auto-call) runs before `App.svelte` and its store dependencies resolve.
 *
 * Idempotent — running twice is safe. Once the sentinel is written the
 * migration short-circuits.
 *
 * Removes the legacy keys after copying so they don't drift over time.
 */

const LEGACY_TO_NEW: Record<string, string> = {
  // Hyphenated (legacy v1) — auth token, layout, LCD modes, hidden layers,
  // skin selection. Some keys (`icom-lan-skin`, `icom-lan-lcd-variant`,
  // `icom-lan-skin-migrated-0.18`) are not currently produced by this
  // codebase, but v1.x users may still have them in browser storage.
  'icom-lan-auth-token': 'rigplane-auth-token',
  'icom-lan-layout': 'rigplane-layout',
  'icom-lan-lcd-display-mode': 'rigplane-lcd-display-mode',
  'icom-lan-hidden-layers': 'rigplane-hidden-layers',
  'icom-lan-lcd-variant': 'rigplane-lcd-variant',
  'icom-lan-skin': 'rigplane-skin',
  'icom-lan-skin-migrated-0.18': 'rigplane-skin-migrated-0.18',
  // Colon-namespaced (newer v1) — panels, install prompt, theme, memory,
  // contrast, dock layout. Note the dock-layout key carries a `:v1` suffix.
  'icom-lan:panel-collapsed': 'rigplane:panel-collapsed',
  'icom-lan:panel-order': 'rigplane:panel-order',
  'icom-lan:right-panel-order': 'rigplane:right-panel-order',
  'icom-lan:install-dismissed': 'rigplane:install-dismissed',
  'icom-lan:memory-channels': 'rigplane:memory-channels',
  'icom-lan:theme': 'rigplane:theme',
  'icom-lan:theme-user-choice': 'rigplane:theme-user-choice',
  'icom-lan:vfo-theme': 'rigplane:vfo-theme',
  'icom-lan:lcd-contrast': 'rigplane:lcd-contrast',
  'icom-lan:local-extension-dock-layout:v1': 'rigplane:local-extension-dock-layout:v1',
  // Dot-namespaced (tuning store) — produced by `frontend/src/lib/stores/tuning.svelte.ts`
  // up to v1.x. Renamed to `rigplane.tuning-step-*` in v2.
  'icom-lan.tuning-step-hz': 'rigplane.tuning-step-hz',
  'icom-lan.tuning-step-auto': 'rigplane.tuning-step-auto',
};

const MIGRATION_SENTINEL_KEY = 'rigplane:storage-migrated-from-icom-lan';

let migrationDone = false;

/**
 * Test-only: reset the in-process flag so a unit test can re-run the
 * migration after manipulating localStorage. Production code must never
 * call this.
 */
export function __resetMigrationStateForTests(): void {
  migrationDone = false;
}

export function migrateLegacyStorage(): void {
  if (migrationDone) return;
  migrationDone = true;

  // SSR / non-browser bail-out.
  if (typeof localStorage === 'undefined') return;

  try {
    if (localStorage.getItem(MIGRATION_SENTINEL_KEY) === '1') return;
  } catch {
    // localStorage may throw in some sandboxed contexts; abort gracefully.
    return;
  }

  for (const [oldKey, newKey] of Object.entries(LEGACY_TO_NEW)) {
    try {
      if (localStorage.getItem(newKey) !== null) {
        // New key already populated — don't overwrite. Just remove the legacy
        // copy so it doesn't drift.
        localStorage.removeItem(oldKey);
        continue;
      }
      const value = localStorage.getItem(oldKey);
      if (value === null) continue;
      localStorage.setItem(newKey, value);
      localStorage.removeItem(oldKey);
    } catch {
      // Per-key failures are swallowed; migration is best-effort.
    }
  }

  try {
    localStorage.setItem(MIGRATION_SENTINEL_KEY, '1');
  } catch {
    // Ignore: next boot will retry — keys not yet migrated will be picked up,
    // already-migrated keys are no-ops.
  }
}

// Auto-run on first import. This is what makes the migration race-free: the
// stores that read localStorage at module init are *transitive imports* of
// `main.ts`, and the migration module is the FIRST import of `main.ts`. ES
// module evaluation is in source order for sibling imports, so this body
// runs before the App / stores are evaluated.
//
// Idempotent via `migrationDone`. Tests call `__resetMigrationStateForTests`
// in `beforeEach` to reset and then explicitly invoke `migrateLegacyStorage`.
migrateLegacyStorage();
