<script lang="ts">
  import { onDestroy, type Snippet } from 'svelte';
  import { t } from '$lib/i18n';
  import { bindChoiceInstrument } from '../primitives/control-instruments/control-instrument-behavior';
  import ControlInstrumentRendererHost from '../primitives/control-instruments/ControlInstrumentRendererHost.svelte';
  import {
    createChoiceRendererSeat, type FiniteControlAppearance, type FiniteRendererContext,
  } from '../primitives/control-instruments/control-instrument-renderer.svelte';
  import type { FilterFiniteChoiceValue, FilterInstrumentHandles } from './filter-instruments';
  import type { RadioViewModel } from './radio-view-model';

  interface ExistingProps {
    view: RadioViewModel | null;
    pendingFilter?: number | null;
    onModeChange?: (mode: string) => void;
    onFilterChange?: (filter: number) => void;
    children: Snippet<[FilterInstrumentHandles]>;
  }
  type RendererSelection = { finiteAppearance?: undefined; rendererContext?: undefined } | {
    finiteAppearance: FiniteControlAppearance<FilterFiniteChoiceValue>;
    rendererContext: FiniteRendererContext | null;
  };
  type Props = ExistingProps & RendererSelection;

  let {
    view, pendingFilter = null, onModeChange, onFilterChange,
    finiteAppearance, rendererContext, children,
  }: Props = $props();

  let modeFilter = $derived(view?.modeFilter);
  const pendingId = $props.id();
  const usable = (field: { availability: { structural: boolean; operational: boolean };
    reading: { status: string } } | undefined): boolean => field !== undefined
      && field.availability.structural && field.availability.operational
      && field.reading.status === 'known';
  const reason = (field: Parameters<typeof usable>[0]) =>
    usable(field) ? undefined : 'field-not-observed';
  const requested = <T,>(target: T | null) => target === null
    ? undefined : { kind: 'requested-target' as const, target };

  const modeBehavior = bindChoiceInstrument(() => ({
    field: modeFilter?.currentMode, choices: modeFilter?.modeChoices ?? [],
    invoke: (value) => onModeChange?.(value),
  }));
  const filterBehavior = bindChoiceInstrument(() => ({
    field: modeFilter?.currentFilter,
    choices: (modeFilter?.filterChoices ?? []).map((_choice, index) => index + 1),
    invoke: (value) => onFilterChange?.(value),
  }));

  const modeSeat = createChoiceRendererSeat<FilterFiniteChoiceValue>(() => ({
    context: rendererContext ?? null, field: modeFilter?.currentMode, label: 'Mode',
    options: (modeFilter?.modeChoices ?? []).map(value => ({ value, label: value })),
    invoke: value => onModeChange?.(value as string),
  }), { selectionRequiresAvailability: true });
  const filterSeat = createChoiceRendererSeat<FilterFiniteChoiceValue>(() => ({
    context: rendererContext ?? null, field: modeFilter?.currentFilter, label: 'Filter',
    options: (modeFilter?.filterChoices ?? []).map((label, index) => ({ value: index + 1, label })),
    requested: requested(pendingFilter), invoke: value => onFilterChange?.(value as number),
  }), { selectionRequiresAvailability: true });
  onDestroy(() => { modeSeat.destroy(); filterSeat.destroy(); });
</script>

{#snippet external(seat: typeof modeSeat)}
  {#if finiteAppearance}
    {#key rendererContext}{#key finiteAppearance.choice}<ControlInstrumentRendererHost
      {seat} renderer={finiteAppearance.choice}
    />{/key}{/key}
  {/if}
{/snippet}
{#snippet mode()}
  {#if modeFilter?.currentMode.availability.structural}
    {#if finiteAppearance}{@render external(modeSeat)}{:else}
      <div class="filter-choice-group" data-testid="filter-mode" data-disabled-reason={reason(modeFilter.currentMode)}>
        {#each modeFilter.modeChoices as choice (choice)}<button type="button" class="filter-choice"
          data-testid={`filter-mode-${choice}`} aria-pressed={modeBehavior.available && modeBehavior.isSelected(choice)}
          disabled={!modeBehavior.available} onclick={() => modeBehavior.invoke(choice)}>{choice}</button>{/each}
      </div>
    {/if}
  {/if}
{/snippet}
{#snippet filter()}
  {#if modeFilter?.currentFilter.availability.structural}
    {#if finiteAppearance}{@render external(filterSeat)}{:else}
      <div class="filter-choice-group" data-testid="filter-select" data-disabled-reason={reason(modeFilter.currentFilter)}
        data-filter-status={pendingFilter !== null ? 'pending' : 'confirmed'} aria-describedby={pendingFilter !== null ? pendingId : undefined}>
        {#each modeFilter.filterChoices as choice, index (choice)}<button type="button" class="filter-choice"
          data-testid={`filter-select-${index + 1}`} aria-pressed={filterBehavior.available && filterBehavior.isSelected(index + 1)}
          data-pending={pendingFilter === index + 1} disabled={!filterBehavior.available}
          onclick={() => filterBehavior.invoke(index + 1)}>{choice}</button>{/each}
        {#if pendingFilter !== null}<span id={pendingId} class="sr-only">{t('core.filter.select.pendingAnnouncement')}</span>{/if}
      </div>
    {/if}
  {/if}
{/snippet}

{@render children({ mode, filter })}

<style>
  .filter-choice-group { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.5rem; }
  .filter-choice[aria-pressed='true'] { font-weight: 700; }
  .filter-choice:disabled { cursor: not-allowed; }
  .filter-choice[data-pending='true'] { font-style: italic; opacity: 0.75; }
  .sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
    overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
</style>
