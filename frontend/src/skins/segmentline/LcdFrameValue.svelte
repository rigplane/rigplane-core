<script lang="ts">
  /**
   * MOR-4270: bridges the wiring's readonly LCD frame store (stable
   * identity, per-frame store sets) into a plain value for the segmentline
   * snippets — pixels stay out of `$state` in the wiring component and the
   * subscription lives exactly where the frame renders.
   */
  import type { Readable } from 'svelte/store';
  import type { Snippet } from 'svelte';
  import type { LcdSpectrumFrame } from './lcd-display-contract';

  let { store, children }: {
    store: Readable<LcdSpectrumFrame | null>;
    children: Snippet<[LcdSpectrumFrame | null]>;
  } = $props();

  let frame = $state.raw<LcdSpectrumFrame | null>(null);
  $effect(() => {
    const stop = store.subscribe((value) => { frame = value; });
    return stop;
  });
</script>

{@render children(frame)}
