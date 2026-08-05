/**
 * MOR-1266 — the `desktop-v2` presentation entrypoint's v1 layout manifest,
 * registered and resolved through the REAL registry (MOR-1066), mirroring
 * the shape `lcd-registration.test.ts` (MOR-1092) and
 * `mobile-registration.test.ts` (MOR-1094) use for their families.
 *
 * Every claim below is read back out of the shared registry rather than off
 * the exported object, so a manifest that is written but never registered
 * fails here. Each test's doc line names the mutation it exists to kill.
 */
import { existsSync, readFileSync } from 'node:fs';
import { describe, it, expect } from 'vitest';
import {
  declaredSurfaces, getLayout, resolveLayoutForTopology, resolveLayoutForViewport,
  TOPOLOGY_CLASSES,
} from '../contract';
// Barrel-only, never '../desktop-declarations' directly — the M7 lesson
// (registry.test.ts's "dual-receiver-cockpit registration barrel proof",
// restated on every family since): a direct manifest import fires
// `registerLayout` from THIS file, masking a `declarations.ts` that no
// longer wires desktop-v2 into the app, and under the fast pool's
// `isolate: false` it would leak the registration into sibling files.
import { desktopV2Layout } from '../declarations';

/** iPhone-class portrait — the viewport the LCD family fails arithmetically. */
const PORTRAIT_MOBILE = { width: 390, height: 844 };

describe('the desktop-v2 entrypoint is registered in the real registry', () => {
  // Kills: desktop-declarations.ts defining the manifest but never calling
  // registerLayout — every resolution below would then read undefined.
  it('registers "desktop-v2" under its stable entrypoint id', () => {
    expect(getLayout('desktop-v2')).toBe(desktopV2Layout);
    expect(desktopV2Layout.id).toBe('desktop-v2');
  });

  // Kills: a manifest id that drifts from the SkinId the App actually loads,
  // or from the id `resolveSkinId` hands back for the standard/auto
  // preference. The manifest is only an entrypoint declaration if all three
  // agree.
  it('uses the same id the skin registry loads the desktop-v2 entrypoint under', () => {
    const source = readFileSync('src/skins/registry.ts', 'utf8');
    const start = source.indexOf('const SKIN_LOADERS');
    const loaders = source.slice(start, source.indexOf('};', start));
    expect(loaders).toContain("'desktop-v2':");
    expect(source).toContain("if (layoutPreference === 'standard') return 'desktop-v2';");
    expect(source).toContain('return ctx.hasAnyScope ? \'desktop-v2\' : \'lcd-cockpit\';');
  });

  // Kills: a manifest that declares no compiled loader at all.
  it('declares a compiled loader', () => {
    expect(typeof desktopV2Layout.loader).toBe('function');
  });
});

describe('declared zones now drive the DOM (MOR-1263 step 2, MOR-1313)', () => {
  // Kills: declaring a surface this manifest does not name, or dropping
  // either one — the pair per-zone suppression consumes.
  it('declares receiver-deck:[vfo] and rx-tx:[rxTx]', () => {
    expect(desktopV2Layout.zones).toEqual([
      { id: 'receiver-deck', surfaces: ['vfo'] },
      { id: 'rx-tx', surfaces: ['rxTx'] },
    ]);
    expect([...desktopV2Layout.requiredSemanticSurfaces].sort()).toEqual(['rxTx', 'vfo']);
  });

  // Kills: RadioLayout.svelte regressing to a per-skin-id gate — the exact
  // `skinId === 'sdr-test'` boolean MOR-1266 pinned as still-present and
  // MOR-1313 removed. A reintroduced id fork would leave this manifest
  // decorative again while every registry assertion above stayed green. Read
  // as TEXT for the same reason as the loader pin below (no DOM tree needed).
  it('RadioLayout.svelte derives suppression from the manifest, not from a skin id', () => {
    const source = readFileSync('src/components-v2/layout/RadioLayout.svelte', 'utf8');
    expect(source).toContain('let declared = $derived(declaredSurfaces(getLayout(skinId)));');
    expect(source).not.toContain("$derived(skinId === 'sdr-test')");
  });

  // Kills: `declaredSurfaces` losing the zone walk (e.g. returning a fixed
  // set, or reading only the first zone) — desktop-v2 is the manifest that
  // splits the pair ACROSS two zones, so it is the one that proves the walk
  // is per-zone rather than per-manifest-first-zone.
  it('its two zones flatten to the surfaces the shell suppresses legacy twins for', () => {
    expect([...declaredSurfaces(getLayout('desktop-v2'))].sort()).toEqual(['rxTx', 'vfo']);
  });
});

describe('loader identity — pins the real desktop-v2 entrypoint (verify.md N1)', () => {
  // Kills: repointing the manifest's OWN `loader` closure at a different,
  // real, loadable skin (e.g. `SdrTestSkin.svelte`) — the adversarial
  // verification's surviving mutant (V3). `typeof loader === 'function'`
  // above and the `skins/registry.ts` reachability check earlier both stay
  // green under that mutation, because neither ties THIS manifest's loader
  // to the file it actually names. Read as TEXT — the same idiom as every
  // other module-specifier pin in this suite — because invoking the loader
  // would pull in RadioLayout.svelte's full import graph, including
  // `lib/stores/layout.svelte.ts`'s module-scope `localStorage` read, which
  // throws outside a DOM environment (why F8 reads `skins/registry.ts` as
  // text instead of importing it).
  it('the manifest loader names DesktopSkin.svelte, not a sibling skin entrypoint', () => {
    const source = readFileSync('src/presentation/layouts/desktop-declarations.ts', 'utf8');
    const match = source.match(/loader:\s*\(\)\s*=>\s*import\(['"]([^'"]+)['"]\)/);
    expect(match?.[1]).toBe('../../skins/desktop-v2/DesktopSkin.svelte');
  });

  // Belt-and-braces: the specifier must resolve to a real file too — guards
  // against a typo'd path that would only fail at runtime, in the browser,
  // never in this suite.
  it('the loader specifier resolves to a real file on disk', () => {
    expect(existsSync('src/skins/desktop-v2/DesktopSkin.svelte')).toBe(true);
  });
});

describe('topology honesty', () => {
  // Kills: under-declaring the topology set. VfoHeader renders VfoOps
  // (A/B swap/equal) unconditionally and branches DualVfoDisplay vs a
  // single-receiver VfoPanel on hasDualReceiver(), so every canonical class
  // resolves to desktop-v2 itself, never to a fallback (it declares none).
  it.each(TOPOLOGY_CLASSES)('resolves itself on the %s topology', (topology) => {
    expect(resolveLayoutForTopology('desktop-v2', topology)?.id).toBe('desktop-v2');
  });
});

describe('MOR-1160 sizing axis — desktop-v2 stays fluid, mirroring sdr-test', () => {
  // Kills: silently switching desktop-v2 onto the fixed-native stage the LCD
  // family owns, or recording a breakpoint set that disagrees with
  // sdr-test's declaration for the identical shared RadioLayout stylesheet.
  it('declares fluid sizing with no breakpoints', () => {
    expect(desktopV2Layout.stageSizing).toEqual({ mode: 'fluid', responsiveBreakpoints: [] });
  });

  // Kills: fitsViewport gating a fluid layout on a breakpoint instead of
  // always fitting (contract: breakpoints are reflow hints, not a hard gate
  // in v1).
  it.each([
    ['desktop', { width: 1440, height: 900 }],
    ['portrait phone', PORTRAIT_MOBILE],
  ])('resolves itself on a %s viewport', (_label, viewport) => {
    expect(resolveLayoutForViewport('desktop-v2', viewport)?.id).toBe('desktop-v2');
  });
});

describe('no fallback family', () => {
  // Kills: adding a fallbackLayoutId that has nothing to do — desktop-v2
  // supports every canonical topology and fluid sizing never fails a
  // viewport, so any hop off it would be unreachable and would only mask a
  // real resolution failure.
  it('declares no fallback', () => {
    expect(desktopV2Layout.fallbackLayoutId).toBeNull();
  });
});
