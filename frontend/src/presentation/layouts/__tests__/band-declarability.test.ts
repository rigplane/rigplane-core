/**
 * MOR-1307 — `band` is DECLARABLE. MOR-1367 (S8) declares it for real.
 *
 * Slice 7B added the name to `SEMANTIC_SURFACE_NAMES` so a manifest COULD mount
 * the surface later; it deliberately touched no manifest and added no
 * design-language renderer slot (that set was frozen by MOR-1072).
 *
 * MOR-1367 flips the second half, on `desktop-v2` ONLY — the cockpit manifest is
 * deliberately untouched (S5 precedent), so 7B's single-composition-only mount
 * and its dual-absence pin in
 * `components-v2/wiring/__tests__/semantic-band-wiring.component.test.ts` stay
 * valid and unedited. The inventory below is a LITERAL of who declares it,
 * mirroring `meters-declarability.test.ts`'s post-S5 shape.
 *
 * `band` is the one surface in this slice whose legacy twin does NOT retire
 * wholesale: `BandSelector` keeps hosting the LW/MW + SWL broadcast tabs and
 * loses only its HAM half, via `hamBands={!declared.has('band')}` (S10 §4a).
 * That half of the contract is pinned in
 * `components-v2/layout/__tests__/semantic-desktop-migration.component.test.ts`.
 */
import { describe, it, expect } from 'vitest';
import { SEMANTIC_SURFACE_NAMES, validateLayoutManifest } from '../contract';
import { RENDERER_SLOT_NAMES } from '../../languages/contract';
import { validLayoutManifest } from './fixtures';
import {
  desktopV2Layout, dualReceiverCockpitLayout, lcdCockpitLayout, lcdScopeLayout, mobileLayout,
  sdrTestLayout,
} from '../declarations';

describe('band is a declarable semantic surface', () => {
  // Kills: reverting the SEMANTIC_SURFACE_NAMES addition. MOR-1273 appended
  // `meters`, MOR-1304 appended `filter`, MOR-1305 appended `dsp`, MOR-1306
  // appended `rfFrontEnd`; `band` must land last, after all of them.
  it('is in the declarable set alongside vfo, rxTx, txAux, meters, rxAudio, filter, dsp and rfFrontEnd', () => {
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
      zones: [{ id: 'main', surfaces: ['vfo', 'rxTx', 'band'] }],
      requiredSemanticSurfaces: ['vfo', 'rxTx'],
    });
    expect(() => validateLayoutManifest(manifest)).not.toThrow();
  });

  it('still rejects a zone naming a surface that does not exist', () => {
    const manifest = validLayoutManifest({
      zones: [{ id: 'main', surfaces: ['bandSelector'] as unknown as readonly ['vfo'] }],
    });
    expect(() => validateLayoutManifest(manifest)).toThrow(/subset of/);
  });
});

describe('exactly the reviewed manifests declare a band zone (MOR-1367)', () => {
  /** The literal — extend by hand, with a layout review, never silently. */
  const DECLARES_BAND = ['desktop-v2'];

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
      .filter(([, m]) => m.zones.some((z) => z.surfaces.includes('band')))
      .map(([id]) => id)
      .sort();
    expect(declaring).toEqual([...DECLARES_BAND].sort());
  });

  // Kills: declaring the zone under a drifted id — the suppression binds the
  // DECLARED set, and `BandSelector`'s `hamBands` prop reads exactly
  // `declared.has('band')`, so the surface name and the zone id are both
  // contracts with the shell.
  it.each(DECLARES_BAND)('%s declares it under the stable `band` id, alone in its zone', (id) => {
    const manifest = ALL.find(([name]) => name === id)![1];
    const zone = manifest.zones.find((z) => z.surfaces.includes('band'))!;
    expect(zone.id).toBe('band');
    expect(zone.surfaces).toEqual(['band']);
  });

  // Kills: making band REQUIRED. A radio whose caps carry no frequency ranges
  // at all must still resolve this layout; the surface self-gates on
  // `view.band`, and a required surface no zone could fill would be a
  // resolution failure rather than an honest absence.
  it.each(ALL)('%s does not require the band surface', (_id, manifest) => {
    expect(manifest.requiredSemanticSurfaces).not.toContain('band');
  });
});

describe('the design-language renderer slot set is untouched', () => {
  // Kills: adding a renderer slot for this surface. A band picker has no gauge
  // grammar a language must describe; the slot set stays frozen.
  it('still carries exactly the three MOR-1072 slots', () => {
    expect([...RENDERER_SLOT_NAMES]).toEqual(['meters', 'frequencyDisplay', 'stateFeedback']);
  });
});
