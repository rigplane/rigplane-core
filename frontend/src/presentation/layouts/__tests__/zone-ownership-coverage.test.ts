/**
 * MOR-1317 CLOSURE — every semantic surface is either zone-owned on
 * `desktop-v2` or carries a RECORDED REASON why it is not.
 *
 * MOR-1317 is the defect "a surface exists in the vocabulary but nothing
 * decides whether the flagship skin owns it", and the rework tail closes it
 * family by family (S6a scopeDisplay, S7 filter/rfFrontEnd, S8
 * band/antenna/ritXitScan, S9 rxAudio/dsp/cwKeyer). What made it a defect
 * rather than a backlog item is that the gap was invisible: a surface could be
 * added to `SEMANTIC_SURFACE_NAMES`, wired, shipped — and double-presented
 * beside its legacy twin forever — without any test noticing that nobody had
 * decided where it lives.
 *
 * This file is the decision ledger, and it is EXHAUSTIVE BY CONSTRUCTION: the
 * declared set is derived from the real manifest, the excused set is a hand-
 * written literal, and the two must partition `SEMANTIC_SURFACE_NAMES`
 * exactly. Appending a fourteenth-plus surface name therefore fails here until
 * someone either declares a zone for it or writes down why not — which is the
 * whole point. The reason strings are not decoration: they are what the next
 * slice reads instead of guessing.
 *
 * Deliberately NOT a claim that every surface belongs on desktop-v2. Two of
 * the recorded reasons are permanent-by-design; the rest name their slice.
 */
import { describe, it, expect } from 'vitest';
import {
  SEMANTIC_SURFACE_NAMES, declaredSurfaces, type SemanticSurfaceName,
} from '../contract';
import { desktopV2Layout } from '../declarations';
import { readFileSync } from 'node:fs';

/** Derived, never hand-listed: what `desktop-v2` actually declares today. */
const OWNED = declaredSurfaces(desktopV2Layout);

/**
 * The complement, with the decision that put each name here. A surface leaves
 * this map only by gaining a `desktop-v2` zone in the same commit.
 */
const RECORDED_REASONS: Partial<Record<SemanticSurfaceName, string>> = {
  filter:
    'S7/MOR-1366 declares { id: "filter", surfaces: ["filter"] }; not yet landed on this base.',
  rfFrontEnd:
    'S7/MOR-1366 declares { id: "rf-front-end", … }; not yet landed on this base.',
  band:
    'S8/MOR-1367. The BAND twin is NOT a plain retirement: `BandSelector` also hosts the '
    + 'LW/MW + SWL tabs and 17 broadcast presets, which are deliberately not facts and have no '
    + 'other production host (S10 row 10, permanent). S8 retires the HAM half by a `hamBands` '
    + 'prop, never by unmounting the section.',
  antenna: 'S8/MOR-1367 declares { id: "antenna", surfaces: ["antenna"] }.',
  ritXitScan: 'S8/MOR-1367 declares { id: "rit-xit-scan", … } for the RIT/XIT + SCAN pair.',
  scopeControls:
    'S6b-2. 11B/MOR-1311 built the surface; the scope toolbar it replaces is not on the '
    + 'MOR-1364 suppression channel yet, so declaring the zone today would double-present it.',
};

describe('MOR-1317 — every semantic surface has a desktop-v2 decision', () => {
  /**
   * THE CLOSURE PIN. Owned ∪ excused === the whole vocabulary, with no
   * overlap. A new `SEMANTIC_SURFACE_NAMES` member fails this test until it is
   * either declared or excused in writing — which is the gap MOR-1317 names.
   */
  it('partitions SEMANTIC_SURFACE_NAMES into zone-owned and recorded-reason, exactly', () => {
    const excused = Object.keys(RECORDED_REASONS) as SemanticSurfaceName[];
    const covered = [...OWNED, ...excused].sort();
    expect(covered).toEqual([...SEMANTIC_SURFACE_NAMES].sort());
  });

  // Kills: "closing" the gap by excusing a surface that is in fact declared,
  // which would leave a stale reason in the ledger the next slice would trust.
  it('no surface is both zone-owned and excused', () => {
    const both = (Object.keys(RECORDED_REASONS) as SemanticSurfaceName[]).filter((s) => OWNED.has(s));
    expect(both).toEqual([]);
  });

  // Kills: an empty or placeholder reason. The ledger is only useful if the
  // entry says something a later reader can act on.
  it.each(Object.entries(RECORDED_REASONS))('%s records a substantive reason', (_surface, reason) => {
    expect(reason!.length).toBeGreaterThan(30);
    expect(reason).toMatch(/S\d|permanent|MOR-\d+/);
  });

  /**
   * NO ZONE-MOUNT IS A NO-OP — the second half of the MOR-1317 closure. A
   * manifest may only declare a surface the single composition can actually
   * mount; declaring one `SemanticRadioSurfaces` never renders would produce a
   * zone id in the plan with no DOM behind it (an "empty promise" zone,
   * MOR-1069), and every suppression pin for its legacy twin would then be a
   * pure regression: the twin gone, nothing in its place.
   *
   * Read as TEXT rather than mounted for the same reason as the loader pin in
   * `desktop-v2-registration.test.ts` — the component's import graph reaches
   * `localStorage` at module scope.
   */
  it.each([...OWNED].sort())('%s has a real mount in the single composition', (surface) => {
    const source = readFileSync('src/components-v2/wiring/SemanticRadioSurfaces.svelte', 'utf8');
    // `vfo`/`rxTx` ride the `singleOrder` {#each} rather than `zoned()`; every
    // other owned surface must have its own `zoned('<name>', …)` call.
    if (surface === 'vfo' || surface === 'rxTx') {
      expect(source).toContain(`surface === '${surface}'`);
    } else {
      expect(source).toContain(`zoned('${surface}'`);
    }
  });
});
