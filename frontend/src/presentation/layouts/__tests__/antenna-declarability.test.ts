/**
 * MOR-1309 — `antenna` is DECLARABLE. MOR-1367 (S8) declares it for real.
 *
 * Slice 8C added the name to `SEMANTIC_SURFACE_NAMES` so a manifest COULD mount
 * the surface later; it deliberately touched no manifest and added no
 * design-language renderer slot (that set was frozen by MOR-1072).
 *
 * MOR-1367 flips the second half, on `desktop-v2` ONLY. The "nothing declares
 * it" half was load-bearing for 8C because the surface is CONTROL-BEARING and is
 * mounted in the SINGLE composition only (MOR-1304 mounting canon, option (i));
 * declaring a zone on `desktop-v2` does not put it into the dual composition, so
 * the cockpit manifest stays untouched and 8C's dual-absence pin stays valid and
 * unedited. This is canon option (ii), for `desktop-v2` alone.
 */
import { describe, it, expect } from 'vitest';
import { SEMANTIC_SURFACE_NAMES, validateLayoutManifest } from '../contract';
import { RENDERER_SLOT_NAMES } from '../../languages/contract';
import { validLayoutManifest } from './fixtures';
import {
  desktopV2Layout, dualReceiverCockpitLayout, lcdCockpitLayout, lcdScopeLayout, mobileLayout,
  sdrTestLayout,
} from '../declarations';

describe('antenna is a declarable semantic surface', () => {
  // Kills: reverting the SEMANTIC_SURFACE_NAMES addition.
  it('is in the declarable set alongside vfo, rxTx, txAux, meters and rxAudio', () => {
    expect([...SEMANTIC_SURFACE_NAMES])
      .toEqual([
        'vfo', 'rxTx', 'txAux', 'meters', 'rxAudio', 'filter', 'dsp', 'rfFrontEnd', 'band',
        'antenna', 'ritXitScan', 'cwKeyer', 'scopeDisplay', 'scopeControls',
      ]);
  });

  // Kills: adding the name to the type but not to the runtime allow-list the
  // zone validator checks — the manifest would still be rejected.
  it('accepts a manifest zone that declares it', () => {
    const manifest = validLayoutManifest({
      zones: [{ id: 'main', surfaces: ['vfo', 'rxTx', 'antenna'] }],
      requiredSemanticSurfaces: ['vfo', 'rxTx'],
    });
    expect(() => validateLayoutManifest(manifest)).not.toThrow();
  });

  it('still rejects a zone naming a surface that does not exist', () => {
    const manifest = validLayoutManifest({
      zones: [{ id: 'main', surfaces: ['antennaPanel'] as unknown as readonly ['vfo'] }],
    });
    expect(() => validateLayoutManifest(manifest)).toThrow(/subset of/);
  });
});

describe('exactly the reviewed manifests declare an antenna zone (MOR-1367)', () => {
  /** The literal — extend by hand, with a layout review, never silently. */
  const DECLARES_ANTENNA = ['desktop-v2'];

  const ALL = [
    ['sdr-test', sdrTestLayout], ['dual-receiver-cockpit', dualReceiverCockpitLayout],
    ['lcd-cockpit', lcdCockpitLayout], ['lcd-scope', lcdScopeLayout],
    ['mobile', mobileLayout], ['desktop-v2', desktopV2Layout],
  ] as const;

  // Kills BOTH directions: a family losing the zone S8 gave it, and a family
  // gaining one without review. For the cockpit that is load-bearing rather
  // than cosmetic — a declared zone there would move a control-bearing surface
  // into the dual composition and change what MOR-1069's tab-order assertion
  // has to say.
  it('the declaring set is exactly the reviewed literal', () => {
    const declaring = ALL
      .filter(([, m]) => m.zones.some((z) => z.surfaces.includes('antenna')))
      .map(([id]) => id)
      .sort();
    expect(declaring).toEqual([...DECLARES_ANTENNA].sort());
  });

  // Kills: declaring the zone under a drifted id — the wiring binds whatever
  // the plan's key is, so the id IS the contract with the layout's arrangement.
  it.each(DECLARES_ANTENNA)('%s declares it under the stable `antenna` id, alone in its zone', (id) => {
    const manifest = ALL.find(([name]) => name === id)![1];
    const zone = manifest.zones.find((z) => z.surfaces.includes('antenna'))!;
    expect(zone.id).toBe('antenna');
    expect(zone.surfaces).toEqual(['antenna']);
  });

  // Kills: making antenna REQUIRED. A single-antenna radio reports no antenna
  // group at all and must still resolve this layout; the surface self-gates on
  // `view.antenna`, and a required surface no zone could fill would be a
  // resolution failure rather than an honest absence.
  it.each(ALL)('%s does not require the antenna surface', (_id, manifest) => {
    expect(manifest.requiredSemanticSurfaces).not.toContain('antenna');
  });
});

describe('the design-language renderer slot set is untouched', () => {
  // Kills: adding a renderer slot for this surface. An antenna selector has no
  // gauge grammar a language must describe; the slot set stays frozen.
  it('still carries exactly the three MOR-1072 slots', () => {
    expect([...RENDERER_SLOT_NAMES]).toEqual(['meters', 'frequencyDisplay', 'stateFeedback']);
  });
});
