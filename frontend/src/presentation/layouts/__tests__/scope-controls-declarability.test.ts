/**
 * MOR-1311 — `scopeControls` is DECLARABLE. MOR-1370 (S6b-2) declares it for
 * real.
 *
 * Slice 11B (the LAST B-slice of the vocabulary program) adds the name to
 * `SEMANTIC_SURFACE_NAMES` so a manifest CAN mount the surface later; it
 * deliberately does not touch any manifest, and it adds no design-language
 * renderer slot (that set was frozen by MOR-1072).
 *
 * MOR-1370 flips the second half, on `desktop-v2` ONLY: the cockpit manifest
 * is deliberately untouched (S5 precedent, restated by 12B/MOR-1365 for the
 * sibling `scopeDisplay` zone), so `scopeControls` stays undeclared — and
 * therefore unmounted, per the MOR-1304 canon — everywhere else. The
 * inventory below is a LITERAL of who declares it, mirroring
 * `scope-display-declarability.test.ts`'s post-S6a shape. This is the LAST
 * surface in the whole MOR-1262 vocabulary to graduate:
 * `zone-ownership-coverage.test.ts`'s `RECORDED_REASONS` ledger is empty
 * after this slice.
 *
 * Same three pins as `tx-aux-declarability.test.ts` / `meters-declarability.test.ts`
 * / `scope-display-declarability.test.ts`:
 *   - drop the name and a future manifest's zone stops validating;
 *   - quietly add a `scopeControls` zone to an unreviewed manifest and the DOM
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

describe('exactly the reviewed manifests declare a scopeControls zone (MOR-1370)', () => {
  /** The literal — extend by hand, with a layout review, never silently. */
  const DECLARES_SCOPE_CONTROLS = ['desktop-v2'];

  const ALL = [
    ['sdr-test', sdrTestLayout], ['dual-receiver-cockpit', dualReceiverCockpitLayout],
    ['lcd-cockpit', lcdCockpitLayout], ['lcd-scope', lcdScopeLayout],
    ['mobile', mobileLayout], ['desktop-v2', desktopV2Layout],
  ] as const;

  // Kills BOTH directions: a family losing the zone S6b-2 gave it (the
  // MOR-1069 dual-receiver-cockpit tab-order assertion is written against
  // the zones it declares today, so a cockpit gain here would put toolbar
  // controls in the cockpit with no updated sequence pin), and a family
  // gaining one without review.
  it('the declaring set is exactly the reviewed literal', () => {
    const declaring = ALL
      .filter(([, m]) => m.zones.some((z) => z.surfaces.includes('scopeControls')))
      .map(([id]) => id)
      .sort();
    expect(declaring).toEqual([...DECLARES_SCOPE_CONTROLS].sort());
  });

  // Kills: declaring the zone under a drifted id — the wiring binds whatever
  // the plan's key is, so the id IS the contract with the layout's arrangement.
  it.each(DECLARES_SCOPE_CONTROLS)(
    '%s declares it under the stable `scope-controls` id, alone in its zone',
    (id) => {
      const manifest = ALL.find(([name]) => name === id)![1];
      const zone = manifest.zones.find((z) => z.surfaces.includes('scopeControls'))!;
      expect(zone.id).toBe('scope-controls');
      expect(zone.surfaces).toEqual(['scopeControls']);
    },
  );

  // Kills: making scopeControls REQUIRED. A radio the `scope` evidence gate
  // declines must still resolve this layout; the surface self-gates on
  // `view.scopeControls`, and a required surface no zone could fill would be
  // a resolution failure rather than an honest absence.
  it.each(ALL)('%s does not require the scopeControls surface', (_id, manifest) => {
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
