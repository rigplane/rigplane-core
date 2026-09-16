import { readFileSync } from 'node:fs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { AppResource } from '$lib/runtime/resource-demand';
import type { HostedFaceComponentV1 } from '../../../component-kit-api/src/index';
import type { SkinId } from '../registry';

// MOR-2074: which SkinId is QA-gated, read off `resolveSkinId`'s actual
// early-return branch in `../registry.ts`, checked on the RAW
// `ctx.layoutPreference` before `normalizeLayoutMode` runs — the regex
// requires the `ctx.` prefix specifically so it does not also match the
// normal forced-preference branches further down, which check the normalized
// local instead — rather than hand-copied, so this list cannot silently
// drift from the production branch that actually makes an id reachable only
// through the QA-only param.
//
// T198 replaced the matched branch's bare `return 'X'` with the topology
// gate's own call (`return resolveWithFallback('X', ctx.capabilities)`). Both
// QA-only ids stay derivable here only while both branches still carry that
// call: drop the gate from one and this list loses that id, which the "pins a
// lazy-load case for every skin" completeness test below then fails on.
const registrySource = readFileSync('src/skins/registry.ts', 'utf8');
const QA_GATED_LAZY_LOAD_IDS = [...registrySource.matchAll(
  /if \(ctx\.layoutPreference === '([a-z0-9-]+)'\) return resolveWithFallback\('\1', ctx\.capabilities\);/g,
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
    'unified-instrument': { name: 'unified-instrument' },
    'panadapter-first': { name: 'panadapter-first' },
    'sdr-test': { name: 'sdr-test' },
    'dual-receiver-cockpit': { name: 'dual-receiver-cockpit' },
    'dual-sdr-face': { name: 'dual-sdr-face' },
    'flagship-probe': { name: 'flagship-probe' },
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
    'unified-instrument': vi.fn(() => ({ default: entrypoints['unified-instrument'] })),
    'panadapter-first': vi.fn(() => ({ default: entrypoints['panadapter-first'] })),
    'sdr-test': vi.fn(() => ({ default: entrypoints['sdr-test'] })),
    'dual-receiver-cockpit': vi.fn(() => ({ default: entrypoints['dual-receiver-cockpit'] })),
    'dual-sdr-face': vi.fn(() => ({ default: entrypoints['dual-sdr-face'] })),
    'flagship-probe': vi.fn(() => ({ default: entrypoints['flagship-probe'] })),
  };
  return table;
});

vi.mock('../desktop-v2/DesktopSkin.svelte', () => lazyImports['desktop-v2']());
vi.mock('../lcd-cockpit/LcdCockpitSkin.svelte', () => lazyImports['lcd-cockpit']());
vi.mock('../lcd-scope/LcdScopeSkin.svelte', () => lazyImports['lcd-scope']());
vi.mock('../mobile/MobileSkin.svelte', () => lazyImports['mobile']());
vi.mock('../lcd-peer-split/LcdPeerSplitSkin.svelte', () => lazyImports['peer-split']());
vi.mock('../lcd-unified-instrument/LcdUnifiedInstrumentSkin.svelte', () => lazyImports['unified-instrument']());
vi.mock('../lcd-panadapter-first/LcdPanadapterFirstSkin.svelte', () => lazyImports['panadapter-first']());
vi.mock('../sdr-test/SdrTestSkin.svelte', () => lazyImports['sdr-test']());
vi.mock('../dual-receiver-cockpit/DualReceiverCockpit.svelte', () => lazyImports['dual-receiver-cockpit']());
vi.mock('../dual-sdr-face/DualSdrFaceSkin.svelte', () => lazyImports['dual-sdr-face']());
vi.mock('../flagship-probe/FlagshipProbeSkin.svelte', () => lazyImports['flagship-probe']());

import {
  commitExternalPresentationBatch, getPresentationRecord, isPresentationIdReserved,
  loadSkin, prepareExternalPresentationBatch, presentationHostMode,
  presentationResourcePlan, resolveSkinId,
  type ExternalPresentationRecord,
  type PresentationHostMode,
} from '../registry';
// T198: the real manifest registry. `../registry` pulls
// `presentation/layouts/declarations` in for its side effect, so these
// resolve the shipped declarations rather than a fixture.
import { getLayout, TOPOLOGY_CLASSES } from '../../presentation/layouts/contract';
import type { LayoutManifest } from '../../presentation/layouts/contract';
import type { LayoutMode } from '$lib/runtime/adapters/layout-mode-adapter';
import type { Capabilities, VfoScheme } from '$lib/types/capabilities';

/** Only `vfoScheme`/`receivers`/`capabilities` reach the topology half of
 *  `derivePresentationCapabilities`; the rest of `Capabilities` only feeds
 *  its scope/audio diagnostics, which this gate never reads. */
const capsFor = (vfoScheme: VfoScheme, receivers: 1 | 2): Capabilities =>
  ({ vfoScheme, receivers, capabilities: ['dual_rx'] }) as unknown as Capabilities;
const SINGLE_AB = capsFor('ab', 1);
const AB_SHARED = capsFor('ab_shared', 2);
const MAIN_SUB = capsFor('main_sub', 2);

const resolve = (overrides: Partial<Parameters<typeof resolveSkinId>[0]> = {}) =>
  resolveSkinId({
    capabilities: null,
    layoutPreference: 'auto',
    isMobile: false,
    hasAnyScope: false,
    ...overrides,
  });

describe('skin registry', () => {
  it('keeps loader, host kind and resources in one discriminated built-in record table', () => {
    expect(registrySource).not.toContain('const PRESENTATION_HOST_MODE');
    expect(registrySource).not.toContain('const SKIN_RESOURCE_PLAN');

    const catalog = registrySource.slice(
      registrySource.indexOf('const SKIN_LOADERS'),
      registrySource.indexOf('} satisfies BuiltInPresentationCatalog;') + 1,
    );
    for (const id of Object.keys(entrypoints) as SkinId[]) {
      const record = catalog.match(new RegExp(`'${id}':\\s*\\{([\\s\\S]*?)\\n  \\},`));
      expect(record, `no unified catalog record for ${id}`).not.toBeNull();
      expect(record![1]).toMatch(/kind:\s*'built-in-(instrument-layout|self-contained)'/);
      expect(record![1]).toMatch(/loader:\s*\(\)\s*=>\s*import\(/);
      expect(record![1]).toMatch(/resources:\s*\[/);
    }
  });

  it('gives mobile precedence over every forced layout preference', () => {
    for (const layoutPreference of [
      'auto', 'lcd', 'lcd-cockpit', 'lcd-scope', 'standard', 'sdr-test', 'peer-split',
      'unified-instrument', 'panadapter-first', 'dual-sdr-face',
    ] as const) {
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
    ['unified-instrument', 'unified-instrument'],
    ['panadapter-first', 'panadapter-first'],
    ['dual-sdr-face', 'dual-sdr-face'],
  ] as const)('resolves forced %s preference to %s', (layoutPreference, skinId) => {
    expect(resolve({ layoutPreference, hasAnyScope: false, capabilities: MAIN_SUB })).toBe(skinId);
  });

  // T198: the same table minus the three `segmentline` preferences, whose
  // manifests exclude a single-receiver radio. Every preference here resolves
  // with no live capabilities at all, because the manifest registered under
  // its id (or, for `dual-sdr-face`, the absence of one) excludes nothing.
  it.each([
    ['standard', 'desktop-v2'],
    ['lcd', 'lcd-cockpit'],
    ['lcd-cockpit', 'lcd-cockpit'],
    ['lcd-scope', 'lcd-scope'],
    ['sdr-test', 'sdr-test'],
    ['dual-sdr-face', 'dual-sdr-face'],
  ] as const)('resolves forced %s preference to %s before capabilities arrive', (layoutPreference, skinId) => {
    expect(resolve({ layoutPreference, capabilities: null })).toBe(skinId);
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

  // `dual-receiver-cockpit` and `flagship-probe` are deliberately absent from
  // this table: their lazy-load pins live in the QA-only describe block below
  // and must run AFTER this table (see that block's call-order comment) —
  // this file has no per-test mock reset, so merging them here would
  // double-invoke their loaders before that block's "not called merely by
  // resolving" assertion.
  const LAZY_LOAD_TABLE = [
    ['desktop-v2', entrypoints['desktop-v2'], lazyImports['desktop-v2']],
    ['lcd-cockpit', entrypoints['lcd-cockpit'], lazyImports['lcd-cockpit']],
    ['lcd-scope', entrypoints['lcd-scope'], lazyImports['lcd-scope']],
    ['mobile', entrypoints['mobile'], lazyImports['mobile']],
    ['peer-split', entrypoints['peer-split'], lazyImports['peer-split']],
    ['unified-instrument', entrypoints['unified-instrument'], lazyImports['unified-instrument']],
    ['panadapter-first', entrypoints['panadapter-first'], lazyImports['panadapter-first']],
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

// MOR-1257 (cockpit) and T160 PR-1 (flagship probe) — interim QA
// reachability, gated behind the exact `?layout=<id>` query param (the URL ->
// LayoutMode translation itself is `readQaCockpitLayoutOverride`, pinned
// separately in lib/stores/__tests__/qa-cockpit-override.test.ts). These
// tests pin resolveSkinId's half of the contract only.
describe('QA-only layout reachability', () => {
  // Kill-test: removing this branch (or mistyping the literal) leaves the
  // QA-only preference falling through `normalizeLayoutMode` to 'auto',
  // which resolves to 'desktop-v2' unconditionally (MOR-1097 cutover) —
  // never the cockpit.
  it('resolves the QA-only preference to the cockpit skin', () => {
    expect(resolve({ layoutPreference: 'dual-receiver-cockpit', capabilities: MAIN_SUB })).toBe('dual-receiver-cockpit');
    expect(resolve({
      layoutPreference: 'dual-receiver-cockpit', hasAnyScope: true, capabilities: MAIN_SUB,
    })).toBe('dual-receiver-cockpit');
  });

  // Kill-test: removing the T160 branch leaves 'flagship-probe' falling
  // through `normalizeLayoutMode` to 'auto', hence to 'desktop-v2'.
  it('resolves the QA-only preference to the flagship geometry probe', () => {
    expect(resolve({ layoutPreference: 'flagship-probe', capabilities: MAIN_SUB })).toBe('flagship-probe');
    expect(resolve({
      layoutPreference: 'flagship-probe', hasAnyScope: true, capabilities: MAIN_SUB,
    })).toBe('flagship-probe');
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
  it.each(['dual-receiver-cockpit', 'flagship-probe'] as const)(
    'still gives mobile precedence over the QA-only %s preference', (layoutPreference) => {
      expect(resolve({ isMobile: true, layoutPreference, hasAnyScope: true })).toBe('mobile');
    });

  // Must run before the lazy-load test below actually triggers the import —
  // this file has no per-test mock reset, so call order is significant here
  // (mirrors "does not import a skin entrypoint while the registry is
  // initialized" above, which runs before every "lazily loads" case).
  it('does not import a QA-gated entrypoint merely by resolving other preferences', () => {
    for (const layoutPreference of ['auto', 'standard', 'lcd-cockpit', 'lcd-scope', 'sdr-test'] as const) {
      resolve({ layoutPreference });
    }
    for (const id of QA_GATED_LAZY_LOAD_IDS) expect(lazyImports[id]).not.toHaveBeenCalled();
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
  // enumerable list of `SkinId` values, and the unified `SKIN_LOADERS`
  // catalog in registry.ts is module-private), so a skin
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
    'unified-instrument': ['audio-fft'],
    'panadapter-first': ['audio-fft', 'hardware-scope'],
    'sdr-test': ['audio-fft', 'hardware-scope'],
    'lcd-cockpit': ['audio-fft'],
    'lcd-scope': ['audio-fft'],
    // The mobile layout mounts SpectrumPanel but no audio-FFT surface.
    'mobile': ['hardware-scope'],
    'dual-sdr-face': ['hardware-scope'],
    // T160 PR-1: the geometry probe's one resource-demanding component is a
    // SpectrumPanel, the same reason `mobile` above names this alone.
    'flagship-probe': ['hardware-scope'],
  };

  it('keeps panadapter-first resource order hardware-selected, with the LCD shell AF consumer retained', () => {
    expect(presentationResourcePlan('panadapter-first')).toEqual(['hardware-scope', 'audio-fft']);
  });

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

describe('presentation host mode', () => {
  const EXPECTED_HOST_MODE: Record<SkinId, PresentationHostMode> = {
    'desktop-v2': 'instrument-handles',
    'sdr-test': 'instrument-handles',
    'dual-receiver-cockpit': 'self-contained',
    'lcd-cockpit': 'self-contained',
    'lcd-scope': 'self-contained',
    'mobile': 'self-contained',
    'peer-split': 'self-contained',
    'unified-instrument': 'self-contained',
    'panadapter-first': 'self-contained',
    'dual-sdr-face': 'self-contained',
    'flagship-probe': 'self-contained',
  };

  it.each(Object.entries(EXPECTED_HOST_MODE) as Array<[SkinId, PresentationHostMode]>) (
    'selects %s as %s',
    (skinId, mode) => expect(presentationHostMode(skinId)).toBe(mode),
  );
});

describe('external presentation records share the built-in catalog', () => {
  const faceA = (() => ({})) as unknown as HostedFaceComponentV1;
  const faceB = (() => ({})) as unknown as HostedFaceComponentV1;
  const appearances = {
    scalar: { name: 'External' },
    frequency: (() => ({})),
    finite: { action: () => ({}), toggle: () => ({}), choice: () => ({}) },
    meter: { signal: () => ({}), level: () => ({}) },
  } as unknown as ExternalPresentationRecord['appearances'];

  function externalRecord(
    id: string,
    face: HostedFaceComponentV1,
    resources: AppResource[] = [],
  ): ExternalPresentationRecord {
    return {
      id,
      kind: 'external-instruments-v1',
      layoutId: `${id}-layout`,
      loader: vi.fn(async () => face),
      resources,
      appearances,
    };
  }

  it.each(['desktop-v2', '__proto__', 'constructor', 'toString'])(
    'rejects registered or inherited catalog id %s before commit',
    (id) => {
      expect(isPresentationIdReserved(id)).toBe(true);
      expect(() => prepareExternalPresentationBatch([externalRecord(id, faceA)]))
        .toThrow(/registered or reserved/i);
      if (id !== 'desktop-v2') expect(getPresentationRecord(id)).toBeUndefined();
    },
  );

  it('rejects a duplicate external id before commit', () => {
    expect(() => prepareExternalPresentationBatch([
      externalRecord('duplicate-external', faceA),
      externalRecord('duplicate-external', faceB),
    ])).toThrow(/more than once/i);
    expect(getPresentationRecord('duplicate-external')).toBeUndefined();
  });

  it('commits two immutable records and keeps their direct loaders lazy', async () => {
    const resources: AppResource[] = ['hardware-scope'];
    const first = externalRecord('external-face-one', faceA, resources);
    const second = externalRecord('external-face-two', faceB, ['audio-fft']);
    const prepared = prepareExternalPresentationBatch([first, second]);
    resources.length = 0;

    expect(first.loader).not.toHaveBeenCalled();
    expect(second.loader).not.toHaveBeenCalled();
    commitExternalPresentationBatch(prepared);

    const committed = getPresentationRecord('external-face-one');
    expect(committed).toMatchObject({
      kind: 'external-instruments-v1',
      layoutId: 'external-face-one-layout',
      resources: ['hardware-scope'],
    });
    expect(Object.isFrozen(committed)).toBe(true);
    expect(committed?.kind === 'external-instruments-v1'
      && Object.isFrozen(committed.appearances)).toBe(true);
    expect(Object.isFrozen(committed?.resources)).toBe(true);
    expect(presentationHostMode('external-face-one')).toBe('external-instruments-v1');
    expect(presentationResourcePlan('external-face-two')).toEqual(['audio-fft']);
    await expect(loadSkin('external-face-one')).resolves.toBe(faceA);
    await expect(loadSkin('external-face-two')).resolves.toBe(faceB);
    expect(first.loader).toHaveBeenCalledTimes(1);
    expect(second.loader).toHaveBeenCalledTimes(1);
  });

  it('loads the exact external record without looking it up again by id', async () => {
    const catalogRecord = externalRecord('correlated-face', faceB);
    commitExternalPresentationBatch(prepareExternalPresentationBatch([catalogRecord]));
    const capturedRecord = externalRecord('correlated-face', faceA);

    await expect(loadSkin(capturedRecord)).resolves.toBe(faceA);
    expect(capturedRecord.loader).toHaveBeenCalledTimes(1);
    expect(catalogRecord.loader).not.toHaveBeenCalled();
  });

  it('does not fall back for unknown or inherited ids', async () => {
    expect(getPresentationRecord('absent-external')).toBeUndefined();
    await expect(loadSkin('absent-external')).rejects.toThrow(/not registered/i);
    expect(() => presentationHostMode('toString')).toThrow(/not registered/i);
    expect(() => presentationResourcePlan('__proto__')).toThrow(/not registered/i);
  });

  it('retains one literal catalog and one loader implementation', () => {
    expect(registrySource.match(/const SKIN_LOADERS/g)).toHaveLength(1);
    expect(registrySource.match(/export async function loadSkin/g)).toHaveLength(1);
    expect(registrySource).not.toContain('EXTERNAL_SKIN_LOADERS');
    expect(registrySource).not.toContain('EXTERNAL_PRESENTATION_CATALOG');
  });
});

// T198 — a layout manifest's `compatibleTopologies` is a restriction on which
// radios its skin may mount, and its `fallbackLayoutId` names where a refused
// preference goes instead. Until this block both were validated, documented
// and read by nothing. `resolveSkinId` now refuses a layout preference whose
// registered manifest excludes the live receiver topology class, and mounts
// the first hop down that manifest's fallback chain which admits it.
describe('layout-manifest topology gate', () => {
  /** Read off the shipped manifests, not hand-listed: a skin id is gated iff
   *  a manifest is registered under it AND that manifest leaves at least one
   *  class out of `TOPOLOGY_CLASSES`. */
  const GATED = (Object.keys(entrypoints) as SkinId[]).filter((id) => {
    const manifest = getLayout(id);
    return manifest !== undefined
      && !TOPOLOGY_CLASSES.every((c) => manifest.compatibleTopologies.includes(c));
  });

  /** What each gated preference mounts on a radio its own manifest excludes.
   *  Hand-written: this is the outcome the walk owes, not a second copy of
   *  the walk. The completeness case below pins the key set against `GATED`
   *  and pins that every value here declares all four topology classes —
   *  which is why the same expectation holds for `1/ab`, for no capabilities
   *  at all, and for an invalid topology. */
  const MOUNTED_WHEN_REFUSED: Record<string, SkinId> = {
    'dual-receiver-cockpit': 'sdr-test',
    'flagship-probe': 'sdr-test',
    'peer-split': 'lcd-cockpit',
    // `peer-split` is the declared fallback of both, and is itself refused on
    // the same radio, so the walk continues to ITS fallback.
    'unified-instrument': 'lcd-cockpit',
    'panadapter-first': 'lcd-cockpit',
  };

  // Runs first in this block: the refusal report is throttled per (layout,
  // live class), and the cases below consume the pairs it counts.
  it('reports a refusal once per layout and live topology class', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    try {
      expect(resolve({ layoutPreference: 'flagship-probe', capabilities: SINGLE_AB })).toBe('sdr-test');
      expect(resolve({ layoutPreference: 'flagship-probe', capabilities: SINGLE_AB })).toBe('sdr-test');
      expect(warn).toHaveBeenCalledTimes(1);
      const [message] = warn.mock.calls[0] as [string];
      expect(message).toContain('flagship-probe');
      expect(message).toContain('1/ab');
      expect(message).toContain('2/ab_shared, 2/main_sub');
      // Same layout, a class not yet reported.
      expect(resolve({ layoutPreference: 'flagship-probe', capabilities: capsFor('single', 1) })).toBe('sdr-test');
      expect(warn).toHaveBeenCalledTimes(2);
      // Same class, a layout not yet reported.
      expect(resolve({ layoutPreference: 'peer-split', capabilities: SINGLE_AB })).toBe('lcd-cockpit');
      expect(warn).toHaveBeenCalledTimes(3);
    } finally {
      warn.mockRestore();
    }
  });

  // The refusal has to say what the user is looking at instead, or the report
  // explains a blank change of face with the name of a skin that is NOT on
  // screen. `unified-instrument` on `1/single` is a (layout, class) pair no
  // other case in this file consumes, so the throttle above cannot swallow it.
  it('names the layout the walk actually mounted', () => {
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {});
    try {
      expect(resolve({ layoutPreference: 'unified-instrument', capabilities: capsFor('single', 1) }))
        .toBe('lcd-cockpit');
      expect(warn).toHaveBeenCalledTimes(1);
      expect((warn.mock.calls[0] as [string])[0]).toContain('mounting "lcd-cockpit" instead');
    } finally {
      warn.mockRestore();
    }
  });

  it('gates exactly the loadable skins whose manifest excludes a topology class', () => {
    expect([...GATED].sort()).toEqual([
      'dual-receiver-cockpit', 'flagship-probe', 'panadapter-first', 'peer-split',
      'unified-instrument',
    ]);
    expect(Object.keys(MOUNTED_WHEN_REFUSED).sort()).toEqual([...GATED].sort());
    for (const mounted of new Set(Object.values(MOUNTED_WHEN_REFUSED))) {
      const manifest = getLayout(mounted);
      expect(manifest, `${mounted} is a registered layout`).toBeDefined();
      expect(TOPOLOGY_CLASSES.every((c) => manifest!.compatibleTopologies.includes(c)),
        `${mounted} declares every topology class`).toBe(true);
    }
  });

  // The defect this block exists for: the probe mounted on an IC-7300 (1/ab),
  // a radio its own manifest excludes.
  it.each(GATED)('routes the refused %s preference to its declared fallback', (skinId) => {
    expect(resolve({ layoutPreference: skinId as LayoutMode, capabilities: SINGLE_AB }))
      .toBe(MOUNTED_WHEN_REFUSED[skinId]);
  });

  it.each(GATED)('admits the %s preference on both topology classes it declares', (skinId) => {
    expect(resolve({ layoutPreference: skinId as LayoutMode, capabilities: AB_SHARED })).toBe(skinId);
    expect(resolve({ layoutPreference: skinId as LayoutMode, capabilities: MAIN_SUB })).toBe(skinId);
  });

  it.each(GATED)('never mounts %s while the live topology is underivable', (skinId) => {
    const fallback = MOUNTED_WHEN_REFUSED[skinId];
    expect(resolve({ layoutPreference: skinId as LayoutMode, capabilities: null })).toBe(fallback);
    expect(resolve({
      layoutPreference: skinId as LayoutMode, capabilities: undefined as unknown as null,
    })).toBe(fallback);
    // `receivers` disagreeing with `vfoScheme` is what
    // `derivePresentationCapabilities` reports as `invalid-topology`, and it
    // yields no class either.
    expect(resolve({
      layoutPreference: skinId as LayoutMode, capabilities: capsFor('main_sub', 1),
    })).toBe(fallback);
  });

  it('leaves a preference with no registered manifest unaffected', () => {
    expect(getLayout('dual-sdr-face')).toBeUndefined();
    expect(resolve({ layoutPreference: 'dual-sdr-face', capabilities: SINGLE_AB })).toBe('dual-sdr-face');
    expect(resolve({ layoutPreference: 'dual-sdr-face', capabilities: null })).toBe('dual-sdr-face');
  });
});

// T198 — the ends of the fallback walk, which no shipped chain reaches: every
// shipped chain terminates on a layout declaring all four topology classes.
describe('layout fallback chain terminates', () => {
  // Each case refuses a preference, so each reports one refusal.
  beforeEach(() => { vi.spyOn(console, 'warn').mockImplementation(() => {}); });
  afterEach(() => { vi.restoreAllMocks(); });

  /** A fresh module graph. `../registry` pulls `presentation/layouts/
   *  declarations` in for its side effect, so each call gets its own layout
   *  registry — one a test-only manifest can register into under
   *  `dual-sdr-face`, the one loadable skin id the shipped declarations leave
   *  free (pinned by the case above). Static bindings elsewhere in this file
   *  keep pointing at the first instance, so earlier suites are untouched. */
  async function freshRegistry() {
    vi.resetModules();
    const contract = await import('../../presentation/layouts/contract');
    const registry = await import('../registry');
    /** A test-only manifest: a shipped one cloned wholesale, with only the id,
     *  the topology restriction and the fallback replaced. `2/main_sub` alone
     *  is what makes `SINGLE_AB` a refusal, so the walk runs. Cloned rather
     *  than written out as a literal because a literal here would have to name
     *  the manifest's stage sizing-policy field, which
     *  `presentation/layouts/__tests__/stage-sizing-boundary.test.ts` forbids
     *  any file outside `presentation/layouts/` from naming. */
    const base = contract.getLayout('lcd-cockpit');
    expect(base, 'lcd-cockpit is registered in the fresh graph').toBeDefined();
    const register = (id: string, fallbackLayoutId: string | null) =>
      contract.registerLayout({
        ...(base as LayoutManifest),
        id,
        displayName: id,
        compatibleTopologies: ['2/main_sub'],
        fallbackLayoutId,
      });
    return {
      register,
      resolve: (layoutPreference: LayoutMode) => registry.resolveSkinId({
        capabilities: SINGLE_AB, layoutPreference, isMobile: false, hasAnyScope: false,
      }),
    };
  }

  it('stops at the default when the chain names an id already visited', async () => {
    const { register, resolve: resolveFresh } = await freshRegistry();
    register('dual-sdr-face', 'fallback-hop-one');
    register('fallback-hop-one', 'fallback-hop-two');
    register('fallback-hop-two', 'fallback-hop-one');
    expect(resolveFresh('dual-sdr-face')).toBe('desktop-v2');
  });

  it('stops at the default when the chain names no registered layout', async () => {
    const { register, resolve: resolveFresh } = await freshRegistry();
    register('dual-sdr-face', 'fallback-absent');
    expect(resolveFresh('dual-sdr-face')).toBe('desktop-v2');
  });
});
