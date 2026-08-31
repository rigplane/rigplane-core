/**
 * MOR-2035/MOR-2034 — the `accept-probe` acceptance-experiment entrypoint,
 * registered as a v1 layout manifest (`accept-probe-declarations.ts`) and
 * resolved through the REAL registry, mirroring the shape
 * `sdr-registration.test.ts` uses for `sdr-test` (named by
 * `docs/architecture/building-a-skin.md`'s "Wiring a new skin into the app"
 * step 3 as the convention to follow).
 *
 * Unlike `sdr-test`, `accept-probe` does not delegate into `RadioLayout`
 * with a `skinId` prop — it is its own dedicated layout component — so the
 * "shares its id with the skin registry loader" check here confirms the
 * `registry.ts` loader import path instead of a `skinId=` attribute.
 */
import { readFileSync } from 'node:fs';
import { describe, it, expect } from 'vitest';
import {
  getLayout, resolveLayoutForTopology, resolveLayoutForViewport,
  TOPOLOGY_CLASSES,
} from '../contract';
// Deliberately through the shared aggregation entry, not
// `../accept-probe-declarations`' module-scope side effect alone — see
// `sdr-registration.test.ts`'s identical comment for why (vite.config.ts #771).
import { acceptProbeLayout } from '../declarations';

describe('the accept-probe entrypoint is registered in the real registry', () => {
  // Kills: accept-probe-declarations.ts defining the manifest but never
  // calling registerLayout — the resolution below would then read undefined.
  it('registers "accept-probe" under its stable entrypoint id', () => {
    expect(getLayout('accept-probe')).toBe(acceptProbeLayout);
    expect(acceptProbeLayout.id).toBe('accept-probe');
  });

  // Kills: a manifest id that drifts from the SkinId AcceptProbeSkin.svelte
  // actually loads under in skins/registry.ts.
  it('shares its id with the skin registry loader entry', () => {
    const registrySource = readFileSync('src/skins/registry.ts', 'utf8');
    expect(registrySource).toMatch(
      /'accept-probe':\s*\(\)\s*=>\s*import\(['"]\.\/accept-probe\/AcceptProbeSkin\.svelte['"]\)/,
    );
  });

  it('declares a compiled loader', () => {
    expect(typeof acceptProbeLayout.loader).toBe('function');
  });
});

describe('declared semantic zones (own bespoke VFO readout + own bespoke meter)', () => {
  // meters sits alone in its own `meters`-id zone and is declared but never
  // required — both required by meters-declarability.test.ts's hand-
  // reviewed shape checks, neither mentioned by
  // docs/architecture/building-a-skin.md.
  it('declares a main vfo zone and a separate meters zone; meters is not required', () => {
    expect(acceptProbeLayout.zones).toEqual([
      { id: 'main', surfaces: ['vfo'] },
      { id: 'meters', surfaces: ['meters'] },
    ]);
    expect([...acceptProbeLayout.requiredSemanticSurfaces]).toEqual(['vfo']);
  });
});

describe('topology honesty', () => {
  it('resolves itself on all four canonical topologies', () => {
    for (const topology of TOPOLOGY_CLASSES) {
      expect(resolveLayoutForTopology('accept-probe', topology)?.id).toBe('accept-probe');
    }
  });
});

describe('MOR-1160 sizing axis — accept-probe stays fluid', () => {
  it('declares fluid sizing with no breakpoints', () => {
    expect(acceptProbeLayout.stageSizing).toEqual({ mode: 'fluid', responsiveBreakpoints: [] });
  });

  it('resolves on both a desktop and an iPhone-class portrait viewport — fluid never gates', () => {
    expect(resolveLayoutForViewport('accept-probe', { width: 1440, height: 900 })?.id).toBe('accept-probe');
    expect(resolveLayoutForViewport('accept-probe', { width: 390, height: 844 })?.id).toBe('accept-probe');
  });
});

describe('no fallback family', () => {
  it('declares no fallback', () => {
    expect(acceptProbeLayout.fallbackLayoutId).toBeNull();
  });
});
