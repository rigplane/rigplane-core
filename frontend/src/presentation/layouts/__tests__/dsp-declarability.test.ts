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
      'vfo', 'rxTx', 'txAux', 'meters', 'rxAudio', 'filter', 'dsp', 'rfFrontEnd', 'band',
      'antenna', 'ritXitScan', 'cwKeyer', 'scopeDisplay', 'scopeControls',
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

describe('exactly the reviewed manifests declare a dsp zone (MOR-1368)', () => {
  /** The literal — extend by hand, with a layout review, never silently. */
  const DECLARES_DSP = ['desktop-v2'];

  const ALL = [
    ['sdr-test', sdrTestLayout], ['dual-receiver-cockpit', dualReceiverCockpitLayout],
    ['lcd-cockpit', lcdCockpitLayout], ['lcd-scope', lcdScopeLayout],
    ['mobile', mobileLayout], ['desktop-v2', desktopV2Layout],
  ] as const;

  // Kills BOTH directions: desktop-v2 losing the zone S9 gave it — which would
  // resurrect FOUR twins at once, `DspPanel` in both sidebars plus the settings
  // modal's DSP *and* AGC sections — and a family gaining one without review.
  it('the declaring set is exactly the reviewed literal', () => {
    const declaring = ALL
      .filter(([, m]) => m.zones.some((z) => z.surfaces.includes('dsp')))
      .map(([id]) => id)
      .sort();
    expect(declaring).toEqual([...DECLARES_DSP].sort());
  });

  // Kills: declaring the zone under a drifted id — the wiring binds whatever
  // the plan's key is, so the id IS the contract with the layout's arrangement.
  // There is deliberately NO separate `agc` zone: `DspSurface` owns the AGC
  // leaf (5A/MOR-1290), so `AgcPanel` retires on THIS zone or ships a
  // half-double beside a surface that already draws AGC.
  it.each(DECLARES_DSP)('%s declares it under the stable `dsp` id, alone in its zone', (id) => {
    const manifest = ALL.find(([name]) => name === id)![1];
    const zone = manifest.zones.find((z) => z.surfaces.includes('dsp'))!;
    expect(zone.id).toBe('dsp');
    expect(zone.surfaces).toEqual(['dsp']);
    expect(manifest.zones.map((z) => z.id)).not.toContain('agc');
  });

  // Kills: making dsp REQUIRED. A radio declaring no NR/NB/notch/AGC signal at
  // all must still resolve this layout; the surface self-gates on `view.dsp`.
  it.each(ALL)('%s does not require the dsp surface', (_id, manifest) => {
    expect(manifest.requiredSemanticSurfaces).not.toContain('dsp');
  });
});
