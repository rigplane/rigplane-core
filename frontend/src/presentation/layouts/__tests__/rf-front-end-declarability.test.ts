/**
 * MOR-1306 — `rfFrontEnd` is DECLARABLE. MOR-1366 (S7) declares it for real.
 *
 * Slice 6B added the name to `SEMANTIC_SURFACE_NAMES` so a manifest CAN mount
 * the surface later; it deliberately did not touch any manifest, and it added
 * no design-language renderer slot (that set was frozen by MOR-1072). MOR-1366
 * flips the second half, on `desktop-v2` ONLY: closing the double-presentation
 * defect the rework tail found live on that skin (`RfFrontEndSurface` already
 * mounted bare in the receiver deck via the MOR-1306 `zoned(...)`
 * single-composition mount, while `RfFrontEnd` kept rendering a legacy twin in
 * `LeftSidebar` and the settings modal). The cockpit is deliberately
 * untouched (S5 precedent) — the MOR-1069 mount canon in `RfFrontEndSurface`
 * still forbids a dual-composition mount with no cockpit zone declared. The
 * inventory below is a LITERAL of who declares it, mirroring
 * `meters-declarability.test.ts` / `tx-aux-declarability.test.ts`'s
 * post-rework shape.
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
        'antenna', 'ritXitScan', 'cwKeyer', 'scopeDisplay', 'scopeControls',
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

describe('exactly the reviewed manifests declare an rfFrontEnd zone (MOR-1366)', () => {
  /** The literal — extend by hand, with a layout review, never silently. */
  const DECLARES_RF_FRONT_END = ['desktop-v2'];

  const ALL = [
    ['sdr-test', sdrTestLayout], ['dual-receiver-cockpit', dualReceiverCockpitLayout],
    ['lcd-cockpit', lcdCockpitLayout], ['lcd-scope', lcdScopeLayout],
    ['mobile', mobileLayout], ['desktop-v2', desktopV2Layout],
  ] as const;

  // Kills BOTH directions: a family losing the zone S7 gave it, and a family
  // gaining one without review — including the dual-receiver-cockpit, which
  // the mounting canon (MOR-1304) forbids from mounting a control-bearing
  // surface bare and this slice deliberately leaves undeclared.
  it('the declaring set is exactly the reviewed literal', () => {
    const declaring = ALL
      .filter(([, m]) => m.zones.some((z) => z.surfaces.includes('rfFrontEnd')))
      .map(([id]) => id)
      .sort();
    expect(declaring).toEqual([...DECLARES_RF_FRONT_END].sort());
  });

  // Kills: declaring the zone under a drifted id — the wiring binds whatever
  // the plan's key is, so the id IS the contract with the layout's arrangement.
  it.each(DECLARES_RF_FRONT_END)('%s declares it under the stable `rf-front-end` id, alone in its zone', (id) => {
    const manifest = ALL.find(([name]) => name === id)![1];
    const zone = manifest.zones.find((z) => z.surfaces.includes('rfFrontEnd'))!;
    expect(zone.id).toBe('rf-front-end');
    expect(zone.surfaces).toEqual(['rfFrontEnd']);
  });

  // Kills: making rfFrontEnd REQUIRED. A radio whose evidence gate declines
  // every group (no preamp/attenuator/rf_gain/squelch/digisel/ip_plus
  // capability) must still resolve this layout; the surface self-gates on
  // `view.rfFrontEnd`, and a required surface no zone could fill would be a
  // resolution failure rather than an honest absence.
  it.each(ALL)('%s does not require the rfFrontEnd surface', (_id, manifest) => {
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
