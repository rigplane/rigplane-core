/**
 * MOR-1067 — the dual-receiver-cockpit reference layout: two per-receiver
 * VFO channel strips sharing one shared RX/TX status+action surface
 * ("studioline's natural home", MOR-977 §4.4). A standalone manifest file
 * — `declarations.ts` carries only the one registration import — so this
 * and MOR-1092's LCD entrypoints never touch the same lines.
 *
 * MOR-1068 — the frozen adaptation policy across the four canonical pairs
 * (pinned in `__tests__/cockpit-topology-adaptation.test.ts`):
 *
 *   1/single, 1/ab  FALL BACK. `compatibleTopologies` excludes them, so
 *                   `resolveLayoutForTopology` takes the one validated hop to
 *                   the all-topology `sdr-test` (MOR-976 "degrades safely").
 *                   A single-receiver radio has nothing to put in a second
 *                   strip, and an empty column is a claim about the radio.
 *   2/ab_shared     MOUNTS. One unslotted VFO per receiver; `selectionPoolSize`
 *                   keeps the receiver-selection control present even though
 *                   each per-receiver slice holds a single VFO.
 *   2/main_sub      MOUNTS. Slotted A/B per receiver, two tiles per strip.
 *
 * DEGRADE is the third arm and belongs to the shell, not the declaration: a
 * declared-compatible topology whose second receiver was never observed
 * renders ONE strip and no `secondary-vfo` zone. Nothing fabricates a SUB
 * (`receiversOf` reads `view.vfos`), the radio-wide row still renders exactly
 * once, and the single TX authority is untouched in every arm.
 *
 * `stageSizing: fluid` encodes the MOR-1160 chrome-fluid half of the sizing
 * axis: this shell composes only VFO/RX-TX status text today (no fixed-native
 * instrument glass yet), so it always fits, at any viewport.
 *
 * MOR-1069 — the PORTRAIT-MOBILE RULING, stated rather than defaulted.
 *
 * A `fixed-native` layout is excluded from a portrait phone arithmetically:
 * the achievable uniform scale drops under `minScale` and resolution takes the
 * one validated hop to a fluid fallback. A `fluid` layout has no such
 * exclusion — it fits every viewport by construction — so this cockpit DOES
 * reach portrait mobile, and it reaches it deliberately. It is not a silent
 * default: restoring an exclusion would need either a viewport consumer of the
 * frozen sizing field (banned while the ScaledStage primitive does not exist —
 * `__tests__/stage-sizing-boundary.test.ts`) or a second mobile behavior state
 * machine (banned by MOR-1069's owned area). So the ruling is STACK, NOT
 * EXCLUDE: on a portrait phone the cockpit composes as a single column with
 * touch-sized controls, and `fallbackLayoutId` stays a TOPOLOGY fallback
 * (`sdr-test`, MOR-1068's frozen table), never a viewport one.
 */
import { registerLayout, type LayoutManifest } from './contract';

/**
 * The two reflow thresholds the shell actually implements, recorded honestly
 * — the same discipline MOR-1094 applied to the mobile manifest's single
 * breakpoint. Widths, in px: `<768` compact, `768-1023` tablet, `>=1024`
 * desktop, matching the app's existing tablet band in
 * `components-v2/layout/responsive.css`. Declaration only, as the field's
 * contract requires: the media queries in
 * `skins/dual-receiver-cockpit/DualReceiverCockpit.svelte` are what reflows,
 * and `__tests__/cockpit-responsive-composition.test.ts` requires the two
 * descriptions to name the same numbers in both directions.
 */
const COCKPIT_REFLOW_BREAKPOINTS = [768, 1024] as const;

export const dualReceiverCockpitLayout: LayoutManifest = {
  schemaVersion: 1,
  id: 'dual-receiver-cockpit',
  displayName: 'Dual Receiver Cockpit',
  loader: () => import('../../skins/dual-receiver-cockpit/DualReceiverCockpit.svelte'),
  // Declaration order IS rendered order, pinned end to end against the
  // mounted shell (MOR-1067 verification F6 — before this, not one id was
  // shared with the DOM and nothing asserted the correspondence). `global`
  // mounts the `vfo` surface's radio-wide half (split / dual-watch / active
  // receiver), which belongs to no receiver's column. The shell's remaining
  // `scope`/`controls` placeholders are deliberately NOT declared: a
  // `LayoutZone` must mount at least one semantic surface and MOR-1062/1065
  // ship none for them, so declaring them would claim a mount that cannot
  // happen.
  zones: [
    { id: 'primary-vfo', surfaces: ['vfo'] },
    { id: 'secondary-vfo', surfaces: ['vfo'] },
    { id: 'global', surfaces: ['vfo'] },
    { id: 'rx-tx', surfaces: ['rxTx'] },
    // MOR-1336 (S4): the cockpit declares it too, so the MOR-1069 invariant has a
    // live txAux zone to bite on in the DUAL composition — where the surface's
    // three focusable controls previously sat outside every zone.
    { id: 'tx-aux', surfaces: ['txAux'] },
  ],
  compatibleTopologies: ['2/ab_shared', '2/main_sub'],
  requiredSemanticSurfaces: ['vfo', 'rxTx'],
  stageSizing: { mode: 'fluid', responsiveBreakpoints: COCKPIT_REFLOW_BREAKPOINTS },
  fallbackLayoutId: 'sdr-test',
};

registerLayout(dualReceiverCockpitLayout);
