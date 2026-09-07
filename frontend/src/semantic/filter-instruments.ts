import type { Snippet } from 'svelte';

export type FilterFiniteChoiceValue = string | number;

export interface FilterInstrumentHandles {
  readonly mode: Snippet;
  readonly filter: Snippet;
}

export type FilterFiniteLayout = Snippet<[FilterInstrumentHandles]>;
