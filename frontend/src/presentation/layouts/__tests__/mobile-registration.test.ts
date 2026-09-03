/**
 * MOR-1094 — the mobile presentation entrypoint as a v1 layout manifest
 * (schema, validator and registry: `../contract`, MOR-1066), mirroring the
 * MOR-1092 LCD registration proof.
 *
 * What this file is for: the manifest is the only place the mobile entrypoint
 * declares its identity, the semantic zone it mounts and its MOR-1160 sizing
 * assignment. Mobile is the FLUID side of that axis — the counterpart to the
 * LCD's fixed-native stage. Every claim below is read back out of the shared
 * registry rather than off the exported object, so a manifest that is written
 * but never registered fails here. Each test's doc line names the mutation it
 * kills.
 */
import { readFileSync } from 'node:fs';
import { describe, it, expect } from 'vitest';
import { getLayout } from '../contract';
// Deliberately through the shared aggregation entry, not `../mobile-declarations`
// directly: it pins that `declarations.ts` really pulls the mobile family in
// (nothing else does), and it keeps this fast-pool file on the SAME module
// entry point as `registry.test.ts` / `lcd-registration.test.ts` — under
// `isolate: false` a second entry into a module that registers at import time
// is how a split module graph (and a phantom "layout not registered") happens.
import { mobileLayout } from '../declarations';

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

describe('MOR-1160 sizing axis — mobile is the fluid side', () => {
  // Kills: declaring mobile `fixed-native`. Mobile chrome reflows; it is not
  // an instrument stage scaled as one letterboxed block, and a native stage
  // here would make the phone shell fail its own minScale gate.
  it('declares fluid sizing with the one breakpoint the layout implements', () => {
    expect(mobileLayout.stageSizing).toEqual({ mode: 'fluid', responsiveBreakpoints: [500] });
  });

  // Kills: giving mobile a fallback it does not need. Mobile declares fluid
  // sizing, so it has no viewport gate to fail.
  it('declares no fallback', () => {
    expect(mobileLayout.fallbackLayoutId).toBeNull();
  });
});
