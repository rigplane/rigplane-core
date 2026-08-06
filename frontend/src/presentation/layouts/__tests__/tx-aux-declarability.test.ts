/**
 * MOR-1265 — `txAux` is DECLARABLE. MOR-1336 (S4) declares it for real.
 *
 * Slice 1B adds the name to `SEMANTIC_SURFACE_NAMES` so a manifest CAN mount
 * the surface later; it deliberately does not touch any manifest. Both halves
 * need pinning, in both directions:
 *   - drop the name and a future manifest's zone stops validating (the whole
 *     point of this slice's contract change);
 *   - quietly add a `txAux` zone to a shipped manifest and the DOM grows a
 *     zone id no layout review ever saw.
 *
 * MOR-1336 flipped the second half: `desktop-v2` and `dual-receiver-cockpit`
 * now DO declare a `tx-aux` zone, and the surface mounts through it. The
 * inventory below is a LITERAL of who declares it, so a third family gaining
 * the zone without review still fails here — the guard's direction changed, its
 * job did not.
 */
import { describe, it, expect } from 'vitest';
import { SEMANTIC_SURFACE_NAMES, validateLayoutManifest } from '../contract';
import { validLayoutManifest } from './fixtures';
import {
  desktopV2Layout, dualReceiverCockpitLayout, lcdCockpitLayout, lcdScopeLayout, mobileLayout,
  sdrTestLayout,
} from '../declarations';

describe('txAux is a declarable semantic surface', () => {
  // Kills: reverting the SEMANTIC_SURFACE_NAMES addition. MOR-1273 appended
  // `meters`; `txAux` must stay present and in place.
  it('is in the declarable set alongside vfo and rxTx', () => {
    expect([...SEMANTIC_SURFACE_NAMES]).toEqual(['vfo', 'rxTx', 'txAux', 'meters', 'rxAudio']);
  });

  // Kills: adding the name to the type but not to the runtime allow-list the
  // zone validator checks — the manifest would still be rejected.
  it('accepts a manifest zone that declares it', () => {
    const manifest = validLayoutManifest({
      zones: [{ id: 'main', surfaces: ['vfo', 'rxTx', 'txAux'] }],
      requiredSemanticSurfaces: ['vfo', 'rxTx'],
    });
    expect(() => validateLayoutManifest(manifest)).not.toThrow();
  });

  it('still rejects a zone naming a surface that does not exist', () => {
    const manifest = validLayoutManifest({
      zones: [{ id: 'main', surfaces: ['txAuxLegacy'] as unknown as readonly ['vfo'] }],
    });
    expect(() => validateLayoutManifest(manifest)).toThrow(/subset of/);
  });
});

describe('exactly the reviewed manifests declare a txAux zone (MOR-1336)', () => {
  /** The literal — extend by hand, with a layout review, never silently. */
  const DECLARES_TX_AUX = ['desktop-v2', 'dual-receiver-cockpit'];

  const ALL = [
    ['sdr-test', sdrTestLayout], ['dual-receiver-cockpit', dualReceiverCockpitLayout],
    ['lcd-cockpit', lcdCockpitLayout], ['lcd-scope', lcdScopeLayout],
    ['mobile', mobileLayout], ['desktop-v2', desktopV2Layout],
  ] as const;

  // Kills BOTH directions: a family losing the zone S4 gave it, and a family
  // gaining one without review.
  it('the declaring set is exactly the reviewed literal', () => {
    const declaring = ALL
      .filter(([, m]) => m.zones.some((z) => z.surfaces.includes('txAux')))
      .map(([id]) => id)
      .sort();
    expect(declaring).toEqual([...DECLARES_TX_AUX].sort());
  });

  // Kills: declaring the zone under a drifted id — the wiring binds whatever
  // the plan's key is, so the id IS the contract with the layout's arrangement.
  it.each(DECLARES_TX_AUX)('%s declares it under the stable `tx-aux` id, alone in its zone', (id) => {
    const manifest = ALL.find(([name]) => name === id)![1];
    const zone = manifest.zones.find((z) => z.surfaces.includes('txAux'))!;
    expect(zone.id).toBe('tx-aux');
    expect(zone.surfaces).toEqual(['txAux']);
  });

  // Kills: making txAux REQUIRED. A radio whose MOR-1244 evidence gate declined
  // the group must still resolve these layouts; the surface self-gates on
  // `view.txAux`, and a required surface no zone could fill would be a
  // resolution failure rather than an honest absence.
  it.each(ALL)('%s does not require the txAux surface', (_id, manifest) => {
    expect(manifest.requiredSemanticSurfaces).not.toContain('txAux');
  });
});
