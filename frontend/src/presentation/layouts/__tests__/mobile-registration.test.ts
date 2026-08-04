/**
 * MOR-1094 — the mobile presentation entrypoint as a v1 layout manifest
 * (schema, validator and registry: `../contract`, MOR-1066), mirroring the
 * MOR-1092 LCD registration proof.
 *
 * What this file is for: the manifest is the only place the mobile entrypoint
 * declares its identity, the semantic zone it mounts, the topologies it
 * survives, and its MOR-1160 sizing assignment. Mobile is the FLUID side of
 * that axis — the counterpart to the LCD's fixed-native stage — and it is the
 * one layout a portrait phone can always resolve, which is what makes it the
 * only viable destination for a fixed-native layout's fallback hop off that
 * viewport. Every claim below is read back out of the shared registry rather
 * than off the exported object, so a manifest that is written but never
 * registered fails here. Each test's doc line names the mutation it kills.
 */
import { readFileSync } from 'node:fs';
import { describe, it, expect } from 'vitest';
import {
  getLayout, registerLayout, resolveLayoutForTopology, resolveLayoutForViewport,
  fitsViewport, TOPOLOGY_CLASSES, type LayoutManifest,
} from '../contract';
// Deliberately through the shared aggregation entry, not `../mobile-declarations`
// directly: it pins that `declarations.ts` really pulls the mobile family in
// (nothing else does), and it keeps this fast-pool file on the SAME module
// entry point as `registry.test.ts` / `lcd-registration.test.ts` — under
// `isolate: false` a second entry into a module that registers at import time
// is how a split module graph (and a phantom "layout not registered") happens.
import { mobileLayout, lcdCockpitLayout, lcdScopeLayout } from '../declarations';

/** iPhone-class portrait — the viewport the LCD family fails arithmetically. */
const PORTRAIT_MOBILE = { width: 390, height: 844 };
/** The same handset rotated: what `isLandscape` in MobileRadioLayout sees. */
const LANDSCAPE_MOBILE = { width: 844, height: 390 };

describe('the mobile entrypoint is registered in the real registry', () => {
  // Kills: mobile-declarations.ts defining the manifest but never calling
  // registerLayout — every resolution below would then read undefined.
  it('registers "mobile" under its stable entrypoint id', () => {
    expect(getLayout('mobile')).toBe(mobileLayout);
    expect(mobileLayout.id).toBe('mobile');
  });

  // Kills: a manifest id that drifts from the SkinId the App actually loads.
  // The manifest is only an entrypoint declaration if the two agree — a
  // "mobile-v2" or "phone" manifest would register cleanly and address nothing.
  it('uses the same id the skin registry loads the mobile entrypoint under', () => {
    const source = readFileSync('src/skins/registry.ts', 'utf8');
    const start = source.indexOf('const SKIN_LOADERS');
    const loaders = source.slice(start, source.indexOf('};', start));
    expect(loaders).toContain("'mobile':");
    // And it is the id `resolveSkinId` hands back for a mobile viewport.
    expect(source).toContain("if (ctx.isMobile) return 'mobile';");
  });

  // Kills: a manifest that declares no compiled loader at all. That the
  // loader reaches the REAL entrypoint — and renders the migrated mobile
  // shell — is proved by mounting it in
  // `components-v2/layout/__tests__/semantic-mobile-migration.component.test.ts`;
  // this file runs outside the DOM environment that whole tree needs.
  it('declares a compiled loader', () => {
    expect(typeof mobileLayout.loader).toBe('function');
  });
});

describe('declared semantic zone (what the migrated mobile shell mounts)', () => {
  // Kills: declaring a surface the layout does not mount, or dropping rxTx
  // (which would leave the PTT FAB as mobile's only RX/TX truth).
  it('mounts vfo + rxTx in one portrait deck zone', () => {
    expect(mobileLayout.zones).toEqual([{ id: 'portrait-deck', surfaces: ['vfo', 'rxTx'] }]);
    expect([...mobileLayout.requiredSemanticSurfaces].sort()).toEqual(['rxTx', 'vfo']);
  });
});

describe('topology honesty', () => {
  // Kills: under-declaring the topology set. The mobile shell renders VFO A
  // unconditionally and gates the MAIN/SUB receiver selector and the SUB
  // readout on `hasDualReceiver`, so every canonical class resolves to the
  // layout itself, never to a fallback.
  it.each(TOPOLOGY_CLASSES)('resolves itself on the %s topology', (topology) => {
    expect(resolveLayoutForTopology('mobile', topology)?.id).toBe('mobile');
  });
});

describe('MOR-1160 sizing axis — mobile is the fluid side', () => {
  // Kills: declaring mobile `fixed-native`. Mobile chrome reflows; it is not
  // an instrument stage scaled as one letterboxed block, and a native stage
  // here would make the phone shell fail its own minScale gate.
  it('declares fluid sizing with the one breakpoint the layout implements', () => {
    expect(mobileLayout.sizing).toEqual({ mode: 'fluid', responsiveBreakpoints: [500] });
  });

  // Kills: flipping mobile to a mode with a viewport gate. `fluid` always
  // fits (contract: breakpoints are reflow hints, not a hard gate in v1), and
  // that is precisely why mobile is resolvable where the LCD stage is not.
  it.each([
    ['portrait phone', PORTRAIT_MOBILE],
    ['landscape phone', LANDSCAPE_MOBILE],
    ['desktop', { width: 1440, height: 900 }],
  ])('resolves itself on a %s viewport', (_label, viewport) => {
    expect(fitsViewport(mobileLayout, viewport)).toBe(true);
    expect(resolveLayoutForViewport('mobile', viewport)?.id).toBe('mobile');
  });

  // Kills: giving mobile a fallback it does not need. Mobile is the terminal
  // destination — it fits every viewport, so a hop off it is unreachable code
  // and a hop TO somewhere else would hide a real resolution failure.
  it('declares no fallback, because it never fails a viewport', () => {
    expect(mobileLayout.fallbackLayoutId).toBeNull();
  });
});

describe('portrait-mobile exclusion of fixed-native layouts, and the hop off it', () => {
  // The exclusion case this fallback machinery exists for, asserted read-only
  // against the LCD family (MOR-1092 owns those manifests). An iPhone-class
  // 390x844 achieves min(390/1280, 844/540) ~= 0.30 against minScale 0.5.
  it.each([
    ['lcd-cockpit', lcdCockpitLayout],
    ['lcd-scope', lcdScopeLayout],
  ])('excludes fixed-native "%s" from portrait mobile while mobile fits', (_id, manifest) => {
    expect(fitsViewport(manifest, PORTRAIT_MOBILE)).toBe(false);
    expect(fitsViewport(mobileLayout, PORTRAIT_MOBILE)).toBe(true);
  });

  // The LCD family names only its own sibling, which shares the same native
  // stage — so it correctly dead-ends at `undefined` rather than handing back
  // a layout that fails the same gate (MOR-1066 review cycle 1, F1). Pinned
  // here so registering a fluid `mobile` alongside it cannot be mistaken for
  // having silently changed LCD resolution.
  it('leaves the LCD family unresolvable on portrait mobile (no LCD fallback rewiring)', () => {
    expect(resolveLayoutForViewport('lcd-scope', PORTRAIT_MOBILE)).toBeUndefined();
    expect(resolveLayoutForViewport('lcd-cockpit', PORTRAIT_MOBILE)).toBeUndefined();
  });

  // Kills: the registration that looks right but cannot actually terminate a
  // hop — a `mobile` manifest that itself failed the viewport, or a validator
  // that returned the fallback WITHOUT re-applying the criterion. Registering
  // a probe is the count-agnostic idiom `registry.test.ts` already uses.
  it('is a viable fallback destination: a fixed-native layout hops to it off portrait mobile', () => {
    const probe: LayoutManifest = {
      schemaVersion: 1,
      id: 'mobile-hop-probe',
      displayName: 'Mobile Hop Probe',
      loader: mobileLayout.loader,
      zones: [{ id: 'main', surfaces: ['vfo', 'rxTx'] }],
      compatibleTopologies: ['1/single'],
      requiredSemanticSurfaces: ['vfo', 'rxTx'],
      sizing: { mode: 'fixed-native', nativeW: 1280, nativeH: 540, minScale: 0.5 },
      fallbackLayoutId: 'mobile',
    };
    registerLayout(probe);

    // Fails its own gate on the phone, and the ONE validated hop lands on
    // mobile — which passes the same criterion it was fetched to satisfy.
    expect(fitsViewport(probe, PORTRAIT_MOBILE)).toBe(false);
    expect(resolveLayoutForViewport('mobile-hop-probe', PORTRAIT_MOBILE)?.id).toBe('mobile');
    // On a viewport it fits, the probe still resolves to itself — the hop is
    // a fallback, not an unconditional redirect to mobile.
    expect(resolveLayoutForViewport('mobile-hop-probe', { width: 1440, height: 900 })?.id)
      .toBe('mobile-hop-probe');
  });
});
