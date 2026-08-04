/**
 * MOR-1266 pin round (verify.md §1(d)/N2 disposition) — makes the
 * DOM-backed-vs-forward-declared split explicit and asserted, not just a file
 * comment on `desktop-declarations.ts`. Every registered manifest's zones
 * name either (a) a REAL mount some skin unconditionally renders today, or
 * (b) a target the DOM does not reach yet — today that is `desktop-v2` only
 * (MOR-1263 §3 "manifest first"; see `desktop-v2-registration.test.ts`).
 *
 * This file reads each skin's OWN source as TEXT, never imports it — the
 * same reason `cockpit-topology-adaptation.test.ts`'s F8 rule and every
 * other module-specifier pin in this suite do: several of these skins
 * transitively import `lib/stores/layout.svelte.ts`, whose module-scope
 * `localStorage` read throws outside a DOM environment.
 *
 * The expected forward-declared set below is a LITERAL, not a derived count:
 * a new manifest that lands still forward-declared must be added to it by
 * hand (silently landing an undeclared one fails `ALL_MANIFESTS`'s DOM_BACKED
 * lookup below), and `desktop-v2` gaining a real mount must remove it by hand
 * (its DOM-backing proof flips to `true`, so the computed set stops matching
 * the literal and the test fails until the literal is edited down to `[]`).
 * Each test's doc line names the mutation it exists to kill.
 */
import { readFileSync } from 'node:fs';
import { describe, it, expect } from 'vitest';
import type { LayoutManifest } from '../contract';
// Barrel-only — the M7 lesson, restated on every family in this directory:
// importing a manifest module directly fires `registerLayout` from this file
// and, under the fast pool's `isolate: false`, leaks the registration into
// sibling files.
import {
  desktopV2Layout, dualReceiverCockpitLayout, lcdCockpitLayout, lcdScopeLayout, mobileLayout,
  sdrTestLayout,
} from '../declarations';

/** Every manifest currently registered by the barrel (mirrors F8's `REAL_LAYOUTS`). */
const ALL_MANIFESTS: readonly LayoutManifest[] = [
  sdrTestLayout, dualReceiverCockpitLayout, lcdCockpitLayout, lcdScopeLayout, mobileLayout,
  desktopV2Layout,
];

/** The set this whole file exists to keep honest — see the header comment. */
const EXPECTED_FORWARD_DECLARED: readonly string[] = ['desktop-v2'];

const radioLayoutSource = readFileSync('src/components-v2/layout/RadioLayout.svelte', 'utf8');
const lcdLayoutSource = readFileSync('src/components-v2/layout/LcdLayout.svelte', 'utf8');
const mobileLayoutSource = readFileSync('src/components-v2/layout/MobileRadioLayout.svelte', 'utf8');
const cockpitShellSource = readFileSync('src/skins/dual-receiver-cockpit/DualReceiverCockpit.svelte', 'utf8');

/** `sdr-test` and `desktop-v2` share this one conditional gate. */
const semanticGateExpr = radioLayoutSource.match(/let semanticSurfaces = \$derived\(([^;]*)\);/)?.[1] ?? '';

/**
 * Per-manifest DOM-backing proof, read off the ACTUAL skin source — never a
 * hardcoded boolean. `sdr-test` and `desktop-v2` are decided by whether their
 * id appears inside RadioLayout.svelte's single `semanticSurfaces` gate
 * expression; the other four each mount `SemanticRadioSurfaces` outright in
 * their own dedicated shell (verified unconditional — not wrapped in any
 * `{#if}` — by direct reading, MOR-1266 pin round).
 */
const DOM_BACKED: Readonly<Record<string, () => boolean>> = {
  'sdr-test': () => semanticGateExpr.includes("'sdr-test'"),
  'desktop-v2': () => semanticGateExpr.includes("'desktop-v2'"),
  'lcd-cockpit': () => /<SemanticRadioSurfaces\s*\/>/.test(lcdLayoutSource),
  'lcd-scope': () => /<SemanticRadioSurfaces\s*\/>/.test(lcdLayoutSource),
  'mobile': () => /<SemanticRadioSurfaces\s*\/>/.test(mobileLayoutSource),
  'dual-receiver-cockpit': () => /<SemanticRadioSurfaces strips="dual"\s*\/>/.test(cockpitShellSource),
};

describe('forward-declared vs DOM-backed manifest inventory (verify.md N2)', () => {
  // Kills: a new manifest landing without a corresponding DOM_BACKED prober
  // above — it would otherwise be silently absent from the inventory instead
  // of failing loudly here.
  it('every registered manifest has an asserted DOM-backing proof', () => {
    for (const m of ALL_MANIFESTS) {
      expect(Object.hasOwn(DOM_BACKED, m.id), `no DOM-backing proof registered for "${m.id}"`).toBe(true);
    }
  });

  // The actual inventory. Kills BOTH directions of drift: (a) a new
  // forward-declared manifest landing without this literal being extended to
  // name it, and (b) desktop-v2 gaining a real mount (RadioLayout's gate
  // widening — already independently pinned RED by
  // `desktop-v2-registration.test.ts`'s own copy of this mutation — or
  // DesktopSkin.svelte switching entrypoints) without this literal shrinking
  // back to `[]`. Either drift flips the computed set against the literal.
  it(`the forward-declared set is exactly ${JSON.stringify(EXPECTED_FORWARD_DECLARED)}`, () => {
    const forwardDeclared = ALL_MANIFESTS
      .filter((m) => !DOM_BACKED[m.id]())
      .map((m) => m.id)
      .sort();
    expect(forwardDeclared).toEqual([...EXPECTED_FORWARD_DECLARED].sort());
  });

  // The negative case the positive assertion alone would not distinguish
  // from "everything is forward-declared" — kills DOM_BACKED probers that
  // always return false regardless of what they read.
  it('sdr-test, both LCD variants, mobile and the cockpit are DOM-backed today', () => {
    for (const id of ['sdr-test', 'lcd-cockpit', 'lcd-scope', 'mobile', 'dual-receiver-cockpit']) {
      expect(DOM_BACKED[id](), `${id} should be DOM-backed`).toBe(true);
    }
  });
});
