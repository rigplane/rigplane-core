/**
 * MOR-1273 — `meters` is DECLARABLE, and nothing declares it yet.
 *
 * Slice 2B adds the name to `SEMANTIC_SURFACE_NAMES` so a manifest CAN mount
 * the surface later; it deliberately does not touch any manifest, and it does
 * not add a renderer slot — a design language draws a meter through the
 * `'meters'` slot `RENDERER_SLOT_NAMES` has carried since MOR-1072 (risk R2:
 * adding a slot would be a language-contract change this slice must not make).
 *
 * The three pins mirror `tx-aux-declarability.test.ts`:
 *   - drop the name and a future manifest's zone stops validating;
 *   - quietly add a `meters` zone to a shipped manifest and the DOM grows a
 *     zone id no layout review ever saw;
 *   - the renderer slot stays exactly the set MOR-1072 froze.
 */
import { describe, it, expect } from 'vitest';
import { SEMANTIC_SURFACE_NAMES, validateLayoutManifest } from '../contract';
import { RENDERER_SLOT_NAMES } from '../../languages/contract';
import { validLayoutManifest } from './fixtures';
import {
  desktopV2Layout, dualReceiverCockpitLayout, lcdCockpitLayout, lcdScopeLayout, mobileLayout,
  sdrTestLayout,
} from '../declarations';

describe('meters is a declarable semantic surface', () => {
  // Kills: reverting the SEMANTIC_SURFACE_NAMES addition.
  it('is in the declarable set alongside vfo, rxTx and txAux', () => {
    expect([...SEMANTIC_SURFACE_NAMES]).toEqual(['vfo', 'rxTx', 'txAux', 'meters']);
  });

  // Kills: adding the name to the type but not to the runtime allow-list the
  // zone validator checks — the manifest would still be rejected.
  it('accepts a manifest zone that declares it', () => {
    const manifest = validLayoutManifest({
      zones: [{ id: 'main', surfaces: ['vfo', 'rxTx', 'meters'] }],
      requiredSemanticSurfaces: ['vfo', 'rxTx'],
    });
    expect(() => validateLayoutManifest(manifest)).not.toThrow();
  });

  it('still rejects a zone naming a surface that does not exist', () => {
    const manifest = validLayoutManifest({
      zones: [{ id: 'main', surfaces: ['meterDock'] as unknown as readonly ['vfo'] }],
    });
    expect(() => validateLayoutManifest(manifest)).toThrow(/subset of/);
  });
});

describe('no shipped manifest declares a meters zone in this slice', () => {
  // Kills: slipping a meters zone into an existing layout here. Declarability
  // is the whole scope of the contract touch; placing the surface in a real
  // layout is a later, separately reviewed slice.
  it.each([
    ['sdr-test', sdrTestLayout], ['dual-receiver-cockpit', dualReceiverCockpitLayout],
    ['lcd-cockpit', lcdCockpitLayout], ['lcd-scope', lcdScopeLayout],
    ['mobile', mobileLayout], ['desktop-v2', desktopV2Layout],
  ])('%s declares no meters zone and does not require the surface', (_id, manifest) => {
    for (const zone of manifest.zones) expect(zone.surfaces).not.toContain('meters');
    expect(manifest.requiredSemanticSurfaces).not.toContain('meters');
  });
});

describe('the design-language renderer slot set is untouched (risk R2)', () => {
  // Kills: adding a renderer slot for this surface. `'meters'` already exists;
  // a language that draws meters goes through it and through `projection.ts`.
  it('still carries exactly the three MOR-1072 slots, meters among them', () => {
    expect([...RENDERER_SLOT_NAMES]).toEqual(['meters', 'frequencyDisplay', 'stateFeedback']);
  });
});
