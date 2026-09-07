import type { Snippet } from 'svelte';

export const DSP_TOGGLES = [['nrActive', 'NR'], ['nbActive', 'NB']] as const;
export const DSP_NOTCH_MODES = ['off', 'auto', 'manual'] as const;

export type DspToggleField = (typeof DSP_TOGGLES)[number][0];
export type DspNotchMode = (typeof DSP_NOTCH_MODES)[number];
export type DspFiniteChoiceValue = DspNotchMode | number;

export interface DspFiniteHandles {
  readonly nrActive: Snippet;
  readonly nbActive: Snippet;
  readonly notchMode: Snippet;
  readonly agcMode: Snippet;
}

export type DspFiniteLayout = Snippet<[DspFiniteHandles]>;
