/**
 * The one real registration proof MOR-1066 acceptance requires: the
 * existing `sdr-test` skin, which already mounts the two live semantic
 * zones (MOR-1065 VFO + RX/TX reference vertical), registers as a v1
 * layout manifest with no change to `skins/sdr-test/SdrTestSkin.svelte` or
 * `components-v2/wiring/SemanticRadioSurfaces.svelte`. MOR-1092/93/94
 * migrate the remaining skins the same way, purely additively.
 */
import { registerLayout, type LayoutManifest } from './contract';

export const sdrTestLayout: LayoutManifest = {
  schemaVersion: 1,
  id: 'sdr-test',
  displayName: 'SDR Test',
  loader: () => import('../../skins/sdr-test/SdrTestSkin.svelte'),
  zones: [{ id: 'main', surfaces: ['vfo', 'rxTx'] }],
  compatibleTopologies: ['1/single', '1/ab', '2/ab_shared', '2/main_sub'],
  requiredSemanticSurfaces: ['vfo', 'rxTx'],
  sizing: { mode: 'fluid', responsiveBreakpoints: [] },
  fallbackLayoutId: null,
};

registerLayout(sdrTestLayout);

export { lcdCockpitLayout, lcdScopeLayout } from './lcd-declarations';
