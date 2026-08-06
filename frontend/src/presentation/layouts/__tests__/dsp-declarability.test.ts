/**
 * MOR-1305 — `dsp` is DECLARABLE, and nothing declares it yet.
 *
 * Slice 5B adds the name to `SEMANTIC_SURFACE_NAMES` so a manifest CAN mount
 * the surface later; it deliberately does not touch any manifest. Mirrors
 * `tx-aux-declarability.test.ts`/`meters-declarability.test.ts`.
 */
import { describe, it, expect } from 'vitest';
import { SEMANTIC_SURFACE_NAMES, validateLayoutManifest } from '../contract';
import { validLayoutManifest } from './fixtures';
import {
  desktopV2Layout, dualReceiverCockpitLayout, lcdCockpitLayout, lcdScopeLayout, mobileLayout,
  sdrTestLayout,
} from '../declarations';

describe('dsp is a declarable semantic surface', () => {
  // Kills: reverting the SEMANTIC_SURFACE_NAMES addition, or reordering the
  // existing names — other B-slices append their own name concurrently at
  // this same line, so an accidental reorder here is the merge conflict this
  // slice must not create.
  it('is in the declarable set alongside vfo, rxTx, txAux, meters and rxAudio', () => {
    expect([...SEMANTIC_SURFACE_NAMES]).toEqual([
      'vfo', 'rxTx', 'txAux', 'meters', 'rxAudio', 'filter', 'dsp', 'rfFrontEnd',
    ]);
  });

  it('accepts a manifest zone that declares it', () => {
    const manifest = validLayoutManifest({
      zones: [{ id: 'main', surfaces: ['vfo', 'rxTx', 'dsp'] }],
      requiredSemanticSurfaces: ['vfo', 'rxTx'],
    });
    expect(() => validateLayoutManifest(manifest)).not.toThrow();
  });

  it('still rejects a zone naming a surface that does not exist', () => {
    const manifest = validLayoutManifest({
      zones: [{ id: 'main', surfaces: ['dspLegacy'] as unknown as readonly ['vfo'] }],
    });
    expect(() => validateLayoutManifest(manifest)).toThrow(/subset of/);
  });
});

describe('no shipped manifest declares a dsp zone in this slice', () => {
  it.each([
    ['sdr-test', sdrTestLayout], ['dual-receiver-cockpit', dualReceiverCockpitLayout],
    ['lcd-cockpit', lcdCockpitLayout], ['lcd-scope', lcdScopeLayout],
    ['mobile', mobileLayout], ['desktop-v2', desktopV2Layout],
  ])('%s declares no dsp zone and does not require the surface', (_id, manifest) => {
    for (const zone of manifest.zones) expect(zone.surfaces).not.toContain('dsp');
    expect(manifest.requiredSemanticSurfaces).not.toContain('dsp');
  });
});
