/**
 * MOR-1267 (gap V3 from the MOR-1266 verification) — the desktop-v2 pin
 * round shipped a loader-identity pin for exactly ONE manifest
 * (`desktop-v2-registration.test.ts`'s "loader identity" describe block).
 * Every other registered manifest's own registration suite (sdr-test, both
 * LCD variants, mobile, dual-receiver-cockpit) only asserts
 * `typeof manifest.loader === 'function'` — a check a loader repointed at
 * any OTHER real, loadable skin satisfies just as well as the correct one.
 * That gap is this file: one table-driven test that pins every registered
 * manifest's loader identity, not just desktop-v2's.
 *
 * Identity mechanism: each manifest is read back out of the REAL registry
 * via `getLayout(id)` (never off the imported barrel binding directly — the
 * `getLayout(id) === manifest` check below still catches a barrel that holds
 * a stale object never actually registered). Its `loader` closure is then
 * stringified with `Function.prototype.toString()` and the dynamic import's
 * resolved specifier is pulled out with a regex. This is read, never
 * invoked — invoking a loader pulls in its skin's full import graph
 * (several transitively import `lib/stores/layout.svelte.ts`, whose
 * module-scope `localStorage` read throws outside a DOM environment; the
 * same reason every other module-specifier pin in this suite, e.g.
 * `desktop-v2-registration.test.ts`'s F8 rule, reads source as TEXT instead
 * of importing it). Stringifying the closure obtained from the live
 * registry is the stronger cousin of that idiom: it pins the specifier
 * actually wired into the object other code resolves through, not a
 * separate copy of the text found by reading a declarations file.
 *
 * The table below is a literal, not a derived list (the same discipline
 * `forward-declaration-inventory.test.ts` uses for its forward-declared
 * set): a new manifest exported from the barrel without a corresponding
 * entry here fails the completeness test, and removing an entry whose
 * manifest the barrel still exports fails it the other way. That
 * completeness check is derived from the barrel's OWN export surface
 * (`Object.values` of a namespace import, filtered by a structural
 * `LayoutManifest` guard) — deliberately not `listLayoutIds()` off the real
 * registry, whose module-scoped Map (`contract.ts`) several sibling files
 * (`registry.test.ts`, `mobile-registration.test.ts`) also register probe
 * manifests into under the fast pool's `isolate: false`, which would make an
 * absolute-registry-set assertion depend on cross-file execution order.
 * Registration itself — that a barrel-exported manifest actually reached the
 * registry under its id — is still verified, just per-id (`getLayout(id)`
 * below), matching `registry.test.ts:45`'s own preference for a relative
 * check over an absolute one. Each test's doc line names the mutation it
 * exists to kill.
 */
import { describe, it, expect } from 'vitest';
import { getLayout, type LayoutManifest } from '../contract';
// Barrel-only, never a direct manifest-module import — the M7 lesson
// (`registry.test.ts`'s "dual-receiver-cockpit registration barrel proof",
// restated on every family in this directory): importing a manifest module
// directly fires `registerLayout` from THIS file and, under the fast pool's
// `isolate: false`, would leak the registration into sibling files.
import {
  desktopV2Layout, dualReceiverCockpitLayout, lcdCockpitLayout, lcdScopeLayout, mobileLayout,
  sdrTestLayout,
} from '../declarations';
// Namespace import of the SAME barrel, used ONLY to derive the completeness
// set structurally (never to register anything — a namespace import has no
// side effect beyond the module evaluation the named import above already
// triggers). NOT `listLayoutIds()`: the fast pool runs with `isolate: false`
// (vite.config.ts), so `contract.ts`'s module-scoped registry Map is shared
// across every test file in the run, and several siblings
// (`registry.test.ts`, `mobile-registration.test.ts`) register their own
// probe manifests into it — `listLayoutIds()` would make this file's
// completeness assertion depend on file execution order, deterministically
// red under `--no-file-parallelism` and intermittently red in a plain run.
// The barrel's own export surface has no such cross-file state.
import * as layoutDeclarationsBarrel from '../declarations';

/** Every manifest currently registered by the barrel (mirrors
 *  `forward-declaration-inventory.test.ts`'s `ALL_MANIFESTS`). */
const ALL_MANIFESTS = {
  'sdr-test': sdrTestLayout,
  'dual-receiver-cockpit': dualReceiverCockpitLayout,
  'lcd-cockpit': lcdCockpitLayout,
  'lcd-scope': lcdScopeLayout,
  mobile: mobileLayout,
  'desktop-v2': desktopV2Layout,
} as const;

/**
 * Expected resolved loader specifier per manifest id — the ONE real skin
 * entrypoint each manifest's `loader` closure must name. Values are the
 * absolute, dev-server-rooted specifiers `import()` resolves to under this
 * suite's Vite/Vitest transform (verified against the actual stringified
 * closures before this table was written); a change to how Vite resolves
 * specifiers would shift every row identically, not selectively, so it does
 * not mask a real swap.
 */
const EXPECTED_LOADER_SPECIFIER: Readonly<Record<keyof typeof ALL_MANIFESTS, string>> = {
  'sdr-test': '/src/skins/sdr-test/SdrTestSkin.svelte',
  'dual-receiver-cockpit': '/src/skins/dual-receiver-cockpit/DualReceiverCockpit.svelte',
  'lcd-cockpit': '/src/skins/lcd-cockpit/LcdCockpitSkin.svelte',
  'lcd-scope': '/src/skins/lcd-scope/LcdScopeSkin.svelte',
  mobile: '/src/skins/mobile/MobileSkin.svelte',
  'desktop-v2': '/src/skins/desktop-v2/DesktopSkin.svelte',
};

/** Pulls the quoted argument out of a stringified `() => import('...')`
 *  closure. Not anchored to Vite's SSR wrapper name (`__vite_ssr_dynamic_
 *  import__` today) so a transform-internal rename does not itself break
 *  this file — only a specifier change does. */
function importedSpecifier(loader: () => Promise<unknown>): string | null {
  return loader.toString().match(/\(\s*["']([^"']+)["']\s*\)/)?.[1] ?? null;
}

/** Structural `LayoutManifest` guard for filtering the barrel's export
 *  surface — every export the barrel currently has IS a manifest, but this
 *  keeps the derivation honest against a future non-manifest export (a
 *  helper constant, a re-exported type, etc.) landing in the same file
 *  without silently corrupting the completeness set below. */
function isLayoutManifest(value: unknown): value is LayoutManifest {
  return (
    typeof value === 'object' && value !== null &&
    (value as { schemaVersion?: unknown }).schemaVersion === 1 &&
    typeof (value as { id?: unknown }).id === 'string' &&
    typeof (value as { loader?: unknown }).loader === 'function'
  );
}

/** Every manifest the barrel exports, derived structurally rather than
 *  hand-listed — the completeness set this file's first test checks
 *  `ALL_MANIFESTS` against. Sourced from the barrel's export surface
 *  (see the namespace-import comment above), NOT `listLayoutIds()`. */
const BARREL_MANIFESTS: readonly LayoutManifest[] =
  Object.values(layoutDeclarationsBarrel).filter(isLayoutManifest);

describe('loader identity — every registered manifest pins its real skin entrypoint (MOR-1267)', () => {
  // Kills: a new manifest landing in the barrel without a corresponding row
  // in ALL_MANIFESTS/EXPECTED_LOADER_SPECIFIER above — it would otherwise be
  // silently absent from this inventory instead of failing loudly here. Also
  // kills the opposite drift: a table row surviving after its manifest stops
  // being exported from the barrel. (Registration omission — a manifest
  // exported from the barrel but never passed to `registerLayout` — is a
  // DIFFERENT mutation, killed below by the per-id `getLayout(id)` check,
  // not by this completeness test.)
  it('the manifest table matches every id the barrel exports', () => {
    const barrelIds = BARREL_MANIFESTS.map((m) => m.id).sort();
    expect(Object.keys(ALL_MANIFESTS).sort()).toEqual(barrelIds);
    expect(Object.keys(EXPECTED_LOADER_SPECIFIER).sort()).toEqual(Object.keys(ALL_MANIFESTS).sort());
  });

  it.each(Object.keys(ALL_MANIFESTS) as (keyof typeof ALL_MANIFESTS)[])(
    '"%s" is registered and its loader resolves to the pinned specifier',
    (id) => {
      // Kills: the barrel's exported binding drifting from what the real
      // registry actually holds under this id (an object built but never
      // passed to registerLayout, or registered under a different id).
      const registered = getLayout(id);
      expect(registered).toBe(ALL_MANIFESTS[id]);

      // Kills: silently repointing this manifest's loader at ANY other
      // real, loadable skin (the desktop-v2 pin round's adversarial-
      // verification mutant, generalized to every family) — `typeof loader
      // === 'function'`, already asserted in each family's own suite, stays
      // green under that mutation because it never inspects which module
      // the closure actually names. Also kills a loader replaced by a
      // function that names no module at all (the specifier extraction
      // returns null and the equality fails).
      const specifier = importedSpecifier(registered!.loader);
      expect(specifier).toBe(EXPECTED_LOADER_SPECIFIER[id]);
    },
  );
});
