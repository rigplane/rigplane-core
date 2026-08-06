/**
 * MOR-1307 — `band` is DECLARABLE, and nothing declares it yet.
 *
 * Slice 7B adds the name to `SEMANTIC_SURFACE_NAMES` so a manifest CAN mount
 * the surface later; it deliberately does not touch any manifest, and it adds
 * no design-language renderer slot (that set was frozen by MOR-1072).
 *
 * Same three pins as `tx-aux-`/`meters-`/`rx-audio-declarability.test.ts`, with
 * one extra weight here: `band` is a CONTROL-BEARING surface, so "no shipped
 * manifest declares a band zone" is also what makes the single-composition-only
 * mount legal under the MOR-1069 cockpit invariant (see the dual-absence pin in
 * `components-v2/wiring/__tests__/semantic-band-wiring.component.test.ts`). If
 * a manifest ever gains the zone, that pin must be revisited in the same PR.
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
        'antenna', 'ritXitScan', 'cwKeyer',
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

describe('no shipped manifest declares a band zone in this slice', () => {
  // Kills: slipping a band zone into an existing layout here. For the cockpit
  // this is load-bearing, not cosmetic: a declared zone would move the surface
  // into the dual composition and change what the MOR-1069 tab-order assertion
  // has to say.
  it.each([
    ['sdr-test', sdrTestLayout], ['dual-receiver-cockpit', dualReceiverCockpitLayout],
    ['lcd-cockpit', lcdCockpitLayout], ['lcd-scope', lcdScopeLayout],
    ['mobile', mobileLayout], ['desktop-v2', desktopV2Layout],
  ])('%s declares no band zone and does not require the surface', (_id, manifest) => {
    for (const zone of manifest.zones) expect(zone.surfaces).not.toContain('band');
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
