/**
 * MOR-1093 — the sdr-test presentation entrypoint, registered as a v1 layout
 * manifest (MOR-1066) and resolved through the REAL registry, not a fixture
 * registry and not a stub loader.
 *
 * `registry.test.ts` already pins the bare registration fact ("the sdr-test
 * real registration proof") as MOR-1066's acceptance evidence. This file is
 * the entrypoint's OWN focused suite, mirroring the shape
 * `lcd-registration.test.ts` (MOR-1092) uses for its family: topology
 * honesty across all four canonical classes, the sizing axis, and — because
 * sdr-test has no sibling to fall back to — that it declares none. Every
 * claim is read back out of the shared registry rather than off the
 * exported object, so a manifest that is written but never registered fails
 * here. Each test's doc line names the mutation it exists to kill.
 */
import { readFileSync } from 'node:fs';
import { describe, it, expect } from 'vitest';
import {
  getLayout, resolveLayoutForTopology, resolveLayoutForViewport,
  TOPOLOGY_CLASSES,
} from '../contract';
// Deliberately through the shared aggregation entry, not `../declarations`'
// module-scope side effect alone: importing the named export pins that
// `declarations.ts` really registers `sdrTestLayout` (nothing else does),
// and keeps this fast-pool file on the SAME module entry point as
// `registry.test.ts` — under `isolate: false` a second entry into a module
// that registers at import time is how a split module graph (and a phantom
// "layout not registered") happens. See vite.config.ts #771.
import { sdrTestLayout } from '../declarations';

describe('the sdr-test entrypoint is registered in the real registry', () => {
  // Kills: declarations.ts defining the manifest but never calling
  // registerLayout — the resolution below would then read undefined.
  it('registers "sdr-test" under its stable entrypoint id', () => {
    expect(getLayout('sdr-test')).toBe(sdrTestLayout);
    expect(sdrTestLayout.id).toBe('sdr-test');
  });

  // Kills: a manifest id that drifts from the SkinId SdrTestSkin.svelte
  // actually passes to RadioLayout. The manifest is only an entrypoint
  // declaration if the two agree.
  it('shares its id with the skin registry loader SdrTestSkin.svelte resolves under', () => {
    const registrySource = readFileSync('src/skins/registry.ts', 'utf8');
    expect(registrySource).toMatch(/'sdr-test':\s*\(\)\s*=>\s*import\(['"]\.\/sdr-test\/SdrTestSkin\.svelte['"]\)/);
    const skinSource = readFileSync('src/skins/sdr-test/SdrTestSkin.svelte', 'utf8');
    expect(skinSource).toMatch(/skinId=["']sdr-test["']/);
  });

  // Kills: a manifest that declares no compiled loader at all. That the
  // loader reaches the REAL entrypoint — and renders the semantic surfaces
  // in place of the legacy VFO/TX block — is proved by mounting it in
  // `components-v2/layout/__tests__/semantic-desktop-migration.component.test.ts`
  // (MOR-1065); this file runs outside the DOM environment that whole tree
  // needs.
  it('declares a compiled loader', () => {
    expect(typeof sdrTestLayout.loader).toBe('function');
  });
});

describe('declared semantic zones (what the migrated entrypoint actually mounts)', () => {
  // Kills: declaring a surface the layout does not mount, or dropping rxTx
  // (which would let sdr-test keep the legacy sidebar TX panel as its only
  // TX owner).
  it('mounts vfo + rxTx in one zone', () => {
    expect(sdrTestLayout.zones).toEqual([{ id: 'main', surfaces: ['vfo', 'rxTx'] }]);
    expect([...sdrTestLayout.requiredSemanticSurfaces].sort()).toEqual(['rxTx', 'vfo']);
  });
});

describe('topology honesty', () => {
  // Kills: under-declaring the topology set. RadioLayout renders the deck
  // unconditionally and SemanticRadioSurfaces itself branches on the live
  // topology fixture (`semantic-desktop-migration.component.test.ts` proves
  // all four render safely), so every canonical class resolves to sdr-test
  // itself, never to a fallback.
  it('resolves itself on all four canonical topologies', () => {
    for (const topology of TOPOLOGY_CLASSES) {
      expect(resolveLayoutForTopology('sdr-test', topology)?.id).toBe('sdr-test');
    }
  });
});

describe('MOR-1160 sizing axis — sdr-test stays fluid', () => {
  // Kills: silently switching sdr-test onto the fixed-native stage the LCD
  // family owns — sdr-test is a reflowing desktop layout, not a native-scaled
  // instrument glass.
  it('declares fluid sizing with no breakpoints', () => {
    expect(sdrTestLayout.stageSizing).toEqual({ mode: 'fluid', responsiveBreakpoints: [] });
  });

  it('resolves on both a desktop and an iPhone-class portrait viewport — fluid never gates', () => {
    expect(resolveLayoutForViewport('sdr-test', { width: 1440, height: 900 })?.id).toBe('sdr-test');
    expect(resolveLayoutForViewport('sdr-test', { width: 390, height: 844 })?.id).toBe('sdr-test');
  });
});

describe('no fallback family', () => {
  // Kills: adding a fallbackLayoutId that was never part of sdr-test's
  // migration — sdr-test is not part of the LCD family and has nothing to
  // fall back to yet.
  it('declares no fallback', () => {
    expect(sdrTestLayout.fallbackLayoutId).toBeNull();
  });
});

describe('IC-specific fallback policy is out of the layout (MOR-1093)', () => {
  // Kills: reintroducing a hardcoded manufacturer-specific label/value table
  // (e.g. an IC-7610 attenuator-dB or AGC-label array) into the sdr-test
  // folder instead of sourcing it from capabilities. This scans the actual
  // files rather than asserting behavior, because the component this policy
  // lived in (SdrVfoScreen.svelte) is not currently mounted by any test —
  // see semantic-desktop-migration.component.test.ts for the proof that it
  // does not render at all.
  it('SdrVfoScreen carries no local manufacturer-specific value table', () => {
    const source = readFileSync('src/skins/sdr-test/SdrVfoScreen.svelte', 'utf8');
    expect(source).not.toMatch(/const ATT_DB/);
    expect(source).not.toMatch(/const AGC_LABELS/);
    expect(source).not.toMatch(/IC-7610 ATT levels/);
    expect(source).not.toMatch(/IC-7610 AGC:/);
    // The two labels it used to hardcode are now capability-sourced.
    expect(source).toMatch(/getAttValues\(\)/);
    expect(source).toMatch(/getAgcLabels\(\)/);
  });
});
