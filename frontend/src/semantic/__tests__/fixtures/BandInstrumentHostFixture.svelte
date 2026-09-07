<script lang="ts">
  import type { Component } from 'svelte';
  import type {
    FiniteControlAppearance, FiniteRendererContext,
  } from '../../../primitives/control-instruments/control-instrument-renderer.svelte';
  import type {
    FrequencyEntryRendererProps,
  } from '../../../primitives/frequency/frequency-entry-renderer.svelte';
  import BandInstrumentHost from '../../BandInstrumentHost.svelte';
  import BandSurface from '../../BandSurface.svelte';
  import type { BandInstrumentHandles } from '../../band-instruments';
  import type { RadioViewModel } from '../../radio-view-model';

  interface Props {
    view: RadioViewModel | null;
    presentation?: 'grouped' | 'independent' | 'surface';
    finiteAppearance?: FiniteControlAppearance<string>;
    rendererContext?: FiniteRendererContext | null;
    entryRendererContext?: FiniteRendererContext | null;
    entryRenderer?: Component<FrequencyEntryRendererProps>;
    onSelectBand?: (name: string) => void;
    onEnterFrequency?: (frequencyHz: number) => void;
  }
  let {
    view, presentation = 'grouped', finiteAppearance, rendererContext,
    entryRendererContext, entryRenderer, onSelectBand, onEnterFrequency,
  }: Props = $props();
  let selection = $derived(finiteAppearance === undefined
    ? {} : { finiteAppearance, rendererContext: rendererContext ?? null });
</script>

<BandInstrumentHost
  {view} {onSelectBand} {onEnterFrequency} {entryRendererContext} {entryRenderer} {...selection}
>
  {#snippet children(handles: BandInstrumentHandles)}
    {#if presentation === 'surface'}
      {#if view}<BandSurface {view} {handles} />{/if}
    {:else}
      <section data-testid={`${presentation}-band-composition`}>
        {#if presentation === 'grouped'}
          {@render handles.bandChoice()}{@render handles.frequencyEntry()}
        {:else}
          <div data-slot="choice">{@render handles.bandChoice()}</div>
          <div data-slot="entry">{@render handles.frequencyEntry()}</div>
        {/if}
      </section>
    {/if}
  {/snippet}
</BandInstrumentHost>
