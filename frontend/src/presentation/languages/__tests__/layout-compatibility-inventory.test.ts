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
 * hand-listed literals by design (that file's own comment says so) — they
 * back that file's own completeness assertion, registration-identity pin
 * and per-id loader-specifier pinning. This file needs none of the three:
 * its own completeness check is structural (`SHIPPED_MANIFESTS` below), and
 * it takes no registry read or loader specifier at all.
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
// Same namespace-import discipline, LAYOUT side, for the second describe
// block below (MOR-2070).
import * as layoutDeclarationsBarrel from '../../layouts/declarations';
import type { LayoutManifest } from '../../layouts/contract';
// The shared structural `LayoutManifest` guard every `*-declarability.test.ts`
// suite in `../../layouts/__tests__/` already derives its own inventory with
// (e.g. `antenna-declarability.test.ts`'s `ALL`) — reused here rather than
// reimplemented.
import { isLayoutManifest } from '../../layouts/__tests__/manifest-guard';

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

/**
 * MOR-2070 — layout-side coverage, the counterpart of the describe block
 * above. That block guards against a shipped LANGUAGE that mentions no
 * layout at all; this one guards the opposite direction: a shipped LAYOUT
 * that no language mentions.
 *
 * "Unmentioned" conflates two different situations: a new layout nobody has
 * reviewed for design-language chrome yet (must fail loudly, the way a new
 * author's mistake should), and a layout this repository has deliberately
 * decided will never carry design-language chrome (`lcd-cockpit`,
 * `lcd-scope`, `mobile`, `sdr-test` below). The distinction is recorded here,
 * on the layout side, as `LAYOUT_EXEMPTIONS` — an explicit list with a reason
 * per entry — rather than as a full language×layout matrix.
 *
 * A layout counts as "mentioned" the moment ANY shipped language's
 * `layoutCompatibility` names it — a `compatible: true` entry and a
 * `compatible: false` entry both count equally as evidence a decision was
 * made, even though only `true` lets `designLanguageActivation` actually
 * activate for it (`fieldline`'s `dual-receiver-cockpit: false` entry
 * is exactly this: a decision, not a silent gap).
 *
 * This is enforced as a vitest assertion, not a runtime warning like
 * `layout-compatibility-guard.ts` (untouched by this ticket): a new layout
 * manifest only ever enters this repository through a PR, and this suite
 * runs on every such PR through `quick.yml`'s frontend path filter (any
 * change under `frontend/**` runs `npx vitest run` in that workflow's
 * frontend block).
 */
describe('every barrel-exported layout is design-language-listed or explicitly exempt (MOR-2070)', () => {
  // [id, manifest] pairs, derived structurally from the layouts barrel's
  // export surface — never hand-listed, the same discipline `SHIPPED_MANIFESTS`
  // above follows on the language side, and the same guard (`isLayoutManifest`)
  // every `*-declarability.test.ts` suite in `../../layouts/__tests__/` already
  // uses to derive its own layout inventory.
  const ALL_LAYOUTS: readonly LayoutManifest[] =
    Object.values(layoutDeclarationsBarrel).filter(isLayoutManifest);

  // Guards the derivation itself, same reason as the language-side check
  // above: if the barrel filter ever matched nothing, every assertion below
  // would pass vacuously.
  it('finds at least one layout to check', () => {
    expect(ALL_LAYOUTS.length).toBeGreaterThan(0);
  });

  /** Every layoutId any shipped language's `layoutCompatibility` names —
   *  `compatible: true` or `false` both count (see header). */
  const MENTIONED_LAYOUT_IDS = new Set(
    SHIPPED_MANIFESTS.flatMap((manifest) => manifest.layoutCompatibility.map((entry) => entry.layoutId)),
  );

  /** Layouts this repository has deliberately decided will carry no
   *  design-language chrome — extend by hand, with a reason, never silently.
   *  A layout landing here without a matching entry in `ALL_LAYOUTS` (a
   *  typo, a renamed/removed layout id) is caught by the third assertion
   *  below; one that stops being true (a language starts mentioning it) is
   *  caught by the second. */
  const LAYOUT_EXEMPTIONS: ReadonlyArray<{ readonly layoutId: string; readonly reason: string }> = [
    {
      layoutId: 'lcd-cockpit',
      reason:
        'carries its own built-in styling; design-language adoption not planned (owner ruling 2026-08-31, MOR-2070)',
    },
    {
      layoutId: 'lcd-scope',
      reason:
        'carries its own built-in styling; design-language adoption not planned (owner ruling 2026-08-31, MOR-2070)',
    },
    {
      layoutId: 'mobile',
      reason:
        'carries its own built-in styling; design-language adoption not planned (owner ruling 2026-08-31, MOR-2070)',
    },
    {
      layoutId: 'sdr-test',
      reason: 'minimal teaching example; no design-language chrome by design (owner ruling 2026-08-31, MOR-2070)',
    },
  ];
  const EXEMPT_LAYOUT_IDS = new Set(LAYOUT_EXEMPTIONS.map((exemption) => exemption.layoutId));

  // Kills: a new layout manifest (a new author's, or a renamed existing one)
  // landing in the barrel with no layoutCompatibility entry anywhere and no
  // exemption — the exact silent gap MOR-2070 exists to close.
  it.each(ALL_LAYOUTS.map((manifest) => [manifest.id] as const))(
    '"%s" is mentioned by a shipped design language, or carries a justified exemption',
    (id) => {
      const decided = MENTIONED_LAYOUT_IDS.has(id) || EXEMPT_LAYOUT_IDS.has(id);
      expect(
        decided,
        `layout "${id}" is mentioned by no shipped design language's layoutCompatibility and has no ` +
          'exemption entry. Either add a layoutCompatibility entry for it to a shipped language ' +
          '(frontend/src/presentation/languages/declarations.ts), or add a justified entry to ' +
          'LAYOUT_EXEMPTIONS in this file.',
      ).toBe(true);
    },
  );

  // Kills: a stale exemption — a layout that WAS unmentioned when exempted,
  // but a language now names it, so the exemption is dead cover rather than
  // a live decision. Same self-liquidation shape as
  // `focus-ring-token-wiring.test.ts`'s "LEGACY_DEBT entries still need
  // their exemption" case.
  it.each(LAYOUT_EXEMPTIONS.map((exemption) => [exemption.layoutId] as const))(
    'exemption "%s" is still needed — no shipped language mentions it',
    (layoutId) => {
      expect(
        MENTIONED_LAYOUT_IDS.has(layoutId),
        `layout "${layoutId}" is exempt but is now mentioned by a shipped design language — remove its ` +
          'entry from LAYOUT_EXEMPTIONS instead of leaving it as stale cover.',
      ).toBe(false);
    },
  );

  // Kills: an orphan exemption — a typo'd or since-removed layout id sitting
  // in LAYOUT_EXEMPTIONS that no longer names anything the barrel exports,
  // which would otherwise silently exempt nothing while looking like it
  // covers a real layout.
  it.each(LAYOUT_EXEMPTIONS.map((exemption) => [exemption.layoutId] as const))(
    'exemption "%s" refers to a layout id the barrel actually exports',
    (layoutId) => {
      expect(
        ALL_LAYOUTS.some((manifest) => manifest.id === layoutId),
        `LAYOUT_EXEMPTIONS names "${layoutId}", which is not a layout id the barrel exports — remove ` +
          'the orphan entry.',
      ).toBe(true);
    },
  );
});
