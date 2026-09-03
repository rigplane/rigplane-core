/**
 * MOR-2151 — the `peer-split` presentation entrypoint, registered as a v1
 * layout manifest (MOR-1066) and resolved through the REAL registry, not a
 * fixture registry and not a stub loader.
 *
 * Mirrors `sdr-registration.test.ts` (MOR-1093)'s shape for a single
 * manifest with no sibling family: topology honesty, the sizing axis, and
 * — because this slice ships one minimal zone, not the finished
 * composition — that the declared zone is exactly `vfo`+`rxTx`. Every claim
 * is read back out of the shared registry rather than off the exported
 * object, so a manifest that is written but never registered fails here.
 * Each test's doc line names the mutation it exists to kill.
 */
import { readFileSync } from 'node:fs';
import { describe, it, expect } from 'vitest';
import {
  getLayout, listLayoutIds, resolveLayoutForTopology, resolveLayoutForViewport,
  TOPOLOGY_CLASSES,
} from '../contract';
import { getGroup, listGroupIds } from '../../groups/contract';
// Deliberately through the shared aggregation entry, not `../segmentline-
// declarations` directly: it pins that `declarations.ts` really registers
// `peerSplitLayout` (nothing else does), and keeps this fast-pool file on
// the SAME module entry point as `registry.test.ts` — under `isolate: false`
// a second entry into a module that registers at import time is how a split
// module graph (and a phantom "layout not registered") happens. See
// vite.config.ts #771 and `sdr-registration.test.ts`'s identical note.
import { peerSplitLayout } from '../declarations';
// MOR-2253 slice 1: the zone's `group` reference, read back from the same
// declaration the manifest points at rather than a hand-typed literal id, so
// a future rename of `peer-split-glass` cannot silently desync this pin.
import { peerSplitGlassGroup } from '../../groups/declarations';
// MOR-2151 review round: a new zone id has no guard of its own in this
// ticket's files — it was caught only by `workspace/__tests__/contract.
// test.ts`'s cross-file derivation (`WORKSPACE_ZONE_IDS` must equal the set
// of every zone id every registered layout manifest declares), a file
// nothing here otherwise touches. Imported for a read-only assertion below,
// turning that cross-file accident into a local pin.
import { WORKSPACE_ZONE_IDS } from '../../workspace/contract';

describe('the peer-split entrypoint is registered in the real registry', () => {
  // Kills: segmentline-declarations.ts defining the manifest but never
  // calling registerLayout — the resolution below would then read undefined.
  it('registers "peer-split" under its stable entrypoint id', () => {
    expect(getLayout('peer-split')).toBe(peerSplitLayout);
    expect(peerSplitLayout.id).toBe('peer-split');
  });

  // Kills: removing or renaming the 'peer-split' key in SKIN_LOADERS — the
  // manifest id would then have no addressable skin to activate under. Not
  // pinned: which module the key imports. MOR-2153 PR-1 retargeted it from
  // `segmentline/PeerSplitLayout.svelte` to the LCD-shell wrapper
  // `lcd-peer-split/LcdPeerSplitSkin.svelte` — the manifest declares no
  // opinion on which component sits behind the SkinId, only that one does.
  // `PeerSplitLayout.svelte`'s own reachability is pinned separately, by
  // `loader-identity-inventory.test.ts`'s `EXPECTED_LOADER_SPECIFIER['peer-split']`
  // — not by the next test below (`declares a compiled loader`), which only
  // asserts `typeof peerSplitLayout.loader === 'function'` and cannot tell
  // that loader from one pointing at any other loadable module.
  it('keeps a `peer-split` key in the skin registry loader table', () => {
    const registrySource = readFileSync('src/skins/registry.ts', 'utf8');
    expect(registrySource).toMatch(/'peer-split':\s*\(\)\s*=>\s*import\(/);
  });

  // Kills: a manifest that declares no compiled loader at all.
  it('declares a compiled loader', () => {
    expect(typeof peerSplitLayout.loader).toBe('function');
  });
});

describe('declared semantic zones (minimal registration — MOR-2151)', () => {
  // Kills: declaring a surface the layout does not mount, dropping rxTx, or
  // shipping the archived draft's richer zone set (ten zones, three of them
  // all naming `vfo` alone) instead of this slice's single minimal zone.
  it('mounts vfo + rxTx in exactly one zone, under the stable `peer-columns` id', () => {
    expect(peerSplitLayout.zones).toEqual([
      { id: 'peer-columns', surfaces: ['vfo', 'rxTx'], group: peerSplitGlassGroup.id },
    ]);
    expect([...peerSplitLayout.requiredSemanticSurfaces].sort()).toEqual(['rxTx', 'vfo']);
  });

  // Kills: introducing a new zone id here without also registering it in
  // `WORKSPACE_ZONE_IDS` (`presentation/workspace/contract.ts`) — the
  // omission this review round found live. That file's own
  // `zone ids are exactly the zones every registered layout manifest
  // declares` test would still catch the drift, but only after this
  // manifest is already registered into the shared registry; this pins it
  // locally too.
  it('declares its zone id in the workspace zone-id registry', () => {
    for (const zone of peerSplitLayout.zones) {
      expect(WORKSPACE_ZONE_IDS as readonly string[]).toContain(zone.id);
    }
  });
});

// The structural half of the instrument-group ADR's "declared once" guard
// (`docs/plans/2026-09-02-instrument-group-adr.md` §4): every manifest zone
// referencing a group must agree with that group's own canvas/minScale. Read
// back out of the shared registries (never hardcoding 1280/540/0.5 itself),
// so a manifest and its referenced group disagreeing is what turns this red.
//
// Lives here rather than beside the group's own validator/registry tests
// (`../../groups/__tests__/contract.test.ts`) because it must read
// `LayoutManifest.stageSizing`, and `./stage-sizing-boundary.test.ts`'s own
// MOR-1247 tripwire fails any file OUTSIDE `presentation/layouts/` that
// names `stageSizing` at all — a file under `presentation/groups/__tests__/`
// is exactly such a file (found by running the check there once and reading
// the tripwire red).
describe('the native canvas value equals the group the zone references (ADR §4)', () => {
  // Kills: retargeting a manifest's stageSizing to a group by NAME
  // (zone.group) without also retargeting its actual nativeW/nativeH/
  // minScale VALUES to come from that same group — the two could drift
  // independently.
  it('every zone referencing a group agrees with that group\'s own canvas and minScale', () => {
    const referencingZones = listLayoutIds()
      .map((id) => getLayout(id)!)
      .flatMap((manifest) => manifest.zones.map((zone) => ({ manifest, zone })))
      .filter(({ zone }) => zone.group !== undefined);

    // Kills: the whole check vacuously passing because no manifest zone
    // actually references a group (e.g. the reference wiring never lands).
    expect(referencingZones.length).toBeGreaterThan(0);

    for (const { manifest, zone } of referencingZones) {
      const group = getGroup(zone.group!);
      expect(group, `zone "${zone.id}" on layout "${manifest.id}" references unregistered group "${zone.group}"`)
        .toBeDefined();
      expect(manifest.stageSizing.mode).toBe('fixed-native');
      expect(group!.scaling.mode).toBe('fixed-native');
      if (manifest.stageSizing.mode !== 'fixed-native' || group!.scaling.mode !== 'fixed-native') continue;
      expect(manifest.stageSizing.nativeW).toBe(group!.canvas.w);
      expect(manifest.stageSizing.nativeH).toBe(group!.canvas.h);
      expect(manifest.stageSizing.minScale).toBe(group!.scaling.minScale);
    }
  });
});

// ADR §7's registry-derived-inventory shape, applied to the reverse
// reference itself: `validateZones` (`../contract.ts`) checks `zone.surfaces`
// against `SEMANTIC_SURFACE_NAMES`, but `zone.group` is checked against
// nothing there — it is a plain `string` (ADR §4's own precedent: `id`/
// `zone` are both undecorated `string` in the illustrative schema), so a
// typo'd or stale id silently resolves to `undefined` at runtime with no
// validator to catch it. Not validated inside `../contract.ts` itself: that
// would need a VALUE import of the groups registry, and a type-only import
// of it already pulled `presentation/groups/contract.ts` into the workspace
// purity closure (MOR-1077/78/79) once already — this test is the guard
// instead, kept as a test-only cross-registry read.
//
// Both sides are derived, never hand-listed: the declared side walks every
// registered manifest's zones (not just peer-split's), and the registered
// side is `listGroupIds()` itself — so a future group-referencing zone or a
// renamed group id needs no matching edit here.
describe('every zone-declared group id resolves in the registry (ADR §7 inventory shape)', () => {
  // Kills: a `zone.group` value that names an id no `InstrumentGroup` ever
  // registers (a typo, or a stale id left behind by a rename on one side
  // only) — `validateZones` has no bounded-vocabulary check for this field,
  // so nothing else in the manifest contract would catch it.
  it('every zone-declared group id is in listGroupIds()', () => {
    const declaredGroupIds = listLayoutIds()
      .map((id) => getLayout(id)!)
      .flatMap((manifest) => manifest.zones)
      .map((zone) => zone.group)
      .filter((group): group is string => group !== undefined);

    // Kills: the whole check vacuously passing because no manifest zone
    // actually declares a group.
    expect(declaredGroupIds.length).toBeGreaterThan(0);

    const registeredGroupIds = listGroupIds();
    for (const groupId of declaredGroupIds) {
      expect(registeredGroupIds, `zone declares group "${groupId}", which listGroupIds() does not contain`)
        .toContain(groupId);
    }
  });
});

describe('topology honesty', () => {
  // Kills: widening compatibleTopologies to a single-receiver pair — the
  // dual composition PeerSplitLayout.svelte mounts has nothing to put in a
  // second column for those.
  it('resolves itself on the two dual-receiver topologies', () => {
    expect(resolveLayoutForTopology('peer-split', '2/ab_shared')?.id).toBe('peer-split');
    expect(resolveLayoutForTopology('peer-split', '2/main_sub')?.id).toBe('peer-split');
  });

  // Kills: narrowing away from a single-receiver pair's declared fallback —
  // resolution would return undefined and a single-receiver radio would get
  // no layout at all from this entrypoint.
  it('falls back to lcd-cockpit on the two single-receiver topologies', () => {
    expect(resolveLayoutForTopology('peer-split', '1/single')?.id).toBe('lcd-cockpit');
    expect(resolveLayoutForTopology('peer-split', '1/ab')?.id).toBe('lcd-cockpit');
  });

  it('leaves no canonical topology unresolvable', () => {
    for (const topology of TOPOLOGY_CLASSES) {
      expect(resolveLayoutForTopology('peer-split', topology)).toBeDefined();
    }
  });
});

describe('sizing axis — peer-split shares the LCD family\'s fixed-native glass', () => {
  // Kills: drifting off the 1280x540/0.5 stage `docs/plans/2026-09-01-
  // segmentline-peer-split.md` §9 declares for this family.
  it('declares the frozen fixed-native stage', () => {
    expect(peerSplitLayout.stageSizing).toEqual({
      mode: 'fixed-native', nativeW: 1280, nativeH: 540, minScale: 0.5,
    });
  });

  it('resolves on a desktop viewport', () => {
    expect(resolveLayoutForViewport('peer-split', { width: 1440, height: 900 })?.id).toBe('peer-split');
  });

  // Kills: minScale set to 0 (or the mode flipped to fluid), which would let
  // a fixed-native peer-split resolve on portrait mobile.
  it('is excluded from portrait mobile arithmetically', () => {
    expect(resolveLayoutForViewport('peer-split', { width: 390, height: 844 })).toBeUndefined();
  });
});

describe('fallback to lcd-cockpit (MOR-2151 correction — the archived draft named unified-instrument)', () => {
  // Kills: reverting to the archived draft's fallbackLayoutId, which names a
  // layout this slice does not build.
  it('declares lcd-cockpit as its one fallback hop', () => {
    expect(peerSplitLayout.fallbackLayoutId).toBe('lcd-cockpit');
    expect(getLayout(peerSplitLayout.fallbackLayoutId!)).toBeDefined();
  });

  // Kills: returning the fallback without re-applying the criterion — both
  // layouts share the same native stage, so a viewport that fails peer-split
  // fails lcd-cockpit too; resolution must report "unresolvable", not hand
  // back a sibling that fails the same gate.
  it('does not hand back lcd-cockpit for a viewport that fails it too', () => {
    expect(resolveLayoutForViewport('peer-split', { width: 390, height: 844 })).toBeUndefined();
  });
});
