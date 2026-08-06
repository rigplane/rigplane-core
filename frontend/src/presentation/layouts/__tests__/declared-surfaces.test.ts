/**
 * MOR-1313 — the pure half of per-zone legacy suppression.
 *
 * `declaredSurfaces` is the whole mechanism: a shared v2 shell asks the ACTIVE
 * manifest which semantic surfaces its declared zones mount, and drops the
 * legacy twin of exactly those. The rendered consequences are pinned in
 * `components-v2/layout/__tests__/semantic-desktop-migration.component.test.ts`;
 * this file pins the derivation itself, on synthetic manifests the shipped set
 * does not (and should not) contain — the one place the matrix can be walked
 * without inventing a fake skin.
 *
 * Config-free by construction: every case below expresses "this area is
 * semantic" purely by DECLARING the zone. The v1 zone schema is untouched
 * (`{id, surfaces[]}`, risk R3) — there is no per-zone suppression flag to set.
 *
 * Each test's doc line names the mutation it exists to kill.
 */
import { describe, it, expect } from 'vitest';
import {
  declaredSurfaces, type LayoutManifest, type LayoutZone, type SemanticSurfaceName,
} from '../contract';

/** Not registered anywhere — these never touch the real registry. */
function manifest(zones: readonly LayoutZone[]): LayoutManifest {
  return {
    schemaVersion: 1,
    id: 'probe',
    displayName: 'Probe',
    loader: () => Promise.reject(new Error('never loaded')),
    zones,
    compatibleTopologies: ['1/single'],
    requiredSemanticSurfaces: zones[0]?.surfaces ?? ['vfo'],
    stageSizing: { mode: 'fluid', responsiveBreakpoints: [] },
    fallbackLayoutId: null,
  };
}

const sorted = (surfaces: ReadonlySet<SemanticSurfaceName>): string[] => [...surfaces].sort();

describe('declaredSurfaces flattens the zones a layout declares', () => {
  // Kills: reading only the first zone — the shape that would silently leave
  // desktop-v2's second zone (`rx-tx`) unsuppressed while sdr-test, whose
  // single zone carries both surfaces, kept passing.
  it('unions across zones, not just the first', () => {
    expect(sorted(declaredSurfaces(manifest([
      { id: 'receiver-deck', surfaces: ['vfo'] },
      { id: 'rx-tx', surfaces: ['rxTx'] },
    ])))).toEqual(['rxTx', 'vfo']);
  });

  // Kills: a derivation that keys off zone IDS rather than declared surfaces.
  // One zone named nothing like the shell's areas must still suppress both
  // twins — that equivalence is what makes sdr-test the degenerate case of the
  // same rule rather than a second code path.
  it('is zone-name agnostic: one zone declaring both is the same result', () => {
    expect(sorted(declaredSurfaces(manifest([{ id: 'main', surfaces: ['vfo', 'rxTx'] }]))))
      .toEqual(['rxTx', 'vfo']);
  });

  // Kills: suppressing an area whose surface no zone declares — the "keeps its
  // legacy presentation" half of the matrix, which no shipped manifest
  // exercises and which would otherwise be unpinned.
  it('reports only what is declared, never a surface no zone mounts', () => {
    const declared = declaredSurfaces(manifest([{ id: 'receiver-deck', surfaces: ['vfo'] }]));
    expect(declared.has('vfo')).toBe(true);
    expect(declared.has('rxTx')).toBe(false);
    expect(declared.has('meters')).toBe(false);
  });

  // Kills: an unresolvable id resolving to anything other than "declare
  // nothing". This is the fail-safe direction — the shell must fall back to
  // the shipped legacy panels, never to a screen the semantic vertical was
  // never asked to fill.
  it('an unregistered layout declares nothing', () => {
    expect(declaredSurfaces(undefined).size).toBe(0);
  });

  // Kills: returning a live view onto a manifest's zones — the set is derived,
  // and a caller must not be able to mutate a layout declaration through it.
  it('returns a fresh set per call, detached from the manifest', () => {
    const m = manifest([{ id: 'main', surfaces: ['vfo', 'rxTx'] }]);
    const first = declaredSurfaces(m) as Set<SemanticSurfaceName>;
    first.delete('rxTx');
    expect(sorted(declaredSurfaces(m))).toEqual(['rxTx', 'vfo']);
  });
});
