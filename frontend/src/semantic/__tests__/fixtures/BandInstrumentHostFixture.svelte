<script lang="ts">
  import type {
    FiniteControlAppearance, FiniteRendererContext,
  } from '../../../primitives/control-instruments/control-instrument-renderer.svelte';
  import BandInstrumentHost from '../../BandInstrumentHost.svelte';
  import BandSurface from '../../BandSurface.svelte';
  import type { BandInstrumentHandles } from '../../band-instruments';
  import type { RadioViewModel } from '../../radio-view-model';

  interface Props {
    view: RadioViewModel | null;
    presentation?: 'grouped' | 'independent' | 'surface';
    finiteAppearance?: FiniteControlAppearance<string>;
    rendererContext?: FiniteRendererContext | null;
    onSelectBand?: (name: string) => void;
    onEnterFrequency?: (frequencyHz: number) => void;
  }
  let {
    view, presentation = 'grouped', finiteAppearance, rendererContext,
    onSelectBand, onEnterFrequency,
  }: Props = $props();
  let selection = $derived(finiteAppearance === undefined
    ? {} : { finiteAppearance, rendererContext: rendererContext ?? null });
</script>

<BandInstrumentHost {view} {onSelectBand} {...selection}>
  {#snippet children(handles: BandInstrumentHandles)}
    {#if presentation === 'surface'}
      {#if view}<BandSurface {view} {handles} {onEnterFrequency} />{/if}
    {:else}
      <section data-testid={`${presentation}-band-composition`}>
        {#if presentation === 'grouped'}
          {@render handles.bandChoice()}
        {:else}
          <div data-slot="choice">{@render handles.bandChoice()}</div>
        {/if}
      </section>
    {/if}
  {/snippet}
</BandInstrumentHost>
