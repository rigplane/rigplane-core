<script lang="ts">
  import type { FiniteControlAppearance, FiniteRendererContext } from '../../../primitives/control-instruments/control-instrument-renderer.svelte';
  import type { CommandScalarFeedback } from '../../../primitives/scalar/continuous-scalar.svelte';
  import FilterInstrumentHost from '../../FilterInstrumentHost.svelte';
  import FilterSurface from '../../FilterSurface.svelte';
  import type { FilterFiniteChoiceValue, FilterInstrumentHandles } from '../../filter-instruments';
  import type { RadioViewModel } from '../../radio-view-model';

  interface Props {
    view: RadioViewModel | null;
    presentation?: 'grouped' | 'independent' | 'standard';
    renderSurface?: boolean;
    pendingFilter?: number | null;
    pendingDataMode?: number | null;
    pendingModInput?: number | null;
    filterWidthFeedback?: Readonly<CommandScalarFeedback>;
    finiteAppearance?: FiniteControlAppearance<FilterFiniteChoiceValue>;
    rendererContext?: FiniteRendererContext | null;
    onModeChange?: (mode: string) => void;
    onFilterChange?: (filter: number) => void;
    onDataModeChange?: (mode: number) => void;
    onModInputChange?: (source: number) => void;
    onFilterWidthChange?: (width: number) => void;
    onFilterShapeChange?: (shape: number) => void;
    onIfShiftChange?: (value: number) => void;
    onPbtInnerChange?: (value: number) => void;
    onPbtOuterChange?: (value: number) => void;
  }
  let {
    view, presentation = 'grouped', renderSurface = false,
    pendingFilter = null, pendingDataMode = null, pendingModInput = null, filterWidthFeedback,
    finiteAppearance, rendererContext = null, onModeChange, onFilterChange,
    onDataModeChange, onModInputChange, onFilterWidthChange, onFilterShapeChange,
    onIfShiftChange, onPbtInnerChange, onPbtOuterChange,
  }: Props = $props();
  let selection = $derived(finiteAppearance === undefined ? {} : { finiteAppearance, rendererContext });
</script>

{#snippet independent(handles: FilterInstrumentHandles)}
  <div data-slot="mode">{@render handles.mode()}</div>
  <div data-slot="filter">{@render handles.filter()}</div>
  <div data-slot="shape">{@render handles.shape()}</div>
  <div data-slot="data-mode">{@render handles.dataMode()}</div>
{/snippet}

<FilterInstrumentHost {view} {pendingFilter} {pendingDataMode} {pendingModInput} {onModeChange} {onFilterChange}
  {onFilterShapeChange} {onDataModeChange} {onModInputChange} {...selection}>
  {#snippet children(handles: FilterInstrumentHandles)}
    {#if renderSurface && view !== null}
      <FilterSurface
        {view} {handles} finiteLayout={presentation === 'independent' ? independent : undefined}
        {filterWidthFeedback} {onFilterWidthChange}
        {onIfShiftChange} {onPbtInnerChange} {onPbtOuterChange}
      />
    {:else}
      {#key presentation}
        <section data-testid={`${presentation}-filter-composition`}>
          {#if presentation === 'standard'}
            {#if handles.standardDataMode}{@render handles.standardDataMode()}{/if}
          {:else if presentation === 'grouped'}
            {@render handles.mode()}{@render handles.filter()}{@render handles.shape()}{@render handles.dataMode()}
          {:else}
            {@render independent(handles)}
          {/if}
        </section>
      {/key}
    {/if}
  {/snippet}
</FilterInstrumentHost>
