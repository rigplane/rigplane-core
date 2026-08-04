/**
 * MOR-1068 — the dual-receiver cockpit's ADAPTATION POLICY across all four
 * canonical topology pairs, resolved through the REAL registry (MOR-1066),
 * plus the two gaps the MOR-1067 adversarial verification handed to this
 * ticket: F8 (SkinId reachability) and the fixture-level survival of
 * `scope=false + audioFft=true` as operational audio-scope availability.
 *
 * The DOM half of the policy (what the shell actually renders, and the F6
 * manifest-zone <-> DOM binding) lives in
 * `skins/dual-receiver-cockpit/__tests__/DualReceiverCockpit.component.test.ts`
 * — that tree needs the isolated jsdom pool; this file is pure.
 *
 * Each test's doc line names the mutation it exists to kill.
 */
import { readFileSync } from 'node:fs';
import { describe, it, expect } from 'vitest';
import {
  getLayout, resolveLayoutForTopology, supportsTopology,
  TOPOLOGY_CLASSES, type LayoutManifest, type TopologyClass,
} from '../contract';
// Barrel-only import, never '../dual-receiver-cockpit' — a direct manifest
// import would fire `registerLayout` from this test file, masking a
// `declarations.ts` that no longer wires the cockpit into the app, and under
// the fast pool's `isolate: false` it would leak that registration into
// sibling files (the MOR-1092 lesson, restated on MOR-1067).
import {
  desktopV2Layout, dualReceiverCockpitLayout, lcdCockpitLayout, lcdScopeLayout, mobileLayout,
  sdrTestLayout,
} from '../declarations';
import { forReceiver, receiversOf } from '../../../components-v2/wiring/dual-receiver-strips';
import { topologyFixtures, withAudioOnlyScope } from '../../../semantic/fixtures/topologies';

/**
 * The frozen policy table. Column 3 is what `compatibleTopologies` must say;
 * column 2 is what a consumer asking for the cockpit ACTUALLY gets. The two
 * single-receiver pairs degrade by resolution (one validated hop to the
 * all-topology `sdr-test`), never by mounting a cockpit whose second strip
 * has nothing to put in it.
 */
const POLICY: readonly (readonly [TopologyClass, string, boolean])[] = [
  ['1/single', 'sdr-test', false],
  ['1/ab', 'sdr-test', false],
  ['2/ab_shared', 'dual-receiver-cockpit', true],
  ['2/main_sub', 'dual-receiver-cockpit', true],
];

const FIXTURE_IDS = ['1/single', '1/ab', '2/ab_shared', '2/main_sub'] as const;

describe('the four-pair adaptation policy, through the real registry', () => {
  // Kills: widening compatibleTopologies to a single-receiver pair (the
  // cockpit would then mount with an empty second strip), or narrowing it
  // away from a dual pair (a real dual-receiver radio would silently lose
  // the layout built for it — the MOR-1067 F1 mirror: live presenting as
  // absent).
  it.each(POLICY)('%s resolves to "%s" (declared-compatible: %s)', (topology, expectedId, mounts) => {
    expect(supportsTopology(dualReceiverCockpitLayout, topology)).toBe(mounts);
    expect(resolveLayoutForTopology('dual-receiver-cockpit', topology)?.id).toBe(expectedId);
  });

  // Kills: dropping `fallbackLayoutId`, or pointing it at a layout that does
  // not itself cover the refused pairs — resolution would return undefined
  // and a single-receiver radio would get NO layout at all.
  it('leaves no canonical topology unresolvable', () => {
    for (const topology of TOPOLOGY_CLASSES) {
      expect(resolveLayoutForTopology('dual-receiver-cockpit', topology)).toBeDefined();
    }
  });

  // Kills: a fallback that is decoration rather than policy. Whatever the
  // cockpit refuses, the declared fallback must actually support.
  it('the declared fallback covers exactly the pairs the cockpit refuses', () => {
    const refused = TOPOLOGY_CLASSES.filter((t) => !supportsTopology(dualReceiverCockpitLayout, t));
    expect(refused).toEqual(['1/single', '1/ab']);
    expect(dualReceiverCockpitLayout.fallbackLayoutId).toBe('sdr-test');
    for (const topology of refused) expect(supportsTopology(sdrTestLayout, topology)).toBe(true);
  });
});

describe('degrade honesty: the strip slicing never invents a receiver', () => {
  // Kills: hardcoding two strips. Read against all four approved fixtures,
  // so a cockpit mounted on a degraded (single-receiver) view model gets one
  // strip and no fabricated SUB — "no impossible receiver/VFO labels".
  it.each(FIXTURE_IDS)('%s: yields exactly the receivers the fixture observes', (id) => {
    const view = topologyFixtures[id];
    const expectedCount = Number(id.split('/')[0]);
    const receivers = receiversOf(view);
    expect(receivers).toHaveLength(expectedCount);
    expect(receivers.every((rx) => view.vfos.some((v) => v.receiver === rx))).toBe(true);
    expect(expectedCount === 1 ? receivers : []).not.toContain('SUB');
  });
});

describe('audio-only scope survives the cockpit slicing (scope=false + audioFft=true)', () => {
  // Kills: `forReceiver` touching anything but `vfos` — the exact MOR-1067
  // F1 mirror at fixture level. An operational audio-FFT scope must reach
  // every strip's view model intact, never flattened to "no scope".
  it.each(FIXTURE_IDS)('%s: every per-receiver slice keeps operational audio-FFT scope', (id) => {
    const view = withAudioOnlyScope(topologyFixtures[id]);
    for (const receiver of receiversOf(view)) {
      expect(forReceiver(view, receiver).scope).toEqual({
        hardwareScope: { structural: false, operational: false },
        audioFftScope: { structural: true, operational: true },
      });
    }
  });
});

// MOR-1067 verification F8: `skins/registry.ts` had no `dual-receiver-cockpit`
// SkinId and no loader, so the cockpit was the only registered layout manifest
// the App could not load. `lcd-registration.test.ts` pins the same rule for
// `lcd-*` ids only; this generalizes it to every real manifest. Read as TEXT,
// not imported: `skins/registry.ts` pulls in the layout preference store,
// which touches `localStorage` at module scope.
describe('F8 — every registered layout manifest names a loadable skin', () => {
  // MOR-1266 adds desktop-v2 to the generalized set — the rule this ticket's
  // acceptance criteria requires to stay green for every newly registered
  // manifest, not just the ones that existed when F8 was written.
  const REAL_LAYOUTS: readonly LayoutManifest[] = [
    sdrTestLayout, dualReceiverCockpitLayout, lcdCockpitLayout, lcdScopeLayout, mobileLayout,
    desktopV2Layout,
  ];
  const source = readFileSync('src/skins/registry.ts', 'utf8');
  const loadersStart = source.indexOf('const SKIN_LOADERS');
  const loaderIds = [...source.slice(loadersStart, source.indexOf('};', loadersStart))
    .matchAll(/'([a-z0-9-]+)':/g)].map((m) => m[1]);
  const unionStart = source.indexOf('export type SkinId');
  const skinIdUnion = source.slice(unionStart, source.indexOf(';', unionStart));

  it.each(REAL_LAYOUTS.map((m) => [m.id, m] as const))(
    '"%s" is registered and reachable as a SkinId with a loader', (id, manifest) => {
      expect(getLayout(id)).toBe(manifest);
      expect(skinIdUnion).toContain(`'${id}'`);
      expect(loaderIds).toContain(id);
    },
  );

  // Kills: closing F8 by deleting the manifest instead of adding the skin.
  it('names the dual-receiver cockpit specifically (the F8 gap)', () => {
    expect(loaderIds).toContain('dual-receiver-cockpit');
    expect(skinIdUnion).toContain("'dual-receiver-cockpit'");
  });
});
