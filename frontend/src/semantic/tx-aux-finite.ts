import type { Snippet } from 'svelte';

export const TX_AUX_TOGGLES = [
  ['atu', 'ATU'],
  ['vox', 'VOX'],
  ['compressor', 'COMP'],
  ['monitor', 'MON'],
] as const;

export type TxAuxToggleField = (typeof TX_AUX_TOGGLES)[number][0];

export type TxAuxFiniteHandles = Readonly<{
  atu: Snippet;
  vox: Snippet;
  compressor: Snippet;
  monitor: Snippet;
  atuTune: Snippet;
}>;
