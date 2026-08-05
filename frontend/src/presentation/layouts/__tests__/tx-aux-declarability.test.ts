/**
 * MOR-1265 — `txAux` is DECLARABLE, and nothing declares it yet.
 *
 * Slice 1B adds the name to `SEMANTIC_SURFACE_NAMES` so a manifest CAN mount
 * the surface later; it deliberately does not touch any manifest. Both halves
 * need pinning, in both directions:
 *   - drop the name and a future manifest's zone stops validating (the whole
 *     point of this slice's contract change);
 *   - quietly add a `txAux` zone to a shipped manifest and the DOM grows a
 *     zone id no layout review ever saw. That is out of scope here and is
 *     what the second test refuses.
 */
import { describe, it, expect } from 'vitest';
import { SEMANTIC_SURFACE_NAMES, validateLayoutManifest } from '../contract';
import { validLayoutManifest } from './fixtures';
import {
  desktopV2Layout, dualReceiverCockpitLayout, lcdCockpitLayout, lcdScopeLayout, mobileLayout,
  sdrTestLayout,
} from '../declarations';

describe('txAux is a declarable semantic surface', () => {
  // Kills: reverting the SEMANTIC_SURFACE_NAMES addition. MOR-1273 appended
  // `meters`; `txAux` must stay present and in place.
  it('is in the declarable set alongside vfo and rxTx', () => {
    expect([...SEMANTIC_SURFACE_NAMES]).toEqual(['vfo', 'rxTx', 'txAux', 'meters', 'rxAudio']);
  });

  // Kills: adding the name to the type but not to the runtime allow-list the
  // zone validator checks — the manifest would still be rejected.
  it('accepts a manifest zone that declares it', () => {
    const manifest = validLayoutManifest({
      zones: [{ id: 'main', surfaces: ['vfo', 'rxTx', 'txAux'] }],
      requiredSemanticSurfaces: ['vfo', 'rxTx'],
    });
    expect(() => validateLayoutManifest(manifest)).not.toThrow();
  });

  it('still rejects a zone naming a surface that does not exist', () => {
    const manifest = validLayoutManifest({
      zones: [{ id: 'main', surfaces: ['txAuxLegacy'] as unknown as readonly ['vfo'] }],
    });
    expect(() => validateLayoutManifest(manifest)).toThrow(/subset of/);
  });
});

describe('no shipped manifest declares a txAux zone in this slice', () => {
  // Kills: slipping a txAux zone into an existing layout here. Declarability
  // is the whole scope of MOR-1265; placing the surface in a real layout is a
  // later, separately reviewed slice.
  it.each([
    ['sdr-test', sdrTestLayout], ['dual-receiver-cockpit', dualReceiverCockpitLayout],
    ['lcd-cockpit', lcdCockpitLayout], ['lcd-scope', lcdScopeLayout],
    ['mobile', mobileLayout], ['desktop-v2', desktopV2Layout],
  ])('%s declares no txAux zone and does not require the surface', (_id, manifest) => {
    for (const zone of manifest.zones) expect(zone.surfaces).not.toContain('txAux');
    expect(manifest.requiredSemanticSurfaces).not.toContain('txAux');
  });
});
