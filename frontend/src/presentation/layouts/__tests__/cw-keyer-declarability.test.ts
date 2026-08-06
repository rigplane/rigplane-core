/**
 * MOR-1310 — `cwKeyer` is DECLARABLE, and nothing declares it yet.
 *
 * Slice 9B adds the name to `SEMANTIC_SURFACE_NAMES` so a manifest CAN mount
 * the surface later; it deliberately does not touch any manifest, and it adds
 * no design-language renderer slot (that set was frozen by MOR-1072).
 *
 * Same three pins as `rx-audio-declarability.test.ts` /
 * `tx-aux-declarability.test.ts` / `meters-declarability.test.ts`:
 *   - drop the name and a future manifest's zone stops validating;
 *   - quietly add a `cwKeyer` zone to a shipped manifest and the DOM grows a
 *     zone id no layout review ever saw — which for THIS surface would also
 *     mount break-in controls into the cockpit's tab order without the
 *     MOR-1069 sequence assertion ever being updated;
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

describe('cwKeyer is a declarable semantic surface', () => {
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
      zones: [{ id: 'main', surfaces: ['vfo', 'rxTx', 'cwKeyer'] }],
      requiredSemanticSurfaces: ['vfo', 'rxTx'],
    });
    expect(() => validateLayoutManifest(manifest)).not.toThrow();
  });

  it('still rejects a zone naming a surface that does not exist', () => {
    const manifest = validLayoutManifest({
      zones: [{ id: 'main', surfaces: ['cwPanel'] as unknown as readonly ['vfo'] }],
    });
    expect(() => validateLayoutManifest(manifest)).toThrow(/subset of/);
  });
});

describe('exactly the reviewed manifests declare a cwKeyer zone (MOR-1368)', () => {
  /**
   * The literal — extend by hand, with a layout review, never silently. This
   * is the SAFETY-CRITICAL one: `desktop-v2` is the only family reviewed for
   * it, and the dual-receiver cockpit stays OFF the list deliberately. Its
   * MOR-1069 tab-order assertion is written against the zones it declares
   * today, so adding `cwKeyer` there would put break-in controls in the
   * cockpit with no updated sequence pin.
   */
  const DECLARES_CW_KEYER = ['desktop-v2'];

  const ALL = [
    ['sdr-test', sdrTestLayout], ['dual-receiver-cockpit', dualReceiverCockpitLayout],
    ['lcd-cockpit', lcdCockpitLayout], ['lcd-scope', lcdScopeLayout],
    ['mobile', mobileLayout], ['desktop-v2', desktopV2Layout],
  ] as const;

  // Kills BOTH directions: desktop-v2 losing the zone S9 gave it — which would
  // put `CwPanel` back beside `CwKeyerSurface`, i.e. two break-in affordances
  // disagreeing about one setting — and any other family gaining one without
  // the review above.
  it('the declaring set is exactly the reviewed literal', () => {
    const declaring = ALL
      .filter(([, m]) => m.zones.some((z) => z.surfaces.includes('cwKeyer')))
      .map(([id]) => id)
      .sort();
    expect(declaring).toEqual([...DECLARES_CW_KEYER].sort());
  });

  // Kills: declaring the zone under a drifted id, or folding `cwKeyer` into
  // the `rx-tx` zone — the shape MOR-1310 named as putting break-in choices
  // between the operator and the unkey button.
  it.each(DECLARES_CW_KEYER)('%s declares it under the stable `cw-keyer` id, alone in its zone', (id) => {
    const manifest = ALL.find(([name]) => name === id)![1];
    const zone = manifest.zones.find((z) => z.surfaces.includes('cwKeyer'))!;
    expect(zone.id).toBe('cw-keyer');
    expect(zone.surfaces).toEqual(['cwKeyer']);
    expect(manifest.zones.find((z) => z.id === 'rx-tx')!.surfaces).toEqual(['rxTx']);
  });

  // Kills: making cwKeyer REQUIRED. A radio with no keyer must still resolve
  // this layout; the surface self-gates on `view.cwKeyer`.
  it.each(ALL)('%s does not require the cwKeyer surface', (_id, manifest) => {
    expect(manifest.requiredSemanticSurfaces).not.toContain('cwKeyer');
  });
});

describe('the design-language renderer slot set is untouched', () => {
  // Kills: adding a renderer slot for this surface. A CW keyer has no gauge
  // grammar a language must describe; the slot set stays frozen.
  it('still carries exactly the three MOR-1072 slots', () => {
    expect([...RENDERER_SLOT_NAMES]).toEqual(['meters', 'frequencyDisplay', 'stateFeedback']);
  });
});
