<script lang="ts">
  import type { FilterFiniteLayout, FilterInstrumentHandles } from './filter-instruments';

  let { handles, finiteLayout }: {
    handles: FilterInstrumentHandles;
    finiteLayout?: FilterFiniteLayout;
  } = $props();
</script>

<section class="mode-surface" data-testid="mode-surface" aria-label="Mode controls">
  {#if finiteLayout}
    {@render finiteLayout(handles)}
  {:else}
    {@render handles.mode()}
    {@render handles.dataMode()}
  {/if}
</section>

<style>
  .mode-surface { display: flex; flex-direction: column; gap: 0.25rem; }
</style>
