/**
 * MOR-1266 pin round (verify.md §1(d)/N2 disposition) — makes the
 * DOM-backed-vs-forward-declared split explicit and asserted, not just a file
 * comment on `desktop-declarations.ts`. Every registered manifest's zones
 * name either (a) a REAL mount some skin unconditionally renders today, or
 * (b) a target the DOM does not reach yet — today that is `desktop-v2` only
 * (MOR-1263 §3 "manifest first"; see `desktop-v2-registration.test.ts`).
 *
 * This file reads each skin's OWN source as TEXT, never imports it: the
 * `DOM_BACKED` probes below check for markers in each shell's own source
 * that a mounted or compiled `.svelte` component would not expose as
 * inspectable text.
 *
 * The expected forward-declared set below is a LITERAL, not a derived count:
 * a new manifest that lands still forward-declared must be added to it by
 * hand (silently landing an undeclared one fails `ALL_MANIFESTS`'s DOM_BACKED
 * lookup below), and a manifest gaining a real mount must remove it by hand.
 * Each test's doc line names the mutation it exists to kill.
 *
 * MOR-1313 EMPTIED IT. `desktop-v2` was the last entry: RadioLayout.svelte no
 * longer gates its semantic mount on a hardcoded skin id, it derives per-zone
 * suppression from the ACTIVE manifest's zone declarations — so `desktop-v2`'s
 * `receiver-deck: [vfo]` / `rx-tx: [rxTx]` zones are what the rendered tree is
 * built from, and both of the shared shell's families are DOM-backed. The
 * literal is now `[]` and this file's job flips from "keep the promise
 * honest" to "keep it at zero": any NEW forward-declared manifest must extend
 * the literal by hand and be argued for on its own ticket.
 */
import { readFileSync } from 'node:fs';
import { describe, it, expect } from 'vitest';
import type { LayoutManifest } from '../contract';
import { isLayoutManifest } from './manifest-guard';
// Barrel-only — the M7 lesson, restated on every family in this directory:
// importing a manifest module directly fires `registerLayout` from this file
// and, under the fast pool's `isolate: false`, leaks the registration into
// sibling files. Only `desktopV2Layout`/`sdrTestLayout` are referenced by
// name below (`sharedShellMounts`, and the last test's spread); every other
// manifest is reached only through the derived `ALL_MANIFESTS`.
import { desktopV2Layout, sdrTestLayout } from '../declarations';
// Namespace import of the SAME barrel, used ONLY to derive ALL_MANIFESTS
// structurally — never to register anything (a namespace import has no side
// effect beyond the module evaluation the named import above already
// triggers). NOT `listLayoutIds()`: the fast pool's `isolate: false` (see
// vite.config.ts) shares `contract.ts`'s module-scoped registry Map across
// every test file in the run, and sibling suites (`registry.test.ts`,
// `mobile-registration.test.ts`) register their own probe manifests into it
// — `listLayoutIds()` would make this file's inventory depend on cross-file
// execution order. The barrel's own export surface has no such cross-file
// state. Same derivation as `loader-identity-inventory.test.ts`'s
// `BARREL_MANIFESTS` (MOR-2060).
import * as layoutDeclarationsBarrel from '../declarations';

/** Every manifest currently registered by the barrel (mirrors
 *  `cockpit-topology-adaptation.test.ts`'s F8 `REAL_LAYOUTS`), derived
 *  from the barrel's own export surface instead of hand-listed
 *  (MOR-2060) — see the namespace-import comment above. */
const ALL_MANIFESTS: readonly LayoutManifest[] =
  Object.values(layoutDeclarationsBarrel).filter(isLayoutManifest);

/** The set this whole file exists to keep honest — see the header comment. */
const EXPECTED_FORWARD_DECLARED: readonly string[] = [];

const radioLayoutSource = readFileSync('src/components-v2/layout/RadioLayout.svelte', 'utf8');
const lcdLayoutSource = readFileSync('src/components-v2/layout/LcdLayout.svelte', 'utf8');
const mobileLayoutSource = readFileSync('src/components-v2/layout/MobileRadioLayout.svelte', 'utf8');
const cockpitShellSource = readFileSync('src/skins/dual-receiver-cockpit/DualReceiverCockpit.svelte', 'utf8');
const peerSplitShellSource = readFileSync('src/skins/segmentline/PeerSplitLayout.svelte', 'utf8');
const unifiedInstrumentShellSource = readFileSync('src/skins/lcd-unified-instrument/LcdUnifiedInstrumentSkin.svelte', 'utf8');
const panadapterFirstShellSource = readFileSync('src/skins/lcd-panadapter-first/LcdPanadapterFirstSkin.svelte', 'utf8');

/**
 * MOR-1313. `sdr-test` and `desktop-v2` share the one shell whose semantic
 * mount is now MANIFEST-driven: RadioLayout derives the suppressed set from
 * `declaredSurfaces(getLayout(skinId))` and mounts `<SemanticRadioSurfaces />`
 * exactly where the resolved layout declares the `vfo` surface. Two independent
 * halves must both hold for either family to be DOM-backed, and each is read
 * from its real source: the SHELL still resolving through the registry (text),
 * and the MANIFEST still declaring the surface (the registered object).
 *
 * Deliberately NOT `true`: a prober that ignores both halves would report a
 * DOM-backed inventory for a shell that had gone back to a hardcoded id, or for
 * a manifest that had dropped its VFO zone.
 *
 * MOR-2231 widened the mount pattern to accept ATTRIBUTES on the tag (the
 * `regions` prop, which decides only whether `vfo`/`rxTx` gain a zone element
 * — never whether the semantic vertical mounts). What this half asserts is
 * unchanged: the mount is the first thing inside `{#if semanticDeck}`, and
 * `semanticDeck` is the manifest-derived gate the line above pins.
 */
const shellResolvesThroughManifest =
  /let declared = \$derived\(declaredSurfaces\(getLayout\(skinId\)\)\);/.test(radioLayoutSource)
  && /\{#if semanticDeck\}\s*<SemanticRadioSurfaces[^>]*\/>/.test(radioLayoutSource);
const sharedShellMounts = (manifest: LayoutManifest): boolean =>
  shellResolvesThroughManifest && manifest.zones.some((z) => z.surfaces.includes('vfo'));

/**
 * Per-manifest DOM-backing proof, read off the ACTUAL skin source — never a
 * hardcoded boolean. Five dedicated shells each mount `SemanticRadioSurfaces`
 * outright (verified unconditional — not wrapped in any `{#if}` — by direct
 * reading: the original four in the MOR-1266 pin round, `peer-split` here in
 * MOR-2151).
 */
const DOM_BACKED: Readonly<Record<string, () => boolean>> = {
  'sdr-test': () => sharedShellMounts(sdrTestLayout),
  'desktop-v2': () => sharedShellMounts(desktopV2Layout),
  'lcd-cockpit': () => /<SemanticRadioSurfaces\s*\/>/.test(lcdLayoutSource),
  'lcd-scope': () => /<SemanticRadioSurfaces\s*\/>/.test(lcdLayoutSource),
  'mobile': () => /<SemanticRadioSurfaces\s*\/>/.test(mobileLayoutSource),
  'dual-receiver-cockpit': () => /<SemanticRadioSurfaces strips="dual"\s*\/>/.test(cockpitShellSource),
  // MOR-2151: PeerSplitLayout.svelte mounts the same dual-receiver
  // composition unconditionally, so its manifest-declared VFO/RX-TX
  // surfaces have a real DOM path rather than a forward declaration.
  'peer-split': () => /<SemanticRadioSurfaces(?=[^>]*\bstrips\s*=\s*"dual")[^>]*\/>/.test(peerSplitShellSource),
  'unified-instrument': () =>
    /<LcdLayout\s+variant="unified-instrument"\s*\/>/.test(unifiedInstrumentShellSource)
    && /<PeerSplitLayout[\s\S]*displayVariant=\{segmentlineDisplay\}/.test(lcdLayoutSource)
    && /<SemanticRadioSurfaces(?=[^>]*\bstrips\s*=\s*"dual")[^>]*\/>/.test(peerSplitShellSource),
  'panadapter-first': () =>
    /<LcdLayout\s+variant="panadapter-first"\s*\/>/.test(panadapterFirstShellSource)
    && /<PeerSplitLayout[\s\S]*displayVariant=\{segmentlineDisplay\}/.test(lcdLayoutSource)
    && /<SemanticRadioSurfaces(?=[^>]*\bstrips\s*=\s*"dual")[^>]*\/>/.test(peerSplitShellSource),
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
  // name it, and (b) `desktop-v2` LOSING the real mount MOR-1313 gave it —
  // RadioLayout going back to a hardcoded skin id, or the manifest dropping
  // its VFO zone — without this literal growing back. Either drift flips the
  // computed set against the literal.
  it(`the forward-declared set is exactly ${JSON.stringify(EXPECTED_FORWARD_DECLARED)}`, () => {
    const forwardDeclared = ALL_MANIFESTS
      .filter((m) => !DOM_BACKED[m.id]())
      .map((m) => m.id)
      .sort();
    expect(forwardDeclared).toEqual([...EXPECTED_FORWARD_DECLARED].sort());
  });

  // The negative case an empty-set assertion cannot distinguish from "every
  // prober returns true unconditionally" — this is the half that has to stay
  // meaningful now that the literal is `[]`. MOR-1313: desktop-v2 joined.
  it('every registered manifest is DOM-backed today', () => {
    for (const m of ALL_MANIFESTS) {
      expect(DOM_BACKED[m.id](), `${m.id} should be DOM-backed`).toBe(true);
    }
  });

  // Kills: peer-split's real dual-receiver mount losing the explicit
  // `strips="dual"` registration while another broad inventory condition
  // still happens to keep the expected set empty.
  it('discovers the registered peer-split shell as DOM-backed', () => {
    expect(DOM_BACKED['peer-split']()).toBe(true);
  });

  // Kills the two ways `sharedShellMounts` could go vacuously true: a shell
  // that stopped resolving through the manifest, and a manifest whose VFO zone
  // was dropped. Both halves are asserted independently, because with the
  // literal at `[]` an always-true prober would otherwise be invisible.
  it('the shared shell resolves its semantic mount through the manifest', () => {
    expect(shellResolvesThroughManifest).toBe(true);
    expect(sharedShellMounts({ ...desktopV2Layout, zones: [{ id: 'rx-tx', surfaces: ['rxTx'] }] }))
      .toBe(false);
  });
});
