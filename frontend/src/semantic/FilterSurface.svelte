<!--
  Semantic mode/filter surface (MOR-1304, vocabulary slice 4B).

  Presentation only. Places the host-owned mode and filter handles beside
  the MOR-1280 `modeFilter` width, then renders the MOR-1284
  `filterPassband` group — the same two groups the v2 `FilterPanel` reads
  together (`panel-props.ts`'s `deriveFilterProps`).

  Doctrine, same as `TxAuxSurface`/`MetersSurface`:
  (1) Facts only — every value and every min/max bound is READ from the
      adapter-produced view model, never re-derived. `resolveFilterModeConfig`
      and `measuredPbtRawToHz` stay behind the adapter (MOR-1284/1280
      rulings); this file imports neither.
  (2) Two-level availability per field (MOR-977): `structural: false` renders
      nothing; a present-but-unobserved field renders disabled, with reason
      `field-not-observed`, never a guessed value or a fabricated selection
      (a control never claims a choice is active unless its OWN reading says
      so).
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

  PENDING AFFORDANCE (MOR-1441 leg 2). The host-owned Filter handle carries
  the pending target separately from confirmed truth.
-->
<script module lang="ts">
  import type { DisplayObservedField, TxAuxField } from './radio-view-model';

  /** `[field, label, min, max, step]` in RAW Hz — the fallback bounds for a
   *  passband row whose group carries no domain: `ifShift` when the profile
   *  publishes no `controls.if_shift` entry, the PBT rows when no measured
   *  lattice forms (no mode step, unobserved width). Same fallback shape the
   *  v2 `FilterPanel` keeps after W2a (MOR-2497). */
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
  /** MOR-2425/R40: a held reading is a reading — a PBT slider whose last
   *  observed value has aged past its TTL stays enabled and dispatching.
   *  Freshness alone no longer refuses; `usable` and a display carrying an
   *  actual value still do. */
  const pbtUsable = (f: DisplayObservedField<number>): boolean =>
    usable(f) && (pbtDisplay(f).state === 'current' || pbtDisplay(f).state === 'stale');
  const numberOf = (f: DisplayObservedField<number>, fallback: number): number =>
    f.display?.state === 'current' || f.display?.state === 'stale' ? f.display.value
      : f.reading.status === 'known' ? f.reading.value : fallback;
</script>

<script lang="ts">
  import { onDestroy, untrack } from 'svelte';
  import { t } from '$lib/i18n';
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
    part?: 'all' | 'filter';
    filterWidthFeedback?: Readonly<CommandScalarFeedback>;
    ifShiftFeedback?: Readonly<CommandScalarFeedback>;
    pbtInnerFeedback?: Readonly<CommandScalarFeedback>;
    pbtOuterFeedback?: Readonly<CommandScalarFeedback>;
    onFilterWidthChange?: (width: number) => void;
    onIfShiftChange?: (value: number) => void;
    onPbtInnerChange?: (value: number) => void;
    onPbtOuterChange?: (value: number) => void;
    onPbtReset?: () => void;
  }
  let {
    view, handles, finiteLayout, part = 'all', filterWidthFeedback,
    ifShiftFeedback, pbtInnerFeedback, pbtOuterFeedback,
    onFilterWidthChange, onIfShiftChange, onPbtInnerChange, onPbtOuterChange, onPbtReset,
  }: Props = $props();

  const pendingFilterId = $props.id();
  const feedbackIntegratedRange = { 'feedback-policy': 'feedback-integrated' } as const;

  let modeFilter = $derived(view.modeFilter);
  let filterPassband = $derived(view.filterPassband);
  let fixedWidth = $derived(modeFilter?.activeFilterConfiguration?.fixed === true);

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

  /** MOR-1681/MOR-2497: a passband row reads its range/step from the
   *  group's published domain when it carries one — `ifShiftDomain` for the
   *  IF-shift row, `pbtDomain` (the measured twin-PBT lattice at the
   *  observed width and the mode's step) for the PBT rows; every row
   *  without a domain keeps its `FILTER_PASSBAND_LEVELS` constant. */
  function passbandLimits(
    field: FilterPassbandLevelField, min: number, max: number, step: number,
  ): Readonly<{ min: number; max: number; step: number }> {
    const domain = field === 'ifShift'
      ? filterPassband?.ifShiftDomain
      : filterPassband?.pbtDomain;
    return domain === undefined ? { min, max, step } : domain;
  }

  /** The command each passband row's request rides — the same intents
   *  `makeFilterHandlers` dispatches, so the feedback's lifecycle ids match
   *  the commands this surface issues. */
  const PASSBAND_COMMAND: Record<FilterPassbandLevelField, string> = {
    ifShift: 'set_if_shift', pbtInner: 'set_pbt_inner', pbtOuter: 'set_pbt_outer',
  };

  function passbandFeedbackOf(field: FilterPassbandLevelField): Readonly<CommandScalarFeedback> | undefined {
    if (field === 'ifShift') return ifShiftFeedback;
    if (field === 'pbtInner') return pbtInnerFeedback;
    return pbtOuterFeedback;
  }

  /** The width row's contract applied to a passband row: feedback evidence
   *  when the wiring seam supplies an AVAILABLE projection (values in the
   *  display unit, Hz), reading evidence otherwise — a held (stale) or
   *  lattice-less reading keeps commanding through its own view truth
   *  (MOR-2425/R40), the same fallback `RfFrontEnd.svelte`'s `rfGainInput`
   *  established. The field's own availability stays the enabled gate in
   *  both branches. */
  function passbandInput(field: FilterPassbandLevelField): Readonly<ContinuousScalarInput> {
    const row = FILTER_PASSBAND_LEVELS.find(([name]) => name === field)!;
    const limits = passbandLimits(field, row[2], row[3], row[4]);
    const f = filterPassband?.[field];
    const enabled = field === 'ifShift'
      ? f !== undefined && usable(f)
      : f !== undefined && pbtUsable(f);
    const domain = {
      min: limits.min, max: limits.max, step: limits.step,
      defaultValue: null, fineStepDivisor: 1,
    };
    const request = (value: number): void => changePassband(field, value);
    const feedback = passbandFeedbackOf(field);
    if (feedback !== undefined && feedback.availability === 'available') {
      return {
        evidence: 'command-feedback', feedback, command: PASSBAND_COMMAND[field],
        domain, enabled, request,
      };
    }
    const value = f === undefined ? null : numberOf(f, limits.min);
    return {
      evidence: 'reading',
      reading: enabled && value !== null && Number.isFinite(value)
        ? { status: 'known', value } : { status: 'unknown' },
      ownerKey: `filter-passband-${field}`, domain, enabled, request,
    };
  }

  /** The width row's lease/view plumbing, once per passband row. */
  function createPassbandRow(field: FilterPassbandLevelField): Readonly<{
    lease: ContinuousScalarRendererLease | null;
    view: Readonly<ContinuousScalarView>;
  }> {
    const scalar = createContinuousScalar(
      () => passbandInput(field), nativeRangeContinuousScalarPolicy,
    );
    let lease: ContinuousScalarRendererLease | null = $state(null);
    const initialView = untrack(() => scalar.view);
    let view: Readonly<ContinuousScalarView> = $state(initialView);
    $effect(() => {
      const attached = scalar.attachRenderer();
      lease = attached;
      return () => attached.dispose();
    });
    $effect(() => {
      view = lease === null ? scalar.view : lease.view;
    });
    onDestroy(() => scalar.destroy());
    return {
      get lease() { return lease; },
      get view() { return view; },
    };
  }
  const passbandRows: Record<FilterPassbandLevelField, Readonly<{
    lease: ContinuousScalarRendererLease | null;
    view: Readonly<ContinuousScalarView>;
  }>> = {
    ifShift: createPassbandRow('ifShift'),
    pbtInner: createPassbandRow('pbtInner'),
    pbtOuter: createPassbandRow('pbtOuter'),
  };
</script>

{#if modeFilter || filterPassband}
  <section class="filter-surface" data-testid={part === 'all' ? 'filter-surface' : `${part}-surface`}
    aria-label={part === 'filter' ? 'Filter controls' : 'Mode and filter controls'}>
    {#if finiteLayout}
      {@render finiteLayout(handles)}
    {:else}
      {#if modeFilter && part !== 'filter'}
        {@render handles.mode()}
      {/if}
      {#if modeFilter}
        {@render handles.filter()}
      {/if}
    {/if}
    {#if modeFilter}
      {#if modeFilter.filterWidth.availability.structural}
        <label class="filter-level" data-testid="filter-width" data-disabled-reason={reasonOf(modeFilter.filterWidth)}>
          <span class="filter-level-name">Width</span>
          {#if !fixedWidth}
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
          {/if}
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
        {@const row = passbandRows[field]}
        <!-- The continuity-fenced view model stays the display truth for the
             thumb (MOR-1706): a conflicting state replay must not move it,
             so only the scalar's own gesture draft may override the view. -->
        {#key display?.state}
          <input
            id={`${pendingFilterId}-${field}-input`} type="range" {min} {max} {step}
            {...feedbackIntegratedRange}
            value={row.view.draft ?? numberOf(filterPassband[field], min)}
            disabled={!row.view.editable}
            data-command-phase={row.view.phase ?? undefined}
            aria-busy={row.view.busy}
            oninput={(event) => row.lease?.nativeInput(event.currentTarget.valueAsNumber)}
          />
        {/key}
      {/snippet}
      {#if !finiteLayout}
        {@render handles.shape()}
      {/if}
      {#each FILTER_PASSBAND_LEVELS as [field, label, min, max, step] (field)}
        {#if field === 'ifShift' ? filterPassband.ifShiftControlStructural : filterPassband[field].availability.structural}
          {@const limits = passbandLimits(field, min, max, step)}
          {#if field === 'pbtInner' || field === 'pbtOuter'}
            {@const display = pbtDisplay(filterPassband[field])}
            {@const measured = display.state === 'current' || display.state === 'stale'}
            <div
              class="filter-level" data-testid={`filter-${field}`} role="group" aria-label={label}
              data-disabled-reason={reasonOf(filterPassband[field])}
              data-presentation={display.state === 'current' ? 'confirmed' : display.state === 'stale' ? 'retained' : 'unknown'}
            >
              <label class="filter-level-name" for={`${pendingFilterId}-${field}-input`}>{label}</label>
              <span class="pbt-slot" data-pbt-slot>
                {#if display.state === 'current' || display.state === 'stale'}
                  {@render passbandRange(field, limits.min, limits.max, limits.step)}
                {:else}
                  <span class="pbt-unknown">{t('core.vfo.state.unknown')}</span>
                {/if}
              </span>
              <output class="pbt-value" aria-live="off">{measured && 'value' in display ? display.value : '—'}</output>
            </div>
          {:else}
            <label
              class="filter-level" data-testid={`filter-${field}`}
              data-disabled-reason={reasonOf(filterPassband[field])}
              data-presentation={presentationOf(filterPassband[field])}
            >
              <span class="filter-level-name">{label}</span>
              {@render passbandRange(field, limits.min, limits.max, limits.step)}
              <output>{textOf(filterPassband[field])}</output>
            </label>
          {/if}
        {/if}
      {/each}
      {#if filterPassband.pbtInner.availability.structural && filterPassband.pbtOuter.availability.structural}
        <button
          type="button" class="pbt-reset-button" data-testid="filter-pbt-reset"
          onclick={() => onPbtReset?.()}
        >Reset</button>
      {/if}
      {#if !finiteLayout && part !== 'filter'}
        {@render handles.dataMode()}
      {/if}
    {/if}
  </section>
{/if}

<style>
  /* Structure only — a design language owns colour (MOR-977, forced-colors). */
  .filter-surface { display: flex; flex-direction: column; gap: 0.25rem; }
  .filter-level { display: flex; align-items: baseline; gap: 0.5rem; }
  .filter-level-name { min-width: 8ch; }
  .pbt-slot { display: inline-flex; align-items: center; width: 8rem; height: 1.5rem; }
  .pbt-slot input { width: 100%; margin-inline: 0; }
  .pbt-unknown { width: 100%; text-align: center; }
  .pbt-value { min-width: 6ch; font-variant-numeric: tabular-nums; }
  .pbt-reset-button { align-self: flex-start; }
  .sr-only {
    position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
    overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0;
  }
</style>
