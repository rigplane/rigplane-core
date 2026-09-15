import type { Snippet } from 'svelte';

export type FilterFiniteChoiceValue = string | number;

/** Fixed filter-shape choice set (SHARP/SOFT) — the one definition, moved
 *  here from `FilterSurface.svelte`'s `<script module>` (MOR-2425 F1-C2) now
 *  that `FilterInstrumentHost` owns the shape seat too. */
export const FILTER_SHAPES = [[0, 'SHARP'], [1, 'SOFT']] as const;

export interface FilterInstrumentHandles {
  readonly mode: Snippet;
  readonly filter: Snippet;
  readonly shape: Snippet;
  readonly dataMode: Snippet;
  readonly standardMode?: Snippet;
  readonly standardDataMode?: Snippet;
}

export type FilterFiniteLayout = Snippet<[FilterInstrumentHandles]>;
