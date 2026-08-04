/**
 * MOR-1092 — the two LCD/scope presentation entrypoints registered as v1
 * layout manifests and resolved through the REAL registry (MOR-1066), not a
 * fixture registry and not a stub loader.
 *
 * What this file is for: the manifest is the only place the LCD entrypoints
 * declare their identity, the semantic zones they mount, the topologies they
 * survive, and the MOR-1160 sizing axis. Every claim below is read back out
 * of the shared registry rather than off the exported object, so a manifest
 * that is written but never registered fails here. Each test's doc line names
 * the mutation it exists to kill.
 */
import { readFileSync } from 'node:fs';
import { describe, it, expect } from 'vitest';
import {
  getLayout, resolveLayoutForTopology, resolveLayoutForViewport,
  TOPOLOGY_CLASSES, type LayoutManifest,
} from '../contract';
// Deliberately through the shared aggregation entry, not `../lcd-declarations`
// directly: it pins that `declarations.ts` really pulls the LCD family in
// (nothing else does), and it keeps this fast-pool file on the SAME module
// entry point as `registry.test.ts` — under `isolate: false` a second entry
// into a module that registers at import time is how a split module graph
// (and a phantom "layout not registered") happens. See vite.config.ts #771.
import { lcdCockpitLayout, lcdScopeLayout } from '../declarations';

/** The pair under migration, by the id the App already resolves them under. */
const LCD_LAYOUTS: readonly (readonly [string, LayoutManifest])[] = [
  ['lcd-cockpit', lcdCockpitLayout],
  ['lcd-scope', lcdScopeLayout],
];

/** iPhone-class portrait: min(390/1280, 844/540) ≈ 0.30, below minScale. */
const PORTRAIT_MOBILE = { width: 390, height: 844 };

describe('the LCD entrypoints are registered in the real registry', () => {
  // Kills: lcd-declarations.ts defining the manifests but never calling
  // registerLayout — every resolution below would then read undefined.
  it.each(LCD_LAYOUTS)('registers "%s" under its stable entrypoint id', (id, manifest) => {
    expect(getLayout(id)).toBe(manifest);
    expect(manifest.id).toBe(id);
  });

  // Kills: a manifest id that drifts from the SkinId the App actually loads.
  // The manifest is only an entrypoint declaration if the two agree — a
  // "lcd" or "amber-lcd" manifest would register cleanly and address nothing.
  it('covers every LCD entrypoint the skin registry can load, under the same ids', () => {
    const source = readFileSync('src/skins/registry.ts', 'utf8');
    const start = source.indexOf('const SKIN_LOADERS');
    const loaders = source.slice(start, source.indexOf('};', start));
    const lcdSkinIds = [...loaders.matchAll(/'(lcd-[a-z-]+)':/g)].map((m) => m[1]).sort();
    expect(lcdSkinIds).toEqual(['lcd-cockpit', 'lcd-scope']);
    for (const skinId of lcdSkinIds) expect(getLayout(skinId)).toBeDefined();
  });

  // Kills: a manifest that declares no compiled loader at all. That the
  // loader reaches the REAL entrypoint — and renders the migrated LCD — is
  // proved by mounting it in
  // `components-v2/layout/__tests__/semantic-lcd-migration.component.test.ts`;
  // this file runs outside the DOM environment that whole tree needs.
  it.each(LCD_LAYOUTS)('"%s" declares a compiled loader', (_id, manifest) => {
    expect(typeof manifest.loader).toBe('function');
  });
});

describe('declared semantic zones (what the migrated LCD actually mounts)', () => {
  // Kills: declaring a surface the layout does not mount, or dropping rxTx
  // (which would let the LCD keep the legacy TX panel as its only TX owner).
  it.each(LCD_LAYOUTS)('"%s" mounts vfo + rxTx in one control zone', (_id, manifest) => {
    expect(manifest.zones).toEqual([{ id: 'control-column', surfaces: ['vfo', 'rxTx'] }]);
    expect([...manifest.requiredSemanticSurfaces].sort()).toEqual(['rxTx', 'vfo']);
  });
});

describe('topology honesty', () => {
  // Kills: under-declaring the topology set. Both LCD variants render VFO A
  // unconditionally and gate the second receiver on `hasDualReceiver`, so
  // every canonical class resolves to the layout itself, never to a fallback.
  it.each(LCD_LAYOUTS)('"%s" resolves itself on all four canonical topologies', (id, _manifest) => {
    for (const topology of TOPOLOGY_CLASSES) {
      expect(resolveLayoutForTopology(id, topology)?.id).toBe(id);
    }
  });
});

describe('MOR-1160 sizing axis — the LCD is the fixed-native archetype', () => {
  // Kills: leaving the LCD on `fluid`, or drifting off the native stage size
  // MOR-1160 froze for the incoming LCD directions (1280x540).
  it.each(LCD_LAYOUTS)('"%s" declares the frozen fixed-native stage', (_id, manifest) => {
    expect(manifest.sizing).toEqual({
      mode: 'fixed-native', nativeW: 1280, nativeH: 540, minScale: 0.5,
    });
  });

  it.each(LCD_LAYOUTS)('"%s" resolves on a desktop viewport', (id) => {
    expect(resolveLayoutForViewport(id, { width: 1440, height: 900 })?.id).toBe(id);
    expect(resolveLayoutForViewport(id, { width: 1280, height: 540 })?.id).toBe(id);
  });

  // Kills: minScale set to 0 (or the mode flipped to fluid), which would let
  // a fixed-native LCD resolve on portrait mobile. MOR-1160 constraint 4:
  // the exclusion is arithmetic, never a mobile-detection branch.
  it.each(LCD_LAYOUTS)('"%s" is excluded from portrait mobile arithmetically', (id) => {
    expect(resolveLayoutForViewport(id, PORTRAIT_MOBILE)).toBeUndefined();
  });
});

describe('fallback behaviour inside the LCD family', () => {
  // The scope variant names the cockpit as its single hop — the cockpit is
  // the family's universal variant (the persisted `amber-lcd` alias already
  // routes there). The cockpit itself has nowhere left to go.
  it('declares the cockpit as the scope variant\'s one fallback hop', () => {
    expect(lcdScopeLayout.fallbackLayoutId).toBe('lcd-cockpit');
    expect(lcdCockpitLayout.fallbackLayoutId).toBeNull();
    expect(getLayout(lcdScopeLayout.fallbackLayoutId!)).toBe(lcdCockpitLayout);
  });

  // Kills: returning the fallback without re-applying the criterion (the
  // MOR-1066 F1 bug), applied to the real LCD pair. Both variants share one
  // native stage, so a viewport that fails the scope variant fails the
  // cockpit too — resolution must report "unresolvable", not hand back a
  // sibling that fails the same gate.
  it('does not hand back the cockpit for a viewport that fails it too', () => {
    expect(resolveLayoutForViewport('lcd-scope', PORTRAIT_MOBILE)).toBeUndefined();
  });

  // The fallback is a real hop, not decoration: it resolves when the
  // criterion is satisfiable.
  it('resolves the scope variant itself while the viewport fits', () => {
    expect(resolveLayoutForViewport('lcd-scope', { width: 1440, height: 900 })?.id).toBe('lcd-scope');
  });
});
