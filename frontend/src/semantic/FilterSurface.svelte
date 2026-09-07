<!--
  Semantic mode/filter surface (MOR-1304, vocabulary slice 4B).

  Presentation only. Places the host-owned mode and filter handles beside
  the MOR-1280 `modeFilter` width, then renders the MOR-1284
  `filterPassband` group (filter shape, IF-shift, PBT inner/outer, DATA
  submode) — the same two groups the v2 `FilterPanel` reads together
  (`panel-props.ts`'s `deriveFilterProps`).

  Doctrine, same as `TxAuxSurface`/`MetersSurface`:
  (1) Facts only — every value and every min/max bound is READ from the
      adapter-produced view model, never re-derived. `resolveFilterModeConfig`
      and `pbtRawToHz` stay behind the adapter (MOR-1284/1280 rulings); this
      file imports neither.
  (2) Two-level availability per field (MOR-977): `structural: false` renders
      nothing; a present-but-unobserved field renders disabled, with reason
      `field-not-observed`, never a guessed value or a fabricated selection
      (a control never claims a choice is active unless its OWN reading says
      so — an unknown `filterShape` on a radio that HAS filters shows neither
      button pressed, matching v2's fail-open default nowhere).
  (3) `filterWidthMin`/`filterWidthMax` are read through their OWN field —
      each one carries its OWN operational flag (the adapter gates them on
      `modeObserved`, `filterWidth` on its own `widthObserved`); this file
      never substitutes one field's gate for another's.

  CAPABILITY-ABSENT CONTROLS ARE HIDDEN, NOT SHOWN DEAD (MOR-1494 review
  round). The `ifShift` ROW is an exception to rule (2) above: it gates
  on `filterPassband.ifShiftControlStructural`, NOT on
  `filterPassband.ifShift.availability.structural`. The latter stays `true`
  for any radio with EITHER `if_shift` OR `pbt` (a PBT-only radio like
  IC-7300 still gets an honest derived `ifShift` reading, e.g. for
  `scope-adapter.ts`'s passband-center overlay) — showing that as a control
  the operator can never actually turn is exactly the "shown dead" defect
  MOR-1494 fixed. `ifShiftControlStructural` answers the narrower question
  this row needs: does the radio have a REAL `if_shift` command. See
  `radio-view-model.ts`'s `FilterPassbandViewModel` doc comment.

  MOR-1502 applies the SAME split to the `filter-shape` ROW: it gates on
  `filterPassband.filterShapeControlStructural`, NOT on
  `filterPassband.filterShape.availability.structural`. The latter stays
  `true` for any radio with a declared filter catalog at all (the FTX-1 has
  filters but no `filter_shape` command — showing SHARP/SOFT permanently
  disabled is the same "shown dead" defect). `filterShapeControlStructural`
  answers whether the radio has a REAL `filter_shape` command; see
  `FilterPassbandViewModel.filterShapeControlStructural`'s doc comment.

  PENDING AFFORDANCE (MOR-1441 leg 2). The host-owned Filter handle carries
  the pending target separately from confirmed truth. DATA remains local and
  keeps the same separation below.
-->
<script module lang="ts">
  import type { DisplayObservedField, TxAuxField } from './radio-view-model';

  /** Fixed filter-shape choice set (SHARP/SOFT) — same two options
   *  `FilterPanel`'s shape buttons offer, `[value, label]`. */
  export const FILTER_SHAPES = [[0, 'SHARP'], [1, 'SOFT']] as const;
  /** `[field, label, min, max, step]` in RAW Hz, same ranges `FilterPanel`'s
   *  IF-shift/PBT sliders have always used. */
  export const FILTER_PASSBAND_LEVELS = [
    ['ifShift', 'IF shift', -1200, 1200, 25],
    ['pbtInner', 'PBT inner', -1200, 1200, 25],
    ['pbtOuter', 'PBT outer', -1200, 1200, 25],
  ] as const;
  export type FilterPassbandLevelField = (typeof FILTER_PASSBAND_LEVELS)[number][0];

  /** Usable ⇔ the radio HAS it, it is readable NOW, and it has been observed.
   *  `ModeFilterField`/`FilterPassbandField` are both declared as aliases of
   *  `TxAuxField` (same field shape per fact family), so one set of helpers
   *  serves every field in both groups — no per-group re-derivation. */
  const usable = (f: TxAuxField<unknown>): boolean =>
    f.availability.structural && f.availability.operational && f.reading.status === 'known';
  const reasonOf = (f: TxAuxField<unknown>): 'field-not-observed' | undefined =>
    usable(f) ? undefined : 'field-not-observed';
  const textOf = (f: TxAuxField<unknown>): string =>
    f.reading.status === 'known' ? String(f.reading.value) : '?';
  const presentationOf = (f: TxAuxField<unknown>): 'confirmed' | 'retained' | 'unknown' =>
    usable(f) ? 'confirmed' : f.reading.status === 'known' ? 'retained' : 'unknown';
  const pbtDisplay = (f: DisplayObservedField<number>) => f.display ?? (
    f.reading.status === 'known'
      ? { state: usable(f) ? 'current' as const : 'stale' as const, value: f.reading.value }
      : { state: 'unknown' as const, reason: 'not-observed' as const }
  );
  const pbtUsable = (f: DisplayObservedField<number>): boolean => usable(f) && pbtDisplay(f).state === 'current';
  const numberOf = (f: DisplayObservedField<number>, fallback: number): number =>
    f.display?.state === 'current' || f.display?.state === 'stale' ? f.display.value
      : f.reading.status === 'known' ? f.reading.value : fallback;
</script>

<script lang="ts">
  import { onDestroy, untrack } from 'svelte';
  import { t } from '$lib/i18n';
  import { bindChoiceInstrument } from '../primitives/control-instruments/control-instrument-behavior';
  import {
    createContinuousScalar, nativeRangeContinuousScalarPolicy,
    type CommandScalarFeedback, type ContinuousScalarInput,
    type ContinuousScalarRendererLease, type ContinuousScalarView,
  } from '../primitives/scalar/continuous-scalar.svelte';
  import type { FilterFiniteLayout, FilterInstrumentHandles } from './filter-instruments';
  import type { RadioViewModel } from './radio-view-model';

  interface Props {
    view: RadioViewModel;
    handles: FilterInstrumentHandles;
    finiteLayout?: FilterFiniteLayout;
    pendingDataMode?: number | null;
    filterWidthFeedback?: Readonly<CommandScalarFeedback>;
    onDataModeChange?: (mode: number) => void;
    onFilterWidthChange?: (width: number) => void;
    onFilterShapeChange?: (shape: number) => void;
    onIfShiftChange?: (value: number) => void;
    onPbtInnerChange?: (value: number) => void;
    onPbtOuterChange?: (value: number) => void;
  }
  let {
    view, handles, finiteLayout, pendingDataMode = null, filterWidthFeedback,
    onDataModeChange, onFilterWidthChange,
    onFilterShapeChange, onIfShiftChange, onPbtInnerChange, onPbtOuterChange,
  }: Props = $props();

  const pendingFilterId = $props.id();
  const pendingDataModeId = `${pendingFilterId}-data-mode`;
  const feedbackIntegratedRange = { 'feedback-policy': 'feedback-integrated' } as const;

  let modeFilter = $derived(view.modeFilter);
  let filterPassband = $derived(view.filterPassband);

  function filterWidthInput(): Readonly<ContinuousScalarInput> {
    const field = modeFilter?.filterWidth;
    const domain = {
      min: modeFilter ? numberOf(modeFilter.filterWidthMin, 50) : 50,
      max: modeFilter ? numberOf(modeFilter.filterWidthMax, 9999) : 9999,
      step: 50, defaultValue: null, fineStepDivisor: 1,
    };
    const enabled = field !== undefined && usable(field);
    const request = (value: number) => onFilterWidthChange?.(value);
    if (filterWidthFeedback !== undefined) {
      return {
        evidence: 'command-feedback', feedback: filterWidthFeedback,
        command: 'set_filter_width', domain, enabled, request,
      };
    }
    return {
      evidence: 'reading',
      reading: field?.reading.status === 'known'
        ? { status: 'known', value: field.reading.value } : { status: 'unknown' },
      ownerKey: 'filter-width-legacy', domain, enabled, request,
    };
  }
  const filterWidthScalar = createContinuousScalar(
    filterWidthInput, nativeRangeContinuousScalarPolicy,
  );
  let filterWidthLease: ContinuousScalarRendererLease | null = $state(null);
  const initialFilterWidthView = untrack(() => filterWidthScalar.view);
  let filterWidthView: Readonly<ContinuousScalarView> = $state(initialFilterWidthView);
  type IssuedFilterWidthAnnouncement = Readonly<{
    authorityKey: string;
    eventKey: string;
    text: string;
  }>;
  function filterWidthAuthorityKey(current: Readonly<ContinuousScalarView>): string {
    const domain = current.domain;
    const shared = [
      current.evidence, current.editable, domain.min, domain.max, domain.step,
      domain.defaultValue, domain.fineStepDivisor, domain.keyboardStep,
    ];
    if (current.evidence === 'reading') {
      return JSON.stringify([...shared, current.reading.status]);
    }
    const feedback = current.feedback;
    return JSON.stringify([
      ...shared, feedback.providerGeneration ?? null, feedback.sessionEpoch,
      feedback.availability, feedback.scope.control, feedback.scope.receiver, feedback.scope.slot,
    ]);
  }
  function nextFilterWidthAnnouncement(
    current: Readonly<ContinuousScalarView>,
    previous: IssuedFilterWidthAnnouncement | null,
  ): IssuedFilterWidthAnnouncement | null {
    const authorityKey = filterWidthAuthorityKey(current);
    const issued = current.presentation?.politeAnnouncement;
    if (issued === null || issued === undefined || current.announcement === null) {
      return previous?.authorityKey === authorityKey ? previous : null;
    }
    return Object.freeze({
      authorityKey,
      eventKey: JSON.stringify([authorityKey, issued.transitionId]),
      text: current.error === null ? current.announcement : `${current.announcement}: ${current.error}`,
    });
  }
  let filterWidthAnnouncement: IssuedFilterWidthAnnouncement | null = $state(
    nextFilterWidthAnnouncement(initialFilterWidthView, null),
  );
  $effect(() => {
    const lease = filterWidthScalar.attachRenderer();
    filterWidthLease = lease;
    return () => lease.dispose();
  });
  $effect(() => {
    const next = filterWidthLease === null
      ? filterWidthScalar.view : filterWidthLease.view;
    filterWidthView = next;
    filterWidthAnnouncement = nextFilterWidthAnnouncement(
      next, untrack(() => filterWidthAnnouncement),
    );
  });
  onDestroy(() => filterWidthScalar.destroy());
  function shapeInstrument() {
    return bindChoiceInstrument(() => ({
      field: filterPassband?.filterShape, choices: FILTER_SHAPES.map(([value]) => value),
      invoke: (value) => onFilterShapeChange?.(value),
    }));
  }
  function dataModeInstrument() {
    return bindChoiceInstrument(() => ({
      field: filterPassband?.dataMode,
      choices: filterPassband?.dataModeChoices.map(choice => choice.value) ?? [],
      blocked: (filterPassband?.dataModeChoices.length ?? 0) < 2,
      invoke: (value) => onDataModeChange?.(value),
    }));
  }
  /** One guarded entry point for all three passband sliders — each still
   *  reads and disables on its OWN field's availability (see the file
   *  header, rule 3), this only routes the already-checked value onward. */
  function changePassband(field: FilterPassbandLevelField, value: number): void {
    if (!filterPassband || !usable(filterPassband[field])) return;
    if (field !== 'ifShift' && !pbtUsable(filterPassband[field])) return;
    if (field === 'ifShift') onIfShiftChange?.(value);
    else if (field === 'pbtInner') onPbtInnerChange?.(value);
    else onPbtOuterChange?.(value);
  }
</script>

{#if modeFilter || filterPassband}
  <section class="filter-surface" data-testid="filter-surface" aria-label="Mode and filter controls">
    {#if modeFilter}
      {#if finiteLayout}{@render finiteLayout(handles)}{:else}
        {@render handles.mode()}
        {@render handles.filter()}
      {/if}
      {#if modeFilter.filterWidth.availability.structural}
        <label class="filter-level" data-testid="filter-width" data-disabled-reason={reasonOf(modeFilter.filterWidth)}>
          <span class="filter-level-name">Width</span>
          <input
            type="range"
            {...feedbackIntegratedRange}
            min={numberOf(modeFilter.filterWidthMin, 50)} max={numberOf(modeFilter.filterWidthMax, 9999)} step={50}
            value={filterWidthView.displayed ?? numberOf(modeFilter.filterWidth, 0)}
            disabled={!filterWidthView.editable}
            data-command-phase={filterWidthView.phase ?? undefined}
            aria-busy={filterWidthView.busy}
            oninput={(event) => filterWidthLease?.nativeInput(event.currentTarget.valueAsNumber)}
          />
          <output>{textOf(modeFilter.filterWidth)}</output>
          {#if filterWidthAnnouncement !== null}
            {#key filterWidthAnnouncement.eventKey}
              <span class="sr-only" role="status" aria-live="polite" aria-atomic="true"
                data-control-feedback-status>{filterWidthAnnouncement.text}</span>
            {/key}
          {/if}
        </label>
      {/if}
    {/if}

    {#if filterPassband}
      {#snippet passbandRange(field: FilterPassbandLevelField, min: number, max: number, step: number)}
        {@const display = field === 'ifShift' ? undefined : pbtDisplay(filterPassband[field])}
        {#key display?.state}
          <input
            id={`${pendingFilterId}-${field}-input`} type="range" {min} {max} {step}
            value={numberOf(filterPassband[field], min)}
            aria-describedby={display?.state === 'stale' ? `${pendingFilterId}-${field}-description` : undefined}
            disabled={field === 'ifShift' ? !usable(filterPassband[field]) : !pbtUsable(filterPassband[field])}
            oninput={(event) => changePassband(field, event.currentTarget.valueAsNumber)}
          />
        {/key}
      {/snippet}
      {#if filterPassband.filterShapeControlStructural}
        {@const behavior = shapeInstrument()}
        <div
          class="filter-choice-group" data-testid="filter-shape"
          data-disabled-reason={reasonOf(filterPassband.filterShape)}
        >
          {#each FILTER_SHAPES as [value, label] (value)}
            <button
              type="button" class="filter-choice" data-testid={`filter-shape-${value}`}
              aria-pressed={behavior.available && behavior.isSelected(value)}
              disabled={!behavior.available}
              onclick={() => behavior.invoke(value)}
            >{label}</button>
          {/each}
        </div>
      {/if}
      {#each FILTER_PASSBAND_LEVELS as [field, label, min, max, step] (field)}
        {#if field === 'ifShift' ? filterPassband.ifShiftControlStructural : filterPassband[field].availability.structural}
          {#if field === 'pbtInner' || field === 'pbtOuter'}
            {@const display = pbtDisplay(filterPassband[field])}
            {@const measured = display.state === 'current' || display.state === 'stale'}
            {@const descriptionId = `${pendingFilterId}-${field}-description`}
            <div
              class="filter-level" data-testid={`filter-${field}`} role="group" aria-label={label}
              data-disabled-reason={reasonOf(filterPassband[field])}
              data-presentation={display.state === 'current' ? 'confirmed' : display.state === 'stale' ? 'retained' : 'unknown'}
            >
              <label class="filter-level-name" for={`${pendingFilterId}-${field}-input`}>{label}</label>
              <span class="pbt-slot" data-pbt-slot>
                {#if display.state === 'current' || display.state === 'stale'}
                  {@render passbandRange(field, min, max, step)}
                {:else}
                  <span class="pbt-unknown">{t('core.vfo.state.unknown')}</span>
                {/if}
              </span>
              <output class="pbt-value" aria-live="off">{measured && 'value' in display ? display.value : '—'}</output>
              <span class="pbt-cue" data-stale-cue title={display.state === 'stale' ? t('core.rxTx.target.reason.stale') : undefined}>
                {#if display.state === 'stale'}<span aria-hidden="true">†</span>{/if}
              </span>
              {#if display.state === 'stale'}
                <span class="sr-only" id={descriptionId}>{t('core.rxTx.target.reason.stale')}</span>
              {/if}
            </div>
          {:else}
            <label
              class="filter-level" data-testid={`filter-${field}`}
              data-disabled-reason={reasonOf(filterPassband[field])}
              data-presentation={presentationOf(filterPassband[field])}
            >
              <span class="filter-level-name">{label}</span>
              {@render passbandRange(field, min, max, step)}
              <output>{textOf(filterPassband[field])}</output>
            </label>
          {/if}
        {/if}
      {/each}
      {#if filterPassband.dataMode.availability.structural}
        {@const behavior = dataModeInstrument()}
        <div
          class={filterPassband.dataModeChoices.length > 1 ? 'filter-choice-group' : 'filter-readout'} data-testid="filter-data-mode"
          role="group" aria-label={t('core.mobile.sheet.dataMode')}
          data-disabled-reason={reasonOf(filterPassband.dataMode)}
          data-data-mode-status={pendingDataMode !== null ? 'pending' : presentationOf(filterPassband.dataMode)}
          aria-describedby={pendingDataMode !== null ? pendingDataModeId : undefined}
        >
          <span class="filter-level-name">{filterPassband.dataModeChoices.length > 1 ? t('core.mobile.sheet.dataMode') : 'DATA'}</span>
          <output>{textOf(filterPassband.dataMode)}</output>
          {#if filterPassband.dataModeChoices.length > 1}
            {#each filterPassband.dataModeChoices as choice (choice.value)}
              <button
                type="button" class="filter-choice" data-testid={`filter-data-mode-${choice.value}`}
                aria-pressed={behavior.available && behavior.isSelected(choice.value)}
                data-pending={pendingDataMode === choice.value}
                disabled={!behavior.available}
                onclick={() => behavior.invoke(choice.value)}
              >{choice.label ?? (choice.value === 0 ? 'OFF' : `D${choice.value}`)}</button>
            {/each}
          {/if}
          {#if pendingDataMode !== null}
            <span id={pendingDataModeId} class="sr-only">{t('core.modePanel.dataMode.pendingAnnouncement')}</span>
          {/if}
        </div>
      {/if}
    {/if}
  </section>
{/if}

<style>
  /* Structure only — a design language owns colour (MOR-977, forced-colors). */
  .filter-surface { display: flex; flex-direction: column; gap: 0.25rem; }
  .filter-choice-group { display: flex; flex-wrap: wrap; gap: 0.5rem; }
  .filter-level, .filter-readout { display: flex; align-items: baseline; gap: 0.5rem; }
  .filter-level-name { min-width: 8ch; }
  .pbt-slot { display: inline-flex; align-items: center; width: 8rem; height: 1.5rem; }
  .pbt-slot input { width: 100%; margin-inline: 0; }
  .pbt-unknown { width: 100%; text-align: center; }
  .pbt-value { min-width: 6ch; font-variant-numeric: tabular-nums; }
  .pbt-cue { width: 1ch; }
  .filter-choice[aria-pressed='true'] { font-weight: 700; }
  .filter-choice:disabled { cursor: not-allowed; }
  /* MOR-1441 leg 2 — a pending (unconfirmed) target never renders identically
     to confirmed truth. Structural (italic + reduced opacity), never a
     color-only tell — same doctrine `.freq[data-freq-status='pending']`
     (leg 1) established. */
  .filter-choice[data-pending='true'] { font-style: italic; opacity: 0.75; }
  .sr-only {
    position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
    overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0;
  }
</style>
