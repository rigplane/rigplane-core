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
 */
import { registerLayout, type LayoutManifest } from './contract';

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
  ],
  compatibleTopologies: ['2/ab_shared', '2/main_sub'],
  requiredSemanticSurfaces: ['vfo', 'rxTx'],
  stageSizing: { mode: 'fluid', responsiveBreakpoints: [] },
  fallbackLayoutId: 'sdr-test',
};

registerLayout(dualReceiverCockpitLayout);
