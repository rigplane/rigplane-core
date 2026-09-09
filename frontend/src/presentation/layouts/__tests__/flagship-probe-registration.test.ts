/**
 * T160 PR-1 — the flagship geometry probe's manifest, and the one agreement
 * that cannot be asserted from the skin's own directory: this file is inside
 * `presentation/layouts/`, which is where `stage-sizing-boundary.test.ts`
 * (MOR-1247) allows the identifier `stageSizing` to appear.
 *
 * The shell's reflow threshold lives in its CSS and the manifest declares the
 * same number; the last test below requires the two to agree.
 * Each test's doc line names the mutation it kills.
 */
import { readFileSync } from 'node:fs';
import { describe, it, expect } from 'vitest';
// The barrel is where this manifest is declared and registered.
import { flagshipProbeLayout } from '../declarations';
import { getLayout, type SemanticSurfaceName } from '../contract';

const SKIN_PATH = 'src/skins/flagship-probe/FlagshipProbeSkin.svelte';
const skinSource = readFileSync(SKIN_PATH, 'utf8');

describe('the flagship-probe registration', () => {
  // Kills: declarations.ts never calling registerLayout for this manifest.
  it('registers through the app-wide barrel', () => {
    expect(getLayout('flagship-probe')).toBe(flagshipProbeLayout);
  });

  // Kills: a zone gaining a second surface, or drifting to an id the skin's
  // style block does not place — the id IS the contract with the arrangement.
  it('declares one surface per zone, under the ids the skin places', () => {
    const placed = new Set(
      [...skinSource.matchAll(/\[data-zone-id='([a-z-]+)'\]/g)].map(([, id]) => id),
    );
    // The deck's two slot strips are placed by `data-strip-slot`, so their
    // zone ids are the only declared ones the style block does not name.
    const deck = new Set(['primary-vfo', 'secondary-vfo']);
    for (const zone of flagshipProbeLayout.zones) {
      expect(zone.surfaces).toHaveLength(1);
      expect(placed.has(zone.id) || deck.has(zone.id)).toBe(true);
    }
    expect([...placed].every((id) => flagshipProbeLayout.zones.some((z) => z.id === id))).toBe(true);
  });

  // Kills: declaring `memory`, the one declarable surface this arrangement
  // leaves out, or dropping one of the fourteen it places.
  it('mounts fourteen of the fifteen declarable surfaces, all but memory', () => {
    const declared = new Set<SemanticSurfaceName>(
      flagshipProbeLayout.zones.flatMap((zone) => [...zone.surfaces]),
    );
    expect([...declared].sort()).toEqual([
      'antenna', 'band', 'cwKeyer', 'dsp', 'filter', 'meters', 'rfFrontEnd', 'ritXitScan',
      'rxAudio', 'rxTx', 'scopeControls', 'scopeDisplay', 'txAux', 'vfo',
    ]);
    expect(declared.has('memory' as SemanticSurfaceName)).toBe(false);
  });

  // Kills: admitting a single-receiver topology.
  it('is compatible with the dual-receiver topologies only', () => {
    expect([...flagshipProbeLayout.compatibleTopologies].sort())
      .toEqual(['2/ab_shared', '2/main_sub']);
  });

  // Kills: the manifest's declared threshold drifting away from the width the
  // shell actually switches at, in either direction.
  it('declares the same reflow width the shell switches at', () => {
    const container = skinSource.match(/@container \(min-width: (\d+)px\)/);
    expect(container).not.toBeNull();
    const switchWidth = skinSource.match(/--flagship-probe-switch-width:\s*(\d+)px/);
    expect(switchWidth).not.toBeNull();
    expect(Number(container![1])).toBe(Number(switchWidth![1]));
    expect(flagshipProbeLayout.stageSizing).toEqual({
      mode: 'fluid', responsiveBreakpoints: [Number(container![1])],
    });
  });
});
