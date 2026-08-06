/**
 * MOR-1312 — `scopeDisplay` is DECLARABLE. MOR-1365 (S6a) declares it for
 * real.
 *
 * Slice 12B added the name to `SEMANTIC_SURFACE_NAMES` so a manifest CAN
 * mount the surface later; it deliberately did not touch any manifest, and it
 * added no design-language renderer slot (that set was frozen by MOR-1072 —
 * adding one would be a language-contract change this slice must not make).
 *
 * MOR-1365 flips the second half, on `desktop-v2` ONLY: the cockpit manifest
 * is deliberately untouched (S5 precedent), so `scopeDisplay` keeps mounting
 * bare there. The inventory below is a LITERAL of who declares it, mirroring
 * `meters-declarability.test.ts`'s post-S5 shape.
 *
 * Same three pins as `tx-aux-declarability.test.ts` / `meters-declarability.test.ts`
 * / `rx-audio-declarability.test.ts`:
 *   - drop the name and a future manifest's zone stops validating;
 *   - quietly add a `scopeDisplay` zone to an unreviewed manifest and the DOM
 *     grows a zone id no layout review ever saw;
 *   - the renderer slot set stays exactly what MOR-1072 froze.
 */
import { describe, it, expect } from 'vitest';
import { SEMANTIC_SURFACE_NAMES, validateLayoutManifest } from '../contract';
import { RENDERER_SLOT_NAMES } from '../../languages/contract';
import { validLayoutManifest } from './fixtures';
import {
  desktopV2Layout, dualReceiverCockpitLayout, lcdCockpitLayout, lcdScopeLayout, mobileLayout,
  sdrTestLayout,
} from '../declarations';

describe('scopeDisplay is a declarable semantic surface', () => {
  // Kills: reverting the SEMANTIC_SURFACE_NAMES addition.
  it('is in the declarable set alongside vfo, rxTx, txAux, meters and rxAudio', () => {
    expect([...SEMANTIC_SURFACE_NAMES]).toEqual([
      'vfo', 'rxTx', 'txAux', 'meters', 'rxAudio', 'filter', 'dsp', 'rfFrontEnd', 'band',
      'antenna', 'ritXitScan', 'cwKeyer', 'scopeDisplay', 'scopeControls',
    ]);
  });

  // Kills: adding the name to the type but not to the runtime allow-list the
  // zone validator checks — the manifest would still be rejected.
  it('accepts a manifest zone that declares it', () => {
    const manifest = validLayoutManifest({
      zones: [{ id: 'main', surfaces: ['vfo', 'rxTx', 'scopeDisplay'] }],
      requiredSemanticSurfaces: ['vfo', 'rxTx'],
    });
    expect(() => validateLayoutManifest(manifest)).not.toThrow();
  });

  it('still rejects a zone naming a surface that does not exist', () => {
    const manifest = validLayoutManifest({
      zones: [{ id: 'main', surfaces: ['scopeStatus'] as unknown as readonly ['vfo'] }],
    });
    expect(() => validateLayoutManifest(manifest)).toThrow(/subset of/);
  });
});

describe('exactly the reviewed manifests declare a scopeDisplay zone (MOR-1365)', () => {
  /** The literal — extend by hand, with a layout review, never silently. */
  const DECLARES_SCOPE_DISPLAY = ['desktop-v2'];

  const ALL = [
    ['sdr-test', sdrTestLayout], ['dual-receiver-cockpit', dualReceiverCockpitLayout],
    ['lcd-cockpit', lcdCockpitLayout], ['lcd-scope', lcdScopeLayout],
    ['mobile', mobileLayout], ['desktop-v2', desktopV2Layout],
  ] as const;

  // Kills BOTH directions: a family losing the zone S6a gave it, and a family
  // gaining one without review.
  it('the declaring set is exactly the reviewed literal', () => {
    const declaring = ALL
      .filter(([, m]) => m.zones.some((z) => z.surfaces.includes('scopeDisplay')))
      .map(([id]) => id)
      .sort();
    expect(declaring).toEqual([...DECLARES_SCOPE_DISPLAY].sort());
  });

  // Kills: declaring the zone under a drifted id — the wiring binds whatever
  // the plan's key is, so the id IS the contract with the layout's arrangement.
  it.each(DECLARES_SCOPE_DISPLAY)(
    '%s declares it under the stable `scope-display` id, alone in its zone',
    (id) => {
      const manifest = ALL.find(([name]) => name === id)![1];
      const zone = manifest.zones.find((z) => z.surfaces.includes('scopeDisplay'))!;
      expect(zone.id).toBe('scope-display');
      expect(zone.surfaces).toEqual(['scopeDisplay']);
    },
  );

  // Kills: making scopeDisplay REQUIRED. A radio the MOR-1301 evidence gate
  // declines must still resolve this layout; the surface self-gates on
  // `view.scopeDisplay`, and a required surface no zone could fill would be a
  // resolution failure rather than an honest absence.
  it.each(ALL)('%s does not require the scopeDisplay surface', (_id, manifest) => {
    expect(manifest.requiredSemanticSurfaces).not.toContain('scopeDisplay');
  });
});

describe('the design-language renderer slot set is untouched', () => {
  // Kills: adding a renderer slot for this surface. A source/health readout
  // has no gauge grammar a language must describe; the slot set stays frozen.
  it('still carries exactly the three MOR-1072 slots', () => {
    expect([...RENDERER_SLOT_NAMES]).toEqual(['meters', 'frequencyDisplay', 'stateFeedback']);
  });
});
