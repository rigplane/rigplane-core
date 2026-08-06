/**
 * MOR-1306 — `rfFrontEnd` is DECLARABLE, and nothing declares it yet.
 *
 * Slice 6B adds the name to `SEMANTIC_SURFACE_NAMES` so a manifest CAN mount
 * the surface later; it deliberately does not touch any manifest, and it adds
 * no design-language renderer slot (that set was frozen by MOR-1072 — adding
 * one would be a language-contract change this slice must not make).
 *
 * Same three pins as `tx-aux-declarability.test.ts` / `meters-declarability.test.ts`
 * / `rx-audio-declarability.test.ts`:
 *   - drop the name and a future manifest's zone stops validating;
 *   - quietly add an `rfFrontEnd` zone to a shipped manifest and the DOM
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

describe('rfFrontEnd is a declarable semantic surface', () => {
  // Kills: reverting the SEMANTIC_SURFACE_NAMES addition. MOR-1307 appended
  // `band`; `rfFrontEnd` must stay present and in place.
  it('is in the declarable set alongside vfo, rxTx, txAux, meters and rxAudio', () => {
    expect([...SEMANTIC_SURFACE_NAMES])
      .toEqual([
        'vfo', 'rxTx', 'txAux', 'meters', 'rxAudio', 'filter', 'dsp', 'rfFrontEnd', 'band',
        'antenna', 'ritXitScan', 'cwKeyer', 'scopeDisplay',
      ]);
  });

  // Kills: adding the name to the type but not to the runtime allow-list the
  // zone validator checks — the manifest would still be rejected.
  it('accepts a manifest zone that declares it', () => {
    const manifest = validLayoutManifest({
      zones: [{ id: 'main', surfaces: ['vfo', 'rxTx', 'rfFrontEnd'] }],
      requiredSemanticSurfaces: ['vfo', 'rxTx'],
    });
    expect(() => validateLayoutManifest(manifest)).not.toThrow();
  });

  it('still rejects a zone naming a surface that does not exist', () => {
    const manifest = validLayoutManifest({
      zones: [{ id: 'main', surfaces: ['rfFrontEndPanel'] as unknown as readonly ['vfo'] }],
    });
    expect(() => validateLayoutManifest(manifest)).toThrow(/subset of/);
  });
});

describe('no shipped manifest declares an rfFrontEnd zone in this slice', () => {
  // Kills: slipping an rfFrontEnd zone into an existing layout here.
  // Declarability is the whole scope of the contract touch; placing the
  // surface in a real layout (including the dual-receiver-cockpit — see the
  // MOR-1069 mount canon in `RfFrontEndSurface.svelte`) is a later, separately
  // reviewed rework slice.
  it.each([
    ['sdr-test', sdrTestLayout], ['dual-receiver-cockpit', dualReceiverCockpitLayout],
    ['lcd-cockpit', lcdCockpitLayout], ['lcd-scope', lcdScopeLayout],
    ['mobile', mobileLayout], ['desktop-v2', desktopV2Layout],
  ])('%s declares no rfFrontEnd zone and does not require the surface', (_id, manifest) => {
    for (const zone of manifest.zones) expect(zone.surfaces).not.toContain('rfFrontEnd');
    expect(manifest.requiredSemanticSurfaces).not.toContain('rfFrontEnd');
  });
});

describe('the design-language renderer slot set is untouched', () => {
  // Kills: adding a renderer slot for this surface. RF front end has no gauge
  // grammar a language must describe; the slot set stays frozen.
  it('still carries exactly the three MOR-1072 slots', () => {
    expect([...RENDERER_SLOT_NAMES]).toEqual(['meters', 'frequencyDisplay', 'stateFeedback']);
  });
});
