/**
 * MOR-2054 — repo-side twin of `layout-compatibility-guard.test.ts`'s dev
 * warning: that file protects a THIRD-PARTY author who ships a manifest
 * declaring no layout compatibility; this file protects THIS repository by
 * failing the suite outright if any manifest actually shipped here does.
 *
 * Mirrors `../../layouts/__tests__/loader-identity-inventory.test.ts`'s
 * completeness-check discipline specifically: like that file's own
 * `BARREL_MANIFESTS` derivation, the manifest list here is derived
 * structurally from the barrel's own export surface, not hand-listed —
 * hand-listing manifest ids here would be the exact defect MOR-2054 exists
 * to remove (a list that silently stops matching reality the moment someone
 * adds a manifest and forgets to update the list). That file's separate
 * `ALL_MANIFESTS`/`EXPECTED_LOADER_SPECIFIER` tables are, by contrast,
 * hand-listed literals by design (that file's own comment says so) — used
 * only for per-id loader-specifier pinning, a check this file has no need
 * for.
 *
 * Deliberately NOT sourced from `listDesignLanguageIds()`/the live registry:
 * `contract.ts`'s registry is module-scope, private, mutable state that
 * sibling test files write throwaway entries into via `registerDesignLanguage`
 * (e.g. `registry.test.ts`'s "no hardcoded family count" case registers a
 * `thirdline` id built from `fixtures.ts`'s `validManifest()`, whose default
 * `layoutCompatibility` is `[]`). Measured 2026-08-31: the shared layout
 * registry returns extra ids under `isolate: false` when sibling suites
 * register probe manifests — the same class of shared cross-file state this
 * file's own registry is exposed to. Probed directly for THIS file before
 * writing it (running this directory, and separately the whole
 * `src/presentation/` tree, under `--no-file-parallelism` with
 * `listDesignLanguageIds()`): this specific registry did not currently show
 * `thirdline` leaking across files in either run, so the risk is not
 * currently observed here — but relying on that would make correctness
 * depend on vitest's module-sharing behavior under `isolate: false` never
 * changing and no sibling file ever registering a colliding id, neither of
 * which this file can pin. The barrel's own export surface depends on none
 * of that, costs nothing extra to use instead, and needs no
 * `*.isolated.test.ts` escape hatch (unlike `loader-identity-inventory.test.ts`,
 * which additionally proves REGISTRATION identity via `getLayout(id)` — a
 * registry read this file has no need for, since
 * `declaresNoLayoutCompatibility` is a pure structural check on the manifest
 * object itself).
 */
import { describe, expect, it } from 'vitest';
import { declaresNoLayoutCompatibility } from '../layout-compatibility-guard';
import type { DesignLanguageManifest } from '../contract';
// Namespace import of the declarations barrel, used ONLY to derive the
// manifest list structurally — never to register anything beyond what
// importing the barrel already does as a side effect (`registerDesignLanguage`
// calls at its own module scope).
import * as languageDeclarationsBarrel from '../declarations';

/** Structural `DesignLanguageManifest` guard for filtering the barrel's
 *  export surface — every export the barrel currently has IS a manifest,
 *  but this keeps the derivation honest against a future non-manifest
 *  export (a helper constant, a re-exported type) landing in the same file
 *  without silently corrupting the set below. */
function isDesignLanguageManifest(value: unknown): value is DesignLanguageManifest {
  return (
    typeof value === 'object' && value !== null &&
    typeof (value as { id?: unknown }).id === 'string' &&
    typeof (value as { displayName?: unknown }).displayName === 'string' &&
    Array.isArray((value as { layoutCompatibility?: unknown }).layoutCompatibility) &&
    typeof (value as { renderers?: unknown }).renderers === 'object'
  );
}

/** Every manifest the barrel exports, derived structurally rather than
 *  hand-listed (see module doc). */
const SHIPPED_MANIFESTS: readonly DesignLanguageManifest[] =
  Object.values(languageDeclarationsBarrel).filter(isDesignLanguageManifest);

describe('every shipped design-language manifest declares at least one compatible layout (MOR-2054)', () => {
  // Guards the derivation itself: if the barrel filter ever matched nothing
  // (a broken import, an over-strict guard), every test below would pass
  // vacuously — this is the check that stays honest about that.
  it('finds at least one manifest to check', () => {
    expect(SHIPPED_MANIFESTS.length).toBeGreaterThan(0);
  });

  it.each(SHIPPED_MANIFESTS.map((manifest) => [manifest.id, manifest] as const))(
    '"%s" declares at least one layoutCompatibility entry with compatible: true',
    (_id, manifest) => {
      // Kills: a shipped manifest whose layoutCompatibility is empty, or
      // holds only compatible: false entries — either way
      // designLanguageActivation can never return this manifest's id, for
      // any layout, silently. See layout-compatibility-guard.ts for the
      // full empty-vs-no-true reasoning this predicate encodes.
      expect(declaresNoLayoutCompatibility(manifest)).toBe(false);
    },
  );
});
