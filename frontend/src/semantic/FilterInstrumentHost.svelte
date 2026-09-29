<script lang="ts">
  import { onDestroy, type Snippet } from 'svelte';
  import { HardwareButton } from '$lib/Button';
  import { t } from '$lib/i18n';
  import { getShortcutHint, joinShortcutHints } from '../components-v2/layout/shortcut-hints';
  import { bindChoiceInstrument, usable } from '../primitives/control-instruments/control-instrument-behavior';
  import ControlInstrumentRendererHost from '../primitives/control-instruments/ControlInstrumentRendererHost.svelte';
  import {
    createChoiceRendererSeat, type FiniteControlAppearance, type FiniteRendererContext,
  } from '../primitives/control-instruments/control-instrument-renderer.svelte';
  import {
    projectControlFeedbackPresentation,
    type ControlFeedbackPresentationInput,
  } from '../primitives/control-feedback/control-feedback-presentation';
  import {
    FILTER_SHAPES, type FilterFiniteChoiceValue, type FilterInstrumentHandles,
  } from './filter-instruments';
  import { readingText } from '../primitives/reading-text';
  import type { RadioViewModel } from './radio-view-model';

  interface ExistingProps {
    view: RadioViewModel | null;
    pendingFilter?: number | null;
    pendingMode?: string | null;
    pendingFilterShape?: number | null;
    pendingDataMode?: number | null;
    pendingModInput?: number | null;
    /** MOR-1689: the shape choice's full command-feedback projection — the
     *  structural `ControlFeedbackPresentationInput` subset, fed by the
     *  wiring layer exactly the way CwKeyerSurface receives its feedback
     *  inputs (semantic hosts never import the adapter layer). */
    filterShapeFeedback?: Readonly<ControlFeedbackPresentationInput<number>>;
    onModeChange?: (mode: string) => void;
    onFilterChange?: (filter: number) => void;
    onFilterShapeChange?: (shape: number) => void;
    onDataModeChange?: (mode: number) => void;
    onModInputChange?: (source: number) => void;
    children: Snippet<[FilterInstrumentHandles]>;
  }
  type RendererSelection = { finiteAppearance?: undefined; rendererContext?: undefined } | {
    finiteAppearance: FiniteControlAppearance<FilterFiniteChoiceValue>;
    rendererContext: FiniteRendererContext | null;
  };
  type Props = ExistingProps & RendererSelection;

  let {
    view, pendingFilter = null, pendingMode = null, pendingFilterShape = null, pendingDataMode = null,
    pendingModInput = null,
    filterShapeFeedback, onModeChange, onFilterChange, onFilterShapeChange, onDataModeChange, onModInputChange,
    finiteAppearance, rendererContext, children,
  }: Props = $props();

  let modeFilter = $derived(view?.modeFilter);
  let filterPassband = $derived(view?.filterPassband);
  const pendingId = $props.id();
  const pendingFilterShapeId = `${pendingId}-shape`;
  const pendingDataModeId = `${pendingId}-data-mode`;
  const pendingModInputId = `${pendingId}-mod-input`;
  const reason = (field: Parameters<typeof usable>[0]) =>
    usable(field) ? undefined : 'field-not-observed';
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
  // Same hint hosts ModePanel/FilterPanel expose (MOR-2793): the Standard
  // face suppresses those panels, so the instrument buttons carry the
  // `data-shortcut-hint` the KeyboardHandler CSS keys on.
  function modeShortcut(mode: string): string | null {
    return getShortcutHint('mode_select', (binding) => binding.params?.mode === mode);
  }
  const dataShortcut = (): string | null => getShortcutHint('cycle_data_mode');
  const cycleFilterShortcut = (): string | null => joinShortcutHints(
    getShortcutHint('cycle_filter'),
    getShortcutHint('cycle_filter', (binding) => Number(binding.params?.step ?? 0) === -1),
  );
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
    requested: requested(pendingMode),
    invoke: value => onModeChange?.(value as string),
  }), { selectionRequiresAvailability: true });
  const filterSeat = createChoiceRendererSeat<FilterFiniteChoiceValue>(() => ({
    context: rendererContext ?? null, field: modeFilter?.currentFilter, label: 'Filter',
    options: (modeFilter?.filterChoices ?? []).map((label, index) => ({ value: index + 1, label })),
    requested: requested(pendingFilter), invoke: value => onFilterChange?.(value as number),
  }), { selectionRequiresAvailability: true });
  const shapeSeat = createChoiceRendererSeat<FilterFiniteChoiceValue>(() => ({
    context: rendererContext ?? null,     field: filterPassband?.filterShape, label: 'Filter shape',
    options: FILTER_SHAPES.map(([value, label]) => ({ value, label })),
    requested: requested(pendingFilterShape), invoke: value => onFilterShapeChange?.(value as number),
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

  // MOR-1689: the shape choice's structural feedback presentation — phase
  // attributes for the interactive elements, a status sentence naming the
  // requested target, and ONE polite announcement per lifecycle transition
  // (the announced ids live in a plain closure, not reactive state: the
  // dedup ledger must not itself retrigger the effect that writes it).
  // `busy` is read from the presentation's own aria-busy derivation — the
  // structural input deliberately omits a separate busy field.
  const describeShapeTarget = (target: number): string =>
    FILTER_SHAPES.find(([value]) => value === target)?.[1] ?? String(target);
  let shapeAnnouncedTransitionIds: readonly string[] = [];
  let shapeAnnouncement = $state<Readonly<{ transitionId: string; message: string }> | null>(null);
  let shapeStatusText = $state<string | null>(null);
  let shapeBusy = $state(false);
  $effect(() => {
    const feedback = filterShapeFeedback;
    if (feedback === undefined) {
      shapeAnnouncedTransitionIds = [];
      shapeAnnouncement = null;
      shapeStatusText = null;
      shapeBusy = false;
      return;
    }
    const presentation = projectControlFeedbackPresentation(
      feedback, { announcedTransitionIds: shapeAnnouncedTransitionIds }, describeShapeTarget,
    );
    shapeAnnouncedTransitionIds = presentation.state.announcedTransitionIds;
    shapeStatusText = presentation.currentStatus;
    shapeBusy = presentation.attributes['aria-busy'] === 'true';
    // A transition's announcement stands until the control goes idle — a
    // terminal outcome (failed/superseded/…) is heard exactly once, and an
    // unrelated state churn must not erase a still-relevant message.
    if (feedback.phase === 'idle' || feedback.phase === 'unavailable') {
      shapeAnnouncement = null;
    } else if (presentation.politeAnnouncement !== null) {
      shapeAnnouncement = {
        transitionId: presentation.politeAnnouncement.transitionId,
        message: presentation.politeAnnouncement.message,
      };
    }
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
          data-shortcut-hint={modeShortcut(choice) ?? undefined} title={modeShortcut(choice) ?? undefined}
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
          data-shortcut-hint={cycleFilterShortcut() ?? undefined} title={cycleFilterShortcut() ?? undefined}
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
      <!-- MOR-1689: same pending affordance the filter() snippet above uses —
           data-pending on the in-flight choice only, a group announcement
           while one is in flight, and the confirmed filterShape reading as
           the sole selection source — PLUS the shared choice seam's
           structural feedback: the lifecycle's data-command-phase and
           aria-busy on the actual buttons, the status sentence naming the
           requested target, and a transition-deduplicated polite live
           region. Italic stays a secondary channel, never the only one. -->
      {@const shapePhase = filterShapeFeedback?.phase}
      <div class="filter-choice-group" data-testid="filter-shape" data-disabled-reason={reason(filterPassband.filterShape)}
        data-command-phase={shapePhase}
        aria-busy={shapeBusy}
        aria-describedby={pendingFilterShape !== null ? pendingFilterShapeId : undefined}>
        {#each FILTER_SHAPES as [value, label] (value)}<button type="button" class="filter-choice"
          data-testid={`filter-shape-${value}`} aria-pressed={shapeBehavior.available && shapeBehavior.isSelected(value)}
          data-pending={pendingFilterShape === value} disabled={!shapeBehavior.available}
          data-command-phase={shapePhase}
          aria-busy={shapeBusy}
          onclick={() => shapeBehavior.invoke(value)}>{label}</button>{/each}
        {#if pendingFilterShape !== null}<span id={pendingFilterShapeId} class="sr-only">{shapeStatusText ?? t('core.filter.select.pendingAnnouncement')}</span>{/if}
        {#if shapeAnnouncement !== null}{#key shapeAnnouncement.transitionId}<span class="sr-only" role="status" aria-live="polite" aria-atomic="true"
          data-control-feedback-status data-filter-shape-live>{shapeAnnouncement.message}</span>{/key}{/if}
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
        <output>{readingText(filterPassband.dataMode)}</output>
        {#if filterPassband.dataModeChoices.length > 1}
          {#each filterPassband.dataModeChoices as choice (choice.value)}<button type="button" class="filter-choice"
            data-testid={`filter-data-mode-${choice.value}`} aria-pressed={dataBehavior.available && dataBehavior.isSelected(choice.value)}
            data-shortcut-hint={dataShortcut() ?? undefined} title={dataShortcut() ?? undefined}
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
        role="radiogroup" aria-label="Mode" data-disabled-reason={reason(modeFilter.currentMode)}>
        {#each standardModeChoices as choice (choice)}
          <!-- MOR-2907 F7: the mode keys expose the confirmed selection —
               the same radio vocabulary `agcKeys` (DspInstrumentHost) uses;
               per-option false is the accepted choice-group form (ADR
               addendum 2026-09-02 item 3). MOR-2907 F3: the pending target
               rides the SAME armed seat/vocabulary `standardDataMode`'s
               keys below use — display-only, never the selection source. -->
          {@const isPending = pendingMode === choice}
          {@const modeArmedId = `${pendingId}-mode-${choice}`}
          <span data-testid={`standard-mode-${choice}`} data-pending={isPending}>
            <HardwareButton
              active={modeBehavior.available && modeBehavior.isSelected(choice)}
              disabled={!modeBehavior.available} indicator="edge-left" color="cyan"
              role="radio" ariaChecked={modeBehavior.isSelected(choice)}
              armed={isPending} describedBy={isPending ? modeArmedId : undefined}
              title={modeShortcut(choice)}
              shortcutHint={modeShortcut(choice)}
              onclick={() => modeBehavior.invoke(choice)}
            >{choice}</HardwareButton>
            {#if isPending}<span id={modeArmedId} class="sr-only">{t('core.modePanel.pendingAnnouncement')}</span>{/if}
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
                title={dataShortcut()}
                shortcutHint={dataShortcut()}
                armed={isPending} describedBy={isPending ? pendingDataModeId : undefined}
                onclick={() => dataBehavior.invoke(choice.value)}
              >{choice.label ?? (choice.value === 0 ? 'OFF' : `D${choice.value}`)}</HardwareButton>
            </span>
          {/each}
        </div>
        {#if pendingDataMode !== null}<span id={pendingDataModeId} class="sr-only">{t('core.modePanel.dataMode.pendingAnnouncement')}</span>{/if}
        {#if filterPassband.modInputSource?.availability.structural}
          <label class="standard-mod-input" data-testid="standard-mod-input"
            data-disabled-reason={reason(filterPassband.modInputSource)}
            data-mod-input-status={pendingModInput !== null ? 'pending' : usable(filterPassband.modInputSource) ? 'confirmed' : filterPassband.modInputSource.reading.status === 'known' ? 'retained' : 'unknown'}>
            <span class="standard-section-label">{t('core.modePanel.modInputLabel')}</span>
            {#key `${filterPassband.modInputSource.reading.status}:${filterPassband.modInputSource.reading.status === 'known' ? filterPassband.modInputSource.reading.value : ''}:${pendingModInput ?? ''}`}
            <select data-testid="mod-input-select" aria-label={t('core.modePanel.modInputAria')}
              aria-describedby={pendingModInput !== null ? pendingModInputId : undefined}
              data-pending-value={pendingModInput === null ? undefined : pendingModInput}
              disabled={!usable(filterPassband.modInputSource)}
              onchange={(event) => onModInputChange?.(Number(event.currentTarget.value))}>
              <!-- MOR-2648: an unread select shows a blank, unlit choice —
                   an empty option, never a dash or a fabricated value; the
                   select's grid column keeps its width reserved. -->
              {#if filterPassband.modInputSource.reading.status !== 'known'}<option value="" disabled selected></option>{/if}
              {#each filterPassband.modInputChoices ?? [] as choice (choice.value)}
                <option value={choice.value}
                  selected={filterPassband.modInputSource.reading.status === 'known'
                    && filterPassband.modInputSource.reading.value === choice.value}>{choice.label}</option>
              {/each}
            </select>
            {/key}
            {#if pendingModInput !== null}<span id={pendingModInputId} class="sr-only">{t('core.modePanel.dataMode.pendingAnnouncement')}</span>{/if}
          </label>
        {/if}
      </div>
    {/if}
  {/if}
{/snippet}

{@render children({ mode, filter, shape, dataMode, standardMode, standardDataMode })}

<style>
  .filter-choice-group, .filter-readout { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.5rem; }
  .filter-level-name { min-width: 8ch; }
  /* MOR-2648: the DATA-mode value box stays reserved — a flex child, so the
     min-width applies; 4ch covers a multi-digit code, tabular digits keep a
     changing value from shifting the row. */
  .filter-readout output, .filter-choice-group output { min-width: 4ch; font-variant-numeric: tabular-nums; }
  .filter-choice[aria-pressed='true'] { font-weight: 700; }
  .filter-choice:disabled { cursor: not-allowed; }
  /* MOR-1689: underline is the structural pending channel that survives
     forced-colors; italic/opacity stay secondary, never the only signal. */
  .filter-choice[data-pending='true'] { font-style: italic; opacity: 0.75; text-decoration: underline; }
  .standard-data-block { display: flex; flex-direction: column; gap: 0.5rem; }
  .standard-mod-input { display: grid; grid-template-columns: 1fr minmax(0, 1fr); align-items: center; gap: 0.75rem; }
  .standard-mod-input select { min-width: 0; }
  .standard-choice-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 4px; }
  .standard-choice-grid > span { min-width: 0; }
  .standard-choice-grid > span > :global(button) { width: 100%; min-width: 0; min-height: 36px; }
  .standard-section-label { color: var(--v2-text-dim); font-family: 'Roboto Mono', monospace;
    font-size: 12px; font-weight: 700; letter-spacing: 0.08em; }
  /* MOR-1689: the shape choice's structural feedback rules — same doctrine
     as FilterSurface's width row (structure only, a design language owns
     colour). */
  @media (forced-colors: active) {
    .filter-choice-group[data-testid='filter-shape'] [data-control-feedback-status] {
      forced-color-adjust: none;
      color: CanvasText;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .filter-choice-group[data-testid='filter-shape'],
    .filter-choice-group[data-testid='filter-shape'] * { transition: none; animation: none; }
  }
  .sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
    overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
</style>
