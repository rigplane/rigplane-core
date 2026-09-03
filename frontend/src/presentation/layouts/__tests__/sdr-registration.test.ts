/**
 * MOR-1093 — the sdr-test presentation entrypoint, registered as a v1 layout
 * manifest (MOR-1066) and resolved through the REAL registry, not a fixture
 * registry and not a stub loader.
 *
 * `registry.test.ts` already pins the bare registration fact ("the sdr-test
 * real registration proof") as MOR-1066's acceptance evidence. This file is
 * the entrypoint's OWN focused suite, mirroring the shape
 * `lcd-registration.test.ts` (MOR-1092) uses for its family: the sizing
 * axis, and — because sdr-test has no sibling to fall back to — that it
 * declares none. Every claim is read back out of the shared registry rather
 * than off the exported object, so a manifest that is written but never
 * registered fails here. Each test's doc line names the mutation it exists
 * to kill.
 */
import { readFileSync } from 'node:fs';
import { describe, it, expect } from 'vitest';
import { getLayout } from '../contract';
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
  // MOR-2231 (step 1, batch 1): the pair is split across the two zone ids
  // `desktop-v2` already uses, so both faces name the same hosts and
  // `SemanticRadioSurfaces` can build a real element for each.
  // Kills: declaring a surface the layout does not mount, dropping rxTx
  // (which would let sdr-test keep the legacy sidebar TX panel as its only
  // TX owner), or folding the pair back into one zone.
  it('mounts vfo and rxTx in their own zones', () => {
    expect(sdrTestLayout.zones).toContainEqual({ id: 'receiver-deck', surfaces: ['vfo'] });
    expect(sdrTestLayout.zones).toContainEqual({ id: 'rx-tx', surfaces: ['rxTx'] });
    expect([...sdrTestLayout.requiredSemanticSurfaces].sort()).toEqual(['rxTx', 'vfo']);
  });

  // MOR-1346: `meters` joins as its own zone (the desktop-v2/MOR-1341 shape),
  // which is what lets RadioLayout's existing `semanticMeters` gate retire
  // the legacy `<MetersDockPanel>` here too. Kills: folding `meters` into
  // another zone (a persisted visibility preference recorded for that zone
  // before `meters` joined it could then silently hide it) or leaving it
  // undeclared again.
  it('mounts meters in its own zone, not required', () => {
    expect(sdrTestLayout.zones).toContainEqual({ id: 'meters', surfaces: ['meters'] });
    expect(sdrTestLayout.requiredSemanticSurfaces).not.toContain('meters');
  });

  // MOR-2231 (step 1, batch 2) — the five control families, each alone in a
  // zone carrying the id `desktop-declarations.ts` already uses.
  //
  // Kills: declaring one of the five under a DRIFTED zone id, folding two of
  // them into one zone, dropping one, or making one `required`. The id drift
  // is the mutation worth a dedicated pin, because it is the one the
  // suppression channel cannot catch: `LeftSidebar`/`RadioLayout` retire the
  // legacy twins on `declared.has(<surface>)`, which reads the SURFACE name
  // and never the zone id — so a drifted id would still retire the twin while
  // naming a host no arrangement can bind, and the face would lose the panel
  // without gaining a placed surface.
  it.each([
    ['filter', 'filter'],
    ['rfFrontEnd', 'rf-front-end'],
    ['band', 'band'],
    ['antenna', 'antenna'],
    ['ritXitScan', 'rit-xit-scan'],
    // MOR-2231 (step 1, batch 3) — the right column's four, same shape. The id
    // drift argument above applies unchanged to `rxAudio`/`dsp`/`cwKeyer`. It
    // does NOT apply to `txAux`: no `declared.has('txAux')` predicate exists,
    // so a drifted id there loses the host without retiring anything, and this
    // row is the only guard that would catch it.
    ['rxAudio', 'rx-audio'],
    ['dsp', 'dsp'],
    ['cwKeyer', 'cw-keyer'],
    ['txAux', 'tx-aux'],
  ] as const)('mounts %s alone in the stable `%s` zone, not required', (surface, zoneId) => {
    const owning = sdrTestLayout.zones.filter((z) => z.surfaces.includes(surface));
    expect(owning).toHaveLength(1);
    expect(owning[0]).toEqual({ id: zoneId, surfaces: [surface] });
    expect(sdrTestLayout.requiredSemanticSurfaces).not.toContain(surface);
  });
});

describe('MOR-1160 sizing axis — sdr-test stays fluid', () => {
  // Kills: silently switching sdr-test onto the fixed-native stage the LCD
  // family owns — sdr-test is a reflowing desktop layout, not a native-scaled
  // instrument glass.
  it('declares fluid sizing with no breakpoints', () => {
    expect(sdrTestLayout.stageSizing).toEqual({ mode: 'fluid', responsiveBreakpoints: [] });
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
