/**
 * MOR-1279 — `rxAudio` is DECLARABLE, and nothing declares it yet.
 *
 * Slice 3B adds the name to `SEMANTIC_SURFACE_NAMES` so a manifest CAN mount
 * the surface later; it deliberately does not touch any manifest, and it adds
 * no design-language renderer slot (that set was frozen by MOR-1072 — adding
 * one would be a language-contract change this slice must not make).
 *
 * Same three pins as `tx-aux-declarability.test.ts` / `meters-declarability.test.ts`:
 *   - drop the name and a future manifest's zone stops validating;
 *   - quietly add an `rxAudio` zone to a shipped manifest and the DOM grows a
 *     zone id no layout review ever saw;
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

describe('rxAudio is a declarable semantic surface', () => {
  // Kills: reverting the SEMANTIC_SURFACE_NAMES addition.
  it('is in the declarable set alongside vfo, rxTx, txAux and meters', () => {
    expect([...SEMANTIC_SURFACE_NAMES]).toEqual([
      'vfo', 'rxTx', 'txAux', 'meters', 'rxAudio', 'filter', 'dsp', 'rfFrontEnd', 'band',
      'antenna', 'ritXitScan', 'cwKeyer', 'scopeDisplay', 'scopeControls',
    ]);
  });

  // Kills: adding the name to the type but not to the runtime allow-list the
  // zone validator checks — the manifest would still be rejected.
  it('accepts a manifest zone that declares it', () => {
    const manifest = validLayoutManifest({
      zones: [{ id: 'main', surfaces: ['vfo', 'rxTx', 'rxAudio'] }],
      requiredSemanticSurfaces: ['vfo', 'rxTx'],
    });
    expect(() => validateLayoutManifest(manifest)).not.toThrow();
  });

  it('still rejects a zone naming a surface that does not exist', () => {
    const manifest = validLayoutManifest({
      zones: [{ id: 'main', surfaces: ['audioPanel'] as unknown as readonly ['vfo'] }],
    });
    expect(() => validateLayoutManifest(manifest)).toThrow(/subset of/);
  });
});

describe('exactly the reviewed manifests declare an rxAudio zone (MOR-1368)', () => {
  /** The literal — extend by hand, with a layout review, never silently. */
  const DECLARES_RX_AUDIO = ['desktop-v2'];

  const ALL = [
    ['sdr-test', sdrTestLayout], ['dual-receiver-cockpit', dualReceiverCockpitLayout],
    ['lcd-cockpit', lcdCockpitLayout], ['lcd-scope', lcdScopeLayout],
    ['mobile', mobileLayout], ['desktop-v2', desktopV2Layout],
  ] as const;

  // Kills BOTH directions: desktop-v2 losing the zone S9 gave it (which would
  // resurrect the RX AUDIO twin in both sidebars), and a family gaining one
  // without review.
  it('the declaring set is exactly the reviewed literal', () => {
    const declaring = ALL
      .filter(([, m]) => m.zones.some((z) => z.surfaces.includes('rxAudio')))
      .map(([id]) => id)
      .sort();
    expect(declaring).toEqual([...DECLARES_RX_AUDIO].sort());
  });

  // Kills: declaring the zone under a drifted id — the wiring binds whatever
  // the plan's key is, so the id IS the contract with the layout's arrangement.
  it.each(DECLARES_RX_AUDIO)('%s declares it under the stable `rx-audio` id, alone in its zone', (id) => {
    const manifest = ALL.find(([name]) => name === id)![1];
    const zone = manifest.zones.find((z) => z.surfaces.includes('rxAudio'))!;
    expect(zone.id).toBe('rx-audio');
    expect(zone.surfaces).toEqual(['rxAudio']);
  });

  // Kills: making rxAudio REQUIRED. A radio with no audio chain at all must
  // still resolve this layout; the surface self-gates on `view.rxAudio`.
  it.each(ALL)('%s does not require the rxAudio surface', (_id, manifest) => {
    expect(manifest.requiredSemanticSurfaces).not.toContain('rxAudio');
  });
});

describe('the design-language renderer slot set is untouched', () => {
  // Kills: adding a renderer slot for this surface. RX audio has no gauge
  // grammar a language must describe; the slot set stays frozen.
  it('still carries exactly the three MOR-1072 slots', () => {
    expect([...RENDERER_SLOT_NAMES]).toEqual(['meters', 'frequencyDisplay', 'stateFeedback']);
  });
});
