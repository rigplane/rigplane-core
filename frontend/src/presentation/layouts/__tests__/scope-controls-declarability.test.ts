/**
 * MOR-1311 — `scopeControls` is DECLARABLE, and nothing declares it yet.
 *
 * Slice 11B (the LAST B-slice of the vocabulary program) adds the name to
 * `SEMANTIC_SURFACE_NAMES` so a manifest CAN mount the surface later; it
 * deliberately does not touch any manifest, and it adds no design-language
 * renderer slot (that set was frozen by MOR-1072).
 *
 * Same three pins as `cw-keyer-declarability.test.ts` /
 * `ritxit-scan-declarability.test.ts` / `rx-audio-declarability.test.ts`:
 *   - drop the name and a future manifest's zone stops validating;
 *   - quietly add a `scopeControls` zone to a shipped manifest and the DOM
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

describe('scopeControls is a declarable semantic surface', () => {
  // Kills: reverting the SEMANTIC_SURFACE_NAMES addition.
  it('is in the declarable set, appended last', () => {
    expect([...SEMANTIC_SURFACE_NAMES]).toEqual([
      'vfo', 'rxTx', 'txAux', 'meters', 'rxAudio', 'filter', 'dsp', 'rfFrontEnd', 'band',
      'antenna', 'ritXitScan', 'cwKeyer', 'scopeDisplay', 'scopeControls',
    ]);
  });

  // Kills: adding the name to the type but not to the runtime allow-list the
  // zone validator checks — the manifest would still be rejected.
  it('accepts a manifest zone that declares it', () => {
    const manifest = validLayoutManifest({
      zones: [{ id: 'main', surfaces: ['vfo', 'rxTx', 'scopeControls'] }],
      requiredSemanticSurfaces: ['vfo', 'rxTx'],
    });
    expect(() => validateLayoutManifest(manifest)).not.toThrow();
  });

  it('still rejects a zone naming a surface that does not exist', () => {
    const manifest = validLayoutManifest({
      zones: [{ id: 'main', surfaces: ['spectrumToolbar'] as unknown as readonly ['vfo'] }],
    });
    expect(() => validateLayoutManifest(manifest)).toThrow(/subset of/);
  });
});

describe('no shipped manifest declares a scopeControls zone in this slice', () => {
  // Kills: slipping a scopeControls zone into an existing layout here — the
  // MOR-1069 dual-receiver-cockpit tab-order assertion is written against
  // the zones it declares today, so declaring one here would put toolbar
  // controls in the cockpit with no updated sequence pin.
  it.each([
    ['sdr-test', sdrTestLayout], ['dual-receiver-cockpit', dualReceiverCockpitLayout],
    ['lcd-cockpit', lcdCockpitLayout], ['lcd-scope', lcdScopeLayout],
    ['mobile', mobileLayout], ['desktop-v2', desktopV2Layout],
  ])('%s declares no scopeControls zone and does not require the surface', (_id, manifest) => {
    for (const zone of manifest.zones) expect(zone.surfaces).not.toContain('scopeControls');
    expect(manifest.requiredSemanticSurfaces).not.toContain('scopeControls');
  });
});

describe('the design-language renderer slot set is untouched', () => {
  // Kills: adding a renderer slot for this surface. The scope toolbar has no
  // gauge grammar a language must describe; the slot set stays frozen.
  it('still carries exactly the three MOR-1072 slots', () => {
    expect([...RENDERER_SLOT_NAMES]).toEqual(['meters', 'frequencyDisplay', 'stateFeedback']);
  });
});
