/**
 * MOR-1273 — `meters` is DECLARABLE. MOR-1341 (S5) declares it for real.
 *
 * Slice 2B added the name to `SEMANTIC_SURFACE_NAMES` so a manifest CAN mount
 * the surface later; it deliberately did not touch any manifest, and it added
 * no renderer slot — a design language draws a meter through the `'meters'`
 * slot `RENDERER_SLOT_NAMES` has carried since MOR-1072 (risk R2: adding a
 * slot would be a language-contract change this slice must not make).
 *
 * MOR-1341 flips the second half, on `desktop-v2` ONLY: the cockpit already
 * renders no `MetersDockPanel` twin to retire (`meters` group availability is
 * not manifest-gated at all; adding the zone there is a separately-scoped,
 * fixture-cost decision left for its own ticket), so this slice's suppression
 * payoff is desktop-v2-shaped. The inventory below is a LITERAL of who
 * declares it, mirroring `tx-aux-declarability.test.ts`'s post-S4 shape.
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
  // Kills: reverting the SEMANTIC_SURFACE_NAMES addition. MOR-1305 appended
  // `dsp`, MOR-1306 appended `rfFrontEnd`; `meters` must stay present and in
  // place.
  it('is in the declarable set alongside vfo, rxTx and txAux', () => {
    expect([...SEMANTIC_SURFACE_NAMES]).toEqual([
      'vfo', 'rxTx', 'txAux', 'meters', 'rxAudio', 'filter', 'dsp', 'rfFrontEnd', 'band',
      'antenna', 'ritXitScan',
    ]);
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

describe('exactly the reviewed manifests declare a meters zone (MOR-1341)', () => {
  /** The literal — extend by hand, with a layout review, never silently. */
  const DECLARES_METERS = ['desktop-v2'];

  const ALL = [
    ['sdr-test', sdrTestLayout], ['dual-receiver-cockpit', dualReceiverCockpitLayout],
    ['lcd-cockpit', lcdCockpitLayout], ['lcd-scope', lcdScopeLayout],
    ['mobile', mobileLayout], ['desktop-v2', desktopV2Layout],
  ] as const;

  // Kills BOTH directions: a family losing the zone S5 gave it, and a family
  // gaining one without review.
  it('the declaring set is exactly the reviewed literal', () => {
    const declaring = ALL
      .filter(([, m]) => m.zones.some((z) => z.surfaces.includes('meters')))
      .map(([id]) => id)
      .sort();
    expect(declaring).toEqual([...DECLARES_METERS].sort());
  });

  // Kills: declaring the zone under a drifted id — the wiring binds whatever
  // the plan's key is, so the id IS the contract with the layout's arrangement.
  it.each(DECLARES_METERS)('%s declares it under the stable `meters` id, alone in its zone', (id) => {
    const manifest = ALL.find(([name]) => name === id)![1];
    const zone = manifest.zones.find((z) => z.surfaces.includes('meters'))!;
    expect(zone.id).toBe('meters');
    expect(zone.surfaces).toEqual(['meters']);
  });

  // Kills: making meters REQUIRED. A radio reporting no meter fields at all
  // must still resolve this layout; the surface self-gates on `view.meters`,
  // and a required surface no zone could fill would be a resolution failure
  // rather than an honest absence.
  it.each(ALL)('%s does not require the meters surface', (_id, manifest) => {
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
