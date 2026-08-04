/**
 * MOR-1067 — the dual-receiver-cockpit reference layout: two per-receiver
 * VFO channel strips sharing one shared RX/TX status+action surface
 * ("studioline's natural home", MOR-977 §4.4). A standalone manifest file
 * — `declarations.ts` carries only the one registration import — so this
 * and MOR-1092's LCD entrypoints never touch the same lines.
 *
 * `compatibleTopologies` deliberately excludes the two single-receiver
 * classes: a single-receiver radio has nothing to put in a second strip, so
 * this layout must not mount for one — `fallbackLayoutId` sends it to the
 * already-registered, all-topology `sdr-test` layout instead (MOR-976
 * "degrades safely" acceptance). `sizing: fluid` encodes the MOR-1160
 * chrome-fluid half of the sizing axis: this shell composes only VFO/RX-TX
 * status text today (no fixed-native instrument glass yet), so it always
 * fits, at any viewport.
 */
import { registerLayout, type LayoutManifest } from './contract';

export const dualReceiverCockpitLayout: LayoutManifest = {
  schemaVersion: 1,
  id: 'dual-receiver-cockpit',
  displayName: 'Dual Receiver Cockpit',
  loader: () => import('../../skins/dual-receiver-cockpit/DualReceiverCockpit.svelte'),
  zones: [
    { id: 'primary-vfo', surfaces: ['vfo'] },
    { id: 'secondary-vfo', surfaces: ['vfo'] },
    { id: 'rx-tx', surfaces: ['rxTx'] },
  ],
  compatibleTopologies: ['2/ab_shared', '2/main_sub'],
  requiredSemanticSurfaces: ['vfo', 'rxTx'],
  sizing: { mode: 'fluid', responsiveBreakpoints: [] },
  fallbackLayoutId: 'sdr-test',
};

registerLayout(dualReceiverCockpitLayout);
