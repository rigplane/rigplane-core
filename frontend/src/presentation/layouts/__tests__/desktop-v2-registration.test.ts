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
import { readFileSync } from 'node:fs';
import { describe, it, expect } from 'vitest';
import { declaredSurfaces, getLayout } from '../contract';
// Barrel-only, never '../desktop-declarations' directly — the M7 lesson
// (registry.test.ts's "dual-receiver-cockpit registration barrel proof",
// restated on every family since): a direct manifest import fires
// `registerLayout` from THIS file, masking a `declarations.ts` that no
// longer wires desktop-v2 into the app, and under the fast pool's
// `isolate: false` it would leak the registration into sibling files.
import { desktopV2Layout } from '../declarations';

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
    expect(source).toContain("return 'desktop-v2';");
  });
});

describe('declared zones now drive the DOM (MOR-1263 step 2, MOR-1313)', () => {
  // Kills: declaring a surface this manifest does not name, or dropping
  // either one — the pair per-zone suppression consumes.
  it('declares receiver-deck:[vfo], rx-tx:[rxTx], tx-aux:[txAux], meters:[meters], '
    + 'scope-display:[scopeDisplay], filter:[filter], rf-front-end:[rfFrontEnd], '
    + 'band:[band], antenna:[antenna], rit-xit-scan:[ritXitScan], rx-audio:[rxAudio], '
    + 'dsp:[dsp], cw-keyer:[cwKeyer], memory:[memory] and scope-controls:[scopeControls]', () => {
    expect(desktopV2Layout.zones).toEqual([
      { id: 'receiver-deck', surfaces: ['vfo'] },
      { id: 'rx-tx', surfaces: ['rxTx'] },
      // MOR-1336 (S4): txAux became zone-owned. Declared, deliberately not required.
      { id: 'tx-aux', surfaces: ['txAux'] },
      // MOR-1341 (S5): meters became zone-owned too, retiring the legacy dock.
      { id: 'meters', surfaces: ['meters'] },
      // MOR-1365 (S6a): scopeDisplay became zone-owned too, retiring the
      // status bar's legacy scope indicator.
      { id: 'scope-display', surfaces: ['scopeDisplay'] },
      // MOR-1366 (S7): filter and rfFrontEnd became zone-owned, closing the
      // double-presentation defect on desktop-v2. Neither required.
      { id: 'filter', surfaces: ['filter'] },
      { id: 'rf-front-end', surfaces: ['rfFrontEnd'] },
      // MOR-1367 (S8): band, antenna and ritXit/scan. `band` retires only the
      // HAM half of `BandSelector` (S10 §4a); the other two retire their
      // sidebar/modal twins outright.
      { id: 'band', surfaces: ['band'] },
      { id: 'antenna', surfaces: ['antenna'] },
      { id: 'rit-xit-scan', surfaces: ['ritXitScan'] },
      // MOR-1368 (S9): the cross-sidebar family. `dsp` also retires `AgcPanel`
      // (DspSurface owns the AGC leaf, 5A/MOR-1290), and `cw-keyer` makes the
      // SAFETY-CRITICAL MOR-1310 surface the sole break-in affordance here.
      { id: 'rx-audio', surfaces: ['rxAudio'] },
      { id: 'dsp', surfaces: ['dsp'] },
      { id: 'cw-keyer', surfaces: ['cwKeyer'] },
      // MOR-2425 (Memory lane, phase B2): memory becomes zone-owned too,
      // closing the ledger entry `zone-ownership-coverage.test.ts` opened in
      // phase B1. No RadioViewModel group backs it — it mounts unconditionally.
      { id: 'memory', surfaces: ['memory'] },
      // MOR-1370 (S6b-2): scopeControls becomes zone-owned, the LAST surface
      // in the MOR-1262 vocabulary to graduate. Not required.
      { id: 'scope-controls', surfaces: ['scopeControls'] },
    ]);
    expect([...desktopV2Layout.requiredSemanticSurfaces].sort()).toEqual(['rxTx', 'vfo']);
  });

  // Kills: RadioLayout.svelte regressing to a per-skin-id gate — the exact
  // `skinId === 'sdr-test'` boolean MOR-1266 pinned as still-present and
  // MOR-1313 removed. A reintroduced id fork would leave this manifest
  // decorative again while every registry assertion above stayed green. Read
  // as TEXT because no DOM tree is needed.
  it('RadioLayout.svelte derives suppression from the manifest, not from a skin id', () => {
    const source = readFileSync('src/components-v2/layout/RadioLayout.svelte', 'utf8');
    expect(source).toContain('let declared = $derived(declaredSurfaces(getLayout(skinId)));');
    expect(source).not.toContain("$derived(skinId === 'sdr-test')");
  });

  // Kills: `declaredSurfaces` losing the zone walk (e.g. returning a fixed
  // set, or reading only the first zone) — desktop-v2 is the manifest that
  // splits the pair ACROSS two zones, so it is the one that proves the walk
  // is per-zone rather than per-manifest-first-zone.
  it('its zones flatten to the surfaces the shell suppresses legacy twins for', () => {
    expect([...declaredSurfaces(getLayout('desktop-v2'))].sort())
      .toEqual(['antenna', 'band', 'cwKeyer', 'dsp', 'filter', 'memory', 'meters', 'rfFrontEnd', 'ritXitScan', 'rxAudio', 'rxTx', 'scopeControls', 'scopeDisplay', 'txAux', 'vfo']);
  });
});

describe('MOR-1160 sizing axis — desktop-v2 stays fluid, mirroring sdr-test', () => {
  // Kills: silently switching desktop-v2 onto the fixed-native stage the LCD
  // family owns, or recording a breakpoint set that disagrees with
  // sdr-test's declaration for the identical shared RadioLayout stylesheet.
  it('declares fluid sizing with no breakpoints', () => {
    expect(desktopV2Layout.stageSizing).toEqual({ mode: 'fluid', responsiveBreakpoints: [] });
  });
});

describe('no fallback family', () => {
  // Kills: adding a fallbackLayoutId that has nothing to do — desktop-v2
  // declares every canonical topology and fluid sizing.
  it('declares no fallback', () => {
    expect(desktopV2Layout.fallbackLayoutId).toBeNull();
  });
});
