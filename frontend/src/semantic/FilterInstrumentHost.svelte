<script lang="ts">
  import { onDestroy, type Snippet } from 'svelte';
  import { HardwareButton } from '$lib/Button';
  import { t } from '$lib/i18n';
  import { bindChoiceInstrument } from '../primitives/control-instruments/control-instrument-behavior';
  import ControlInstrumentRendererHost from '../primitives/control-instruments/ControlInstrumentRendererHost.svelte';
  import {
    createChoiceRendererSeat, type FiniteControlAppearance, type FiniteRendererContext,
  } from '../primitives/control-instruments/control-instrument-renderer.svelte';
  import {
    FILTER_SHAPES, type FilterFiniteChoiceValue, type FilterInstrumentHandles,
  } from './filter-instruments';
  import type { RadioViewModel } from './radio-view-model';

  interface ExistingProps {
    view: RadioViewModel | null;
    pendingFilter?: number | null;
    pendingDataMode?: number | null;
    onModeChange?: (mode: string) => void;
    onFilterChange?: (filter: number) => void;
    onFilterShapeChange?: (shape: number) => void;
    onDataModeChange?: (mode: number) => void;
    children: Snippet<[FilterInstrumentHandles]>;
  }
  type RendererSelection = { finiteAppearance?: undefined; rendererContext?: undefined } | {
    finiteAppearance: FiniteControlAppearance<FilterFiniteChoiceValue>;
    rendererContext: FiniteRendererContext | null;
  };
  type Props = ExistingProps & RendererSelection;

  let {
    view, pendingFilter = null, pendingDataMode = null,
    onModeChange, onFilterChange, onFilterShapeChange, onDataModeChange,
    finiteAppearance, rendererContext, children,
  }: Props = $props();

  let modeFilter = $derived(view?.modeFilter);
  let filterPassband = $derived(view?.filterPassband);
  const pendingId = $props.id();
  const pendingDataModeId = `${pendingId}-data-mode`;
  const usable = (field: { availability: { structural: boolean; operational: boolean };
    reading: { status: string } } | undefined): boolean => field !== undefined
      && field.availability.structural && field.availability.operational
      && field.reading.status === 'known';
  const reason = (field: Parameters<typeof usable>[0]) =>
    usable(field) ? undefined : 'field-not-observed';
  const textOf = (field: { reading: { status: 'known'; value: unknown } | { status: 'unknown' } }) =>
    field.reading.status === 'known' ? String(field.reading.value) : '?';
  const requested = <T,>(target: T | null) => target === null
    ? undefined : { kind: 'requested-target' as const, target };

  const STANDARD_MODE_ORDER = [
    'USB', 'LSB',
    'CW', 'CW-R', 'CW-U', 'CW-L',
    'RTTY', 'RTTY-R', 'RTTY-L', 'RTTY-U',
    'PSK', 'PSK-R',
    'DATA-U', 'DATA-L', 'DATA-FM', 'DATA-FM-N',
    'AM', 'AM-N', 'FM', 'FM-N',
    'C4FM-DN', 'C4FM-VW',
  ] as const;
  let standardModeChoices = $derived.by(() => {
    const choices = modeFilter?.modeChoices ?? [];
    return [
      ...STANDARD_MODE_ORDER.filter(mode => choices.includes(mode)),
      ...choices.filter(mode => !(STANDARD_MODE_ORDER as readonly string[]).includes(mode)),
    ];
  });

  const modeBehavior = bindChoiceInstrument(() => ({
    field: modeFilter?.currentMode, choices: modeFilter?.modeChoices ?? [],
    invoke: (value) => onModeChange?.(value),
  }));
  const filterBehavior = bindChoiceInstrument(() => ({
    field: modeFilter?.currentFilter,
    choices: (modeFilter?.filterChoices ?? []).map((_choice, index) => index + 1),
    invoke: (value) => onFilterChange?.(value),
  }));
  const shapeBehavior = bindChoiceInstrument(() => ({
    field: filterPassband?.filterShape, choices: FILTER_SHAPES.map(([value]) => value),
    invoke: (value) => onFilterShapeChange?.(value),
  }));
  const dataBehavior = bindChoiceInstrument(() => ({
    field: filterPassband?.dataMode,
    choices: filterPassband?.dataModeChoices.map(choice => choice.value) ?? [],
    blocked: (filterPassband?.dataModeChoices.length ?? 0) < 2,
    invoke: (value) => onDataModeChange?.(value),
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
  const shapeSeat = createChoiceRendererSeat<FilterFiniteChoiceValue>(() => ({
    context: rendererContext ?? null, field: filterPassband?.filterShape, label: 'Filter shape',
    options: FILTER_SHAPES.map(([value, label]) => ({ value, label })),
    invoke: value => onFilterShapeChange?.(value as number),
  }), { selectionRequiresAvailability: true });
  const dataSeat = createChoiceRendererSeat<FilterFiniteChoiceValue>(() => ({
    context: rendererContext ?? null, field: filterPassband?.dataMode, label: 'DATA mode',
    options: (filterPassband?.dataModeChoices ?? []).map(choice => ({
      value: choice.value, label: choice.label ?? (choice.value === 0 ? 'OFF' : `D${choice.value}`),
    })),
    blocked: (filterPassband?.dataModeChoices.length ?? 0) < 2,
    requested: requested(pendingDataMode), invoke: value => onDataModeChange?.(value as number),
  }), { selectionRequiresAvailability: true });
  onDestroy(() => {
    modeSeat.destroy(); filterSeat.destroy(); shapeSeat.destroy(); dataSeat.destroy();
  });
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
{#snippet shape()}
  {#if filterPassband?.filterShapeControlStructural}
    {#if finiteAppearance}{@render external(shapeSeat)}{:else}
      <div class="filter-choice-group" data-testid="filter-shape" data-disabled-reason={reason(filterPassband.filterShape)}>
        {#each FILTER_SHAPES as [value, label] (value)}<button type="button" class="filter-choice"
          data-testid={`filter-shape-${value}`} aria-pressed={shapeBehavior.available && shapeBehavior.isSelected(value)}
          disabled={!shapeBehavior.available} onclick={() => shapeBehavior.invoke(value)}>{label}</button>{/each}
      </div>
    {/if}
  {/if}
{/snippet}
{#snippet dataMode()}
  {#if filterPassband?.dataMode.availability.structural}
    {#if finiteAppearance}{@render external(dataSeat)}{:else}
      <div class={filterPassband.dataModeChoices.length > 1 ? 'filter-choice-group' : 'filter-readout'} data-testid="filter-data-mode"
        role="group" aria-label={t('core.mobile.sheet.dataMode')} data-disabled-reason={reason(filterPassband.dataMode)}
        data-data-mode-status={pendingDataMode !== null ? 'pending' : usable(filterPassband.dataMode) ? 'confirmed' : filterPassband.dataMode.reading.status === 'known' ? 'retained' : 'unknown'}
        aria-describedby={pendingDataMode !== null ? pendingDataModeId : undefined}>
        <span class="filter-level-name">{filterPassband.dataModeChoices.length > 1 ? t('core.mobile.sheet.dataMode') : 'DATA'}</span>
        <output>{textOf(filterPassband.dataMode)}</output>
        {#if filterPassband.dataModeChoices.length > 1}
          {#each filterPassband.dataModeChoices as choice (choice.value)}<button type="button" class="filter-choice"
            data-testid={`filter-data-mode-${choice.value}`} aria-pressed={dataBehavior.available && dataBehavior.isSelected(choice.value)}
            data-pending={pendingDataMode === choice.value} disabled={!dataBehavior.available}
            onclick={() => dataBehavior.invoke(choice.value)}>{choice.label ?? (choice.value === 0 ? 'OFF' : `D${choice.value}`)}</button>{/each}
        {/if}
        {#if pendingDataMode !== null}<span id={pendingDataModeId} class="sr-only">{t('core.modePanel.dataMode.pendingAnnouncement')}</span>{/if}
      </div>
    {/if}
  {/if}
{/snippet}

{#snippet standardMode()}
  {#if modeFilter?.currentMode.availability.structural}
    {#if finiteAppearance}
      {@render external(modeSeat)}
    {:else}
      <div class="standard-choice-grid" data-testid="standard-mode-choices"
        role="group" aria-label="Mode" data-disabled-reason={reason(modeFilter.currentMode)}>
        {#each standardModeChoices as choice (choice)}
          <span data-testid={`standard-mode-${choice}`}>
            <HardwareButton
              active={modeBehavior.available && modeBehavior.isSelected(choice)}
              disabled={!modeBehavior.available} indicator="edge-left" color="cyan"
              onclick={() => modeBehavior.invoke(choice)}
            >{choice}</HardwareButton>
          </span>
        {/each}
      </div>
    {/if}
  {/if}
{/snippet}

{#snippet standardDataMode()}
  {#if filterPassband?.dataMode.availability.structural && filterPassband.dataModeChoices.length > 1}
    {#if finiteAppearance}
      {@render external(dataSeat)}
    {:else}
      <div class="standard-data-block" data-testid="standard-data-mode"
        data-disabled-reason={reason(filterPassband.dataMode)}
        data-data-mode-status={pendingDataMode !== null ? 'pending' : usable(filterPassband.dataMode) ? 'confirmed' : filterPassband.dataMode.reading.status === 'known' ? 'retained' : 'unknown'}>
        <div class="standard-section-label">DATA</div>
        <div class="standard-choice-grid" role="group" aria-label={t('core.mobile.sheet.dataMode')}>
          {#each filterPassband.dataModeChoices as choice (choice.value)}
            {@const isPending = pendingDataMode === choice.value}
            <span data-testid={`standard-data-mode-${choice.value}`} data-pending={isPending}>
              <HardwareButton
                active={dataBehavior.available && dataBehavior.isSelected(choice.value)}
                disabled={!dataBehavior.available} indicator="edge-left" color="cyan"
                armed={isPending} describedBy={isPending ? pendingDataModeId : undefined}
                onclick={() => dataBehavior.invoke(choice.value)}
              >{choice.label ?? (choice.value === 0 ? 'OFF' : `D${choice.value}`)}</HardwareButton>
            </span>
          {/each}
        </div>
        {#if pendingDataMode !== null}<span id={pendingDataModeId} class="sr-only">{t('core.modePanel.dataMode.pendingAnnouncement')}</span>{/if}
      </div>
    {/if}
  {/if}
{/snippet}

{@render children({ mode, filter, shape, dataMode, standardMode, standardDataMode })}

<style>
  .filter-choice-group, .filter-readout { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.5rem; }
  .filter-level-name { min-width: 8ch; }
  .filter-choice[aria-pressed='true'] { font-weight: 700; }
  .filter-choice:disabled { cursor: not-allowed; }
  .filter-choice[data-pending='true'] { font-style: italic; opacity: 0.75; }
  .standard-data-block { display: flex; flex-direction: column; gap: 0.5rem; }
  .standard-choice-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 4px; }
  .standard-choice-grid > span { min-width: 0; }
  .standard-choice-grid > span > :global(button) { width: 100%; min-width: 0; min-height: 36px; }
  .standard-section-label { color: var(--v2-text-dim); font-family: 'Roboto Mono', monospace;
    font-size: 12px; font-weight: 700; letter-spacing: 0.08em; }
  .sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
    overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
</style>
