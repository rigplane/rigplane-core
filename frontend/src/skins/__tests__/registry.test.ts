import { readFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import type { AppResource } from '$lib/runtime/resource-demand';
import type { SkinId } from '../registry';

// MOR-2074: which SkinId is QA-gated, read off `resolveSkinId`'s actual
// early-return branch in `../registry.ts` (`if (ctx.layoutPreference ===
// 'X') return 'X';`, checked on the RAW `ctx.layoutPreference` before
// `normalizeLayoutMode` runs — the regex requires the `ctx.` prefix
// specifically so it does not also match the normal forced-preference
// branches further down, which check the normalized local instead) rather
// than hand-copied, so this list cannot silently drift from the production
// branch that actually makes an id reachable only through the QA-only param.
const registrySource = readFileSync('src/skins/registry.ts', 'utf8');
const QA_GATED_LAZY_LOAD_IDS = [...registrySource.matchAll(
  /if \(ctx\.layoutPreference === '([a-z0-9-]+)'\) return '\1';/g,
)].map((m) => m[1]) as SkinId[];

// MOR-2074: keyed by the literal `SkinId` itself (not an arbitrary local
// name) and typed `Record<SkinId, ...>`, so a `SkinId` union member added
// without a matching entry here is a `npm run check` compile error — the
// same technique `EXPECTED_RESOURCE_PLAN` below already uses (MOR-2062),
// generalized to the mock entrypoints/loaders every other case in this file
// reads from.
const entrypoints = vi.hoisted(() => {
  const table: Record<SkinId, { name: SkinId }> = {
    'desktop-v2': { name: 'desktop-v2' },
    'lcd-cockpit': { name: 'lcd-cockpit' },
    'lcd-scope': { name: 'lcd-scope' },
    'mobile': { name: 'mobile' },
    'peer-split': { name: 'peer-split' },
    'sdr-test': { name: 'sdr-test' },
    'dual-receiver-cockpit': { name: 'dual-receiver-cockpit' },
    'dual-sdr-face': { name: 'dual-sdr-face' },
  };
  return table;
});

const lazyImports = vi.hoisted(() => {
  const table: Record<SkinId, () => { default: { name: SkinId } }> = {
    'desktop-v2': vi.fn(() => ({ default: entrypoints['desktop-v2'] })),
    'lcd-cockpit': vi.fn(() => ({ default: entrypoints['lcd-cockpit'] })),
    'lcd-scope': vi.fn(() => ({ default: entrypoints['lcd-scope'] })),
    'mobile': vi.fn(() => ({ default: entrypoints['mobile'] })),
    'peer-split': vi.fn(() => ({ default: entrypoints['peer-split'] })),
    'sdr-test': vi.fn(() => ({ default: entrypoints['sdr-test'] })),
    'dual-receiver-cockpit': vi.fn(() => ({ default: entrypoints['dual-receiver-cockpit'] })),
    'dual-sdr-face': vi.fn(() => ({ default: entrypoints['dual-sdr-face'] })),
  };
  return table;
});

vi.mock('../desktop-v2/DesktopSkin.svelte', () => lazyImports['desktop-v2']());
vi.mock('../lcd-cockpit/LcdCockpitSkin.svelte', () => lazyImports['lcd-cockpit']());
vi.mock('../lcd-scope/LcdScopeSkin.svelte', () => lazyImports['lcd-scope']());
vi.mock('../mobile/MobileSkin.svelte', () => lazyImports['mobile']());
vi.mock('../lcd-peer-split/LcdPeerSplitSkin.svelte', () => lazyImports['peer-split']());
vi.mock('../sdr-test/SdrTestSkin.svelte', () => lazyImports['sdr-test']());
vi.mock('../dual-receiver-cockpit/DualReceiverCockpit.svelte', () => lazyImports['dual-receiver-cockpit']());
vi.mock('../dual-sdr-face/DualSdrFaceSkin.svelte', () => lazyImports['dual-sdr-face']());

import { loadSkin, presentationResourcePlan, resolveSkinId } from '../registry';

const resolve = (overrides: Partial<Parameters<typeof resolveSkinId>[0]> = {}) =>
  resolveSkinId({
    capabilities: null,
    layoutPreference: 'auto',
    isMobile: false,
    hasAnyScope: false,
    ...overrides,
  });

describe('skin registry', () => {
  it('gives mobile precedence over every forced layout preference', () => {
    for (const layoutPreference of ['auto', 'lcd', 'lcd-cockpit', 'lcd-scope', 'standard', 'sdr-test', 'peer-split', 'dual-sdr-face'] as const) {
      expect(resolve({ isMobile: true, layoutPreference, hasAnyScope: true })).toBe('mobile');
    }
  });

  it.each([
    ['standard', 'desktop-v2'],
    ['lcd', 'lcd-cockpit'],
    ['lcd-cockpit', 'lcd-cockpit'],
    ['lcd-scope', 'lcd-scope'],
    ['sdr-test', 'sdr-test'],
    // MOR-2152: peer-split becomes a forced, selectable preference — the
    // resolveSkinId branch this ticket adds.
    ['peer-split', 'peer-split'],
    ['dual-sdr-face', 'dual-sdr-face'],
  ] as const)('resolves forced %s preference to %s', (layoutPreference, skinId) => {
    expect(resolve({ layoutPreference, hasAnyScope: false })).toBe(skinId);
  });

  it.each([
    [true, 'desktop-v2'],
    [false, 'desktop-v2'],
  ] as const)('resolves auto to the v3 desktop default regardless of scope availability (%s)', (hasAnyScope, skinId) => {
    expect(resolve({ hasAnyScope })).toBe(skinId);
  });

  // MOR-2074: derived from `lazyImports` (now `Record<SkinId, ...>`) instead
  // of a hand-picked five of the six keys — this used to omit
  // `dual-receiver-cockpit` with no completeness check to catch it, so a
  // regression that eagerly imported it at module-init time would have
  // stayed silent here (the only other guard, "does not import ... merely by
  // resolving other preferences" below, runs later and after several
  // `resolve()` calls, not at true module-init time).
  it('does not import a skin entrypoint while the registry is initialized', () => {
    for (const lazyImport of Object.values(lazyImports)) {
      expect(lazyImport).not.toHaveBeenCalled();
    }
  });

  // `dual-receiver-cockpit` is deliberately absent from this table: its own
  // lazy-load pin lives in the QA-only describe block below and must run
  // AFTER this table (see that block's call-order comment) — this file has
  // no per-test mock reset, so merging it here would double-invoke its
  // loader before that block's "not called merely by resolving" assertion.
  const LAZY_LOAD_TABLE = [
    ['desktop-v2', entrypoints['desktop-v2'], lazyImports['desktop-v2']],
    ['lcd-cockpit', entrypoints['lcd-cockpit'], lazyImports['lcd-cockpit']],
    ['lcd-scope', entrypoints['lcd-scope'], lazyImports['lcd-scope']],
    ['mobile', entrypoints['mobile'], lazyImports['mobile']],
    ['peer-split', entrypoints['peer-split'], lazyImports['peer-split']],
    ['sdr-test', entrypoints['sdr-test'], lazyImports['sdr-test']],
    ['dual-sdr-face', entrypoints['dual-sdr-face'], lazyImports['dual-sdr-face']],
  ] as const;

  it.each(LAZY_LOAD_TABLE)('lazily loads the %s entrypoint', async (skinId: SkinId, entrypoint, lazyImport) => {
    await expect(loadSkin(skinId)).resolves.toBe(entrypoint);
    expect(lazyImport).toHaveBeenCalledTimes(1);
  });

  // `QA_GATED_LAZY_LOAD_IDS` is now derived from `../registry.ts` itself
  // (see its declaration above), not hand-copied, so this only catches a
  // `SkinId` left off BOTH lists entirely — it does not by itself prove the
  // QA-gated id's own pin test still exists (see the check next to that
  // test in the describe block below).
  it('pins a lazy-load case for every skin, in this table or the QA-gated one', () => {
    const pinnedIds = [...LAZY_LOAD_TABLE.map(([id]) => id), ...QA_GATED_LAZY_LOAD_IDS];
    expect(pinnedIds.sort()).toEqual((Object.keys(entrypoints) as SkinId[]).sort());
  });
});

// MOR-1257 — interim QA reachability for the dual-receiver cockpit, gated
// behind the exact `?layout=dual-receiver-cockpit` query param (the URL ->
// LayoutMode translation itself is `readQaCockpitLayoutOverride`, pinned
// separately in lib/stores/__tests__/qa-cockpit-override.test.ts). These
// tests pin resolveSkinId's half of the contract only.
describe('MOR-1257: QA-only dual-receiver-cockpit reachability', () => {
  // Kill-test: removing this branch (or mistyping the literal) leaves the
  // QA-only preference falling through `normalizeLayoutMode` to 'auto',
  // which resolves to 'desktop-v2' unconditionally (MOR-1097 cutover) —
  // never the cockpit.
  it('resolves the QA-only preference to the cockpit skin', () => {
    expect(resolve({ layoutPreference: 'dual-receiver-cockpit' })).toBe('dual-receiver-cockpit');
    expect(resolve({ layoutPreference: 'dual-receiver-cockpit', hasAnyScope: true })).toBe('dual-receiver-cockpit');
  });

  // Default-path pin (ticket acceptance): every OTHER forced preference is
  // completely unaffected by the new branch — same outcomes as the
  // unmodified 'resolves forced %s preference to %s' cases above.
  it('leaves every other forced preference unaffected', () => {
    expect(resolve({ layoutPreference: 'standard' })).toBe('desktop-v2');
    expect(resolve({ layoutPreference: 'auto', hasAnyScope: false })).toBe('desktop-v2');
  });

  // Documents the chosen behaviour for the ticket's mobile/QA-override
  // tension: the mobile short-circuit stays first, so an actual phone
  // viewport keeps the mobile skin even with the QA param present. QA is
  // expected to open the URL on a desktop-sized viewport.
  it('still gives mobile precedence over the QA-only preference', () => {
    expect(resolve({ isMobile: true, layoutPreference: 'dual-receiver-cockpit', hasAnyScope: true }))
      .toBe('mobile');
  });

  // Must run before the lazy-load test below actually triggers the import —
  // this file has no per-test mock reset, so call order is significant here
  // (mirrors "does not import a skin entrypoint while the registry is
  // initialized" above, which runs before every "lazily loads" case).
  it('does not import the dual-receiver-cockpit entrypoint merely by resolving other preferences', () => {
    for (const layoutPreference of ['auto', 'standard', 'lcd-cockpit', 'lcd-scope', 'sdr-test'] as const) {
      resolve({ layoutPreference });
    }
    expect(lazyImports['dual-receiver-cockpit']).not.toHaveBeenCalled();
  });

  // MOR-2074 review: unlike `LAZY_LOAD_TABLE` above, whose "has a pin"
  // guarantee is structural (`it.each` iterates the array itself, so
  // deleting a row also removes it from the derived id list the
  // completeness test compares against), each QA-gated id's pin here is a
  // freestanding `it.each` case with no array tying its EXISTENCE to
  // `QA_GATED_LAZY_LOAD_IDS` — deleting this whole block previously left
  // every other check in this file green. `observedQaGatedLazyLoadPins`
  // records which ids actually got a pin that ran and passed; the test
  // below fails if an id in `QA_GATED_LAZY_LOAD_IDS` never reached it.
  const observedQaGatedLazyLoadPins = new Set<SkinId>();

  it.each(QA_GATED_LAZY_LOAD_IDS)('lazily loads the %s entrypoint through the real loader', async (skinId) => {
    await expect(loadSkin(skinId)).resolves.toBe(entrypoints[skinId]);
    expect(lazyImports[skinId]).toHaveBeenCalledTimes(1);
    observedQaGatedLazyLoadPins.add(skinId);
  });

  it('every QA-gated id actually reached its own lazy-load pin above', () => {
    for (const id of QA_GATED_LAZY_LOAD_IDS) {
      expect(observedQaGatedLazyLoadPins.has(id), `no lazy-load pin ran for QA-gated id "${id}"`).toBe(true);
    }
  });
});

// MOR-1060 — the private per-presentation resource plan. It exists so the
// composition root can bridge demand across a swap; it is read off the actual
// component trees, not invented per skin.
describe('presentation resource plan', () => {
  // MOR-2062: `everySkin` and this it.each table used to be two separately
  // hand-listed arrays, and both silently dropped `dual-receiver-cockpit` —
  // five of six skins, no failure anywhere. `Record<SkinId, ...>` is the
  // same technique the sibling `entrypoints.test.ts` already uses for this
  // exact constraint (see that file's header: there is no runtime-
  // enumerable list of `SkinId` values, and both `SKIN_LOADERS` and
  // `SKIN_RESOURCE_PLAN` in registry.ts are module-private), so a skin
  // missing from this table is now a `npm run check` compile error instead
  // of a silent gap. `everySkin` is derived from this table's own keys, so
  // the two can no longer drift from each other either.
  const EXPECTED_RESOURCE_PLAN: Record<SkinId, readonly AppResource[]> = {
    'desktop-v2': ['audio-fft', 'hardware-scope'],
    'dual-receiver-cockpit': [],
    // MOR-2153 PR-1: `peer-split` mounts the LCD shell (`LcdLayout`
    // variant="peer-split"), which reuses `RightSidebar`'s
    // `AudioSpectrumPanel`-behind-`hasAudioFft()` — same producer
    // `lcd-cockpit`/`lcd-scope` already demand `audio-fft` for.
    'peer-split': ['audio-fft'],
    'sdr-test': ['audio-fft', 'hardware-scope'],
    'lcd-cockpit': ['audio-fft'],
    'lcd-scope': ['audio-fft'],
    // The mobile layout mounts SpectrumPanel but no audio-FFT surface.
    'mobile': ['hardware-scope'],
    'dual-sdr-face': ['hardware-scope'],
  };

  const everySkin = Object.keys(EXPECTED_RESOURCE_PLAN) as SkinId[];

  it.each(Object.entries(EXPECTED_RESOURCE_PLAN) as Array<[SkinId, readonly AppResource[]]>)(
    'names the resources the %s tree can demand',
    (skinId, resources) => {
      expect([...presentationResourcePlan(skinId)].sort()).toEqual([...resources]);
    },
  );

  // MUTATION KILLED: adding `rx-audio` to any plan. Its lease belongs to the
  // runtime (`setRxLive`), not to a presentation subtree — bridging it would
  // hand a second owner to a resource that already survives a swap.
  it('never claims rx-audio for a presentation', () => {
    for (const skinId of everySkin) {
      expect(presentationResourcePlan(skinId)).not.toContain('rx-audio');
    }
  });
});
