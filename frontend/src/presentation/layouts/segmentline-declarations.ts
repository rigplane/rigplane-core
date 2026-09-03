/**
 * MOR-2151 — `peer-split` as a v1 layout manifest (schema, validator and
 * registry: `./contract.ts`, MOR-1066). The first of the externally
 * authored `segmentline` amber-glass directions to land; the other two
 * (`unified-instrument`, `panadapter-first`) are excluded from this slice —
 * see `docs/plans/2026-09-01-segmentline-peer-split.md` §3.1/§8.
 *
 * The FTX-1 has two genuinely different receivers (MAIN: HF, SUB: separate
 * VHF/UHF) — not a VFO A/B swap. `peer-split` is the direction that says
 * that about the radio: two equal columns, symmetry first.
 *
 * This is a minimal registration, not the finished composition. It declares
 * exactly one zone (`vfo` + `rxTx`), the same shape `sdr-test` calls `main`
 * and the LCD family calls `control-column` — deliberately, per the spec's
 * delivery order (§7): the twelve OPTIONAL semantic surfaces each have a
 * hand-listed `*-declarability.test.ts` inventory that a new declaring
 * manifest must be added to by hand; `vfo`/`rxTx` have no such file. The
 * richer zone set (the rails now mountable in the dual composition since
 * MOR-2150) is deferred to a later PR, per the same spec.
 *
 * The archived handoff's `peerSplitLayout` draft (never executed by its
 * author) needed three corrections:
 *   1. `fallbackLayoutId` named `unified-instrument`, which is not being
 *      built in this slice — retargeted to `lcd-cockpit`, where the
 *      persisted amber preference already routes.
 *   2. The draft's `dsp-rail`/`front-end-rail`/`offsets`/`band-rail` zone
 *      ids did not match this repo's stable-id convention
 *      (`dsp`/`rf-front-end`/`rit-xit-scan`/`band`) — moot here, since none
 *      of those zones are declared in this minimal slice.
 *   3. The draft's ten zones (three of them all carrying `vfo` alone) are
 *      collapsed to the one minimal zone below.
 */
import { registerLayout, type LayoutManifest } from './contract';
import { peerSplitGlassGroup } from '../groups/declarations';

/**
 * The `segmentline` family's canonical glass. Its native size and minScale
 * are the `peer-split-glass` instrument group's own declaration
 * (`../groups/declarations.ts`, MOR-2253 slice 1), read by reference here —
 * this used to duplicate `PeerSplitLayout.svelte`'s own `NATIVE_W`/
 * `NATIVE_H` constants as a second, independent literal (instrument-group
 * ADR §4, F2); both now point at the one group declaration instead.
 * `LCD_NATIVE_STAGE` (`lcd-declarations.ts`) happens to agree with this
 * group's numbers, but stays its own separate literal (ADR §4): `lcd-
 * cockpit`/`lcd-scope` are not groups until a later migration slice.
 */
const SEGMENTLINE_GLASS_STAGE = {
  mode: 'fixed-native',
  nativeW: peerSplitGlassGroup.canvas.w,
  nativeH: peerSplitGlassGroup.canvas.h,
  minScale: peerSplitGlassGroup.scaling.minScale,
} as const;

export const peerSplitLayout: LayoutManifest = {
  schemaVersion: 1,
  id: 'peer-split',
  displayName: 'Peer Split',
  loader: () => import('../../skins/segmentline/PeerSplitLayout.svelte'),
  // One zone: `vfo` + `rxTx`, mounted by `PeerSplitLayout.svelte`'s
  // `<SemanticRadioSurfaces strips="dual" />` (MOR-2155). The dual
  // composition hardcodes its own `primary-vfo`/`secondary-vfo`/`global`/
  // `rx-tx` DOM zone ids regardless of what a manifest declares here (spec
  // §4) — this id is a manifest-level label, not a DOM binding. `group`
  // (MOR-2253 slice 1) is the reverse reference the instrument-group ADR §4
  // adds: the shell resolves `peer-split-glass` through this zone instead of
  // hardcoding the group id.
  zones: [{ id: 'peer-columns', surfaces: ['vfo', 'rxTx'], group: peerSplitGlassGroup.id }],
  // FTX-1's `2/ab_shared` topology is one of these two; a single-receiver
  // radio has nothing to put in a second column (spec §2).
  compatibleTopologies: ['2/ab_shared', '2/main_sub'],
  requiredSemanticSurfaces: ['vfo', 'rxTx'],
  stageSizing: SEGMENTLINE_GLASS_STAGE,
  fallbackLayoutId: 'lcd-cockpit',
};

registerLayout(peerSplitLayout);
