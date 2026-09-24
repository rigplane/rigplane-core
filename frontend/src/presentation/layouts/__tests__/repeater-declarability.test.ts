/**
 * MOR-2111 — `repeater` is DECLARABLE. No manifest declares it yet.
 *
 * This slice adds the name to `SEMANTIC_SURFACE_NAMES` so a manifest CAN
 * mount the surface later; it touches no manifest and adds no design-language
 * renderer slot (that set was frozen by MOR-1072). The inventory below is a
 * LITERAL of who declares it, mirroring `rf-front-end-declarability.test.ts`.
 */
import { describe, it, expect } from 'vitest';
import { SEMANTIC_SURFACE_NAMES, validateLayoutManifest } from '../contract';
import { RENDERER_SLOT_NAMES } from '../../languages/contract';
import { validLayoutManifest } from './fixtures';
import { isLayoutManifest } from './manifest-guard';
import * as layoutDeclarationsBarrel from '../declarations';

describe('repeater is a declarable semantic surface', () => {
  // Kills: reverting the SEMANTIC_SURFACE_NAMES addition, or reordering the
  // existing names.
  it('is in the declarable set, after memory', () => {
    expect([...SEMANTIC_SURFACE_NAMES]).toEqual([
      'vfo', 'rxTx', 'txAux', 'meters', 'rxAudio', 'filter', 'dsp', 'rfFrontEnd', 'band',
      'antenna', 'ritXitScan', 'cwKeyer', 'scopeDisplay', 'scopeControls', 'memory', 'repeater',
    ]);
  });

  // Kills: adding the name to the type but not to the runtime allow-list the
  // zone validator checks — the manifest would still be rejected.
  it('accepts a manifest zone that declares it', () => {
    const manifest = validLayoutManifest({
      zones: [{ id: 'main', surfaces: ['vfo', 'rxTx', 'repeater'] }],
      requiredSemanticSurfaces: ['vfo', 'rxTx'],
    });
    expect(() => validateLayoutManifest(manifest)).not.toThrow();
  });

  it('still rejects a zone naming a surface that does not exist', () => {
    const manifest = validLayoutManifest({
      zones: [{ id: 'main', surfaces: ['repeaterPanel'] as unknown as readonly ['vfo'] }],
    });
    expect(() => validateLayoutManifest(manifest)).toThrow(/subset of/);
  });
});

describe('exactly the reviewed manifests declare a repeater zone (MOR-2111)', () => {
  /** The literal — extend by hand, with a layout review, never silently. */
  const DECLARES_REPEATER: readonly string[] = [];

  // [id, manifest] pairs derived from the barrel's export surface
  // (MOR-2061) — never hand-listed. See `manifest-guard.ts`.
  const ALL = Object.values(layoutDeclarationsBarrel)
    .filter(isLayoutManifest)
    .map((m) => [m.id, m] as const);

  // Kills: a manifest gaining a repeater zone without review.
  it('the declaring set is exactly the reviewed literal', () => {
    const declaring = ALL
      .filter(([, m]) => m.zones.some((z) => z.surfaces.includes('repeater')))
      .map(([id]) => id)
      .sort();
    expect(declaring).toEqual([...DECLARES_REPEATER].sort());
  });

  // Kills: making repeater REQUIRED. The surface exists only on radios whose
  // profile flags a repeater band, so a required surface would fail every
  // other radio's layout resolution.
  it.each(ALL)('%s does not require the repeater surface', (_id, manifest) => {
    expect(manifest.requiredSemanticSurfaces).not.toContain('repeater');
  });
});

describe('the design-language renderer slot set is untouched', () => {
  // Kills: adding a renderer slot for this surface.
  it('still carries exactly the three MOR-1072 slots', () => {
    expect([...RENDERER_SLOT_NAMES]).toEqual(['meters', 'frequencyDisplay', 'stateFeedback']);
  });
});
