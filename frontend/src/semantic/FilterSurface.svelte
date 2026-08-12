<!--
  Semantic mode/filter surface (MOR-1304, vocabulary slice 4B).

  Presentation only. Renders BOTH the MOR-1280 `modeFilter` group (mode,
  filter selection, filter width) and the MOR-1284 `filterPassband` group
  (filter shape, IF-shift, PBT inner/outer, DATA submode) — the same two
  groups the v2 `FilterPanel` reads together (`panel-props.ts`'s
  `deriveFilterProps`). A surface consuming only one half would be half-built.

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

  PENDING AFFORDANCE (MOR-1441 leg 2). `pendingFilter` is a plain, command-
  bus-blind display prop — same "read at the wiring seam, hand down a plain
  value" precedent as `pendingFrequencyHz` (leg 1, `VfoSurface`). It marks
  the targeted filter-select CHOICE distinctly (`data-pending`) and the
  group `data-filter-status="pending"`, but `isSelected`/`aria-pressed`
  keep reading `modeFilter.currentFilter`'s CONFIRMED reading exclusively —
  the leg-1 lesson applied here: pending never becomes a selection source,
  so a click while pending still dispatches the CLICKED (explicit) value,
  never something computed off the pending display.
-->
<script module lang="ts">
  import type { TxAuxField } from './radio-view-model';

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
  const numberOf = (f: TxAuxField<number>, fallback: number): number =>
    f.reading.status === 'known' ? f.reading.value : fallback;
  /** Never fabricates a selection: `usable` alone cannot narrow `reading` for
   *  the `===` comparison below, so the known-check is repeated explicitly. */
  const isSelected = (f: TxAuxField<unknown>, value: unknown): boolean =>
    usable(f) && f.reading.status === 'known' && f.reading.value === value;
</script>

<script lang="ts">
  import { t } from '$lib/i18n';
  import type { RadioViewModel } from './radio-view-model';

  interface Props {
    view: RadioViewModel;
    /** MOR-1441 leg 2 — the freshest in-flight `set_filter` target for the
     *  active receiver, DISPLAY ONLY (see the file header). `null` when
     *  nothing is pending. */
    pendingFilter?: number | null;
    onModeChange?: (mode: string) => void;
    onFilterChange?: (filter: number) => void;
    onFilterWidthChange?: (width: number) => void;
    onFilterShapeChange?: (shape: number) => void;
    onIfShiftChange?: (value: number) => void;
    onPbtInnerChange?: (value: number) => void;
    onPbtOuterChange?: (value: number) => void;
  }
  let {
    view, pendingFilter = null, onModeChange, onFilterChange, onFilterWidthChange,
    onFilterShapeChange, onIfShiftChange, onPbtInnerChange, onPbtOuterChange,
  }: Props = $props();

  const pendingFilterId = $props.id();

  let modeFilter = $derived(view.modeFilter);
  let filterPassband = $derived(view.filterPassband);

  function selectMode(mode: string): void {
    if (modeFilter && usable(modeFilter.currentMode)) onModeChange?.(mode);
  }
  function selectFilter(filter: number): void {
    if (modeFilter && usable(modeFilter.currentFilter)) onFilterChange?.(filter);
  }
  function changeWidth(value: number): void {
    if (modeFilter && usable(modeFilter.filterWidth)) onFilterWidthChange?.(value);
  }
  function selectShape(shape: number): void {
    if (filterPassband && usable(filterPassband.filterShape)) onFilterShapeChange?.(shape);
  }
  /** One guarded entry point for all three passband sliders — each still
   *  reads and disables on its OWN field's availability (see the file
   *  header, rule 3), this only routes the already-checked value onward. */
  function changePassband(field: FilterPassbandLevelField, value: number): void {
    if (!filterPassband || !usable(filterPassband[field])) return;
    if (field === 'ifShift') onIfShiftChange?.(value);
    else if (field === 'pbtInner') onPbtInnerChange?.(value);
    else onPbtOuterChange?.(value);
  }
</script>

{#if modeFilter || filterPassband}
  <section class="filter-surface" data-testid="filter-surface" aria-label="Mode and filter controls">
    {#if modeFilter}
      {#if modeFilter.currentMode.availability.structural}
        <div
          class="filter-choice-group" data-testid="filter-mode"
          data-disabled-reason={reasonOf(modeFilter.currentMode)}
        >
          {#each modeFilter.modeChoices as choice (choice)}
            <button
              type="button" class="filter-choice" data-testid={`filter-mode-${choice}`}
              aria-pressed={isSelected(modeFilter.currentMode, choice)}
              disabled={!usable(modeFilter.currentMode)}
              onclick={() => selectMode(choice)}
            >{choice}</button>
          {/each}
        </div>
      {/if}
      {#if modeFilter.currentFilter.availability.structural}
        <div
          class="filter-choice-group" data-testid="filter-select"
          data-disabled-reason={reasonOf(modeFilter.currentFilter)}
          data-filter-status={pendingFilter !== null ? 'pending' : 'confirmed'}
          aria-describedby={pendingFilter !== null ? pendingFilterId : undefined}
        >
          {#each modeFilter.filterChoices as choice, index (choice)}
            <button
              type="button" class="filter-choice" data-testid={`filter-select-${index + 1}`}
              aria-pressed={isSelected(modeFilter.currentFilter, index + 1)}
              data-pending={pendingFilter === index + 1}
              disabled={!usable(modeFilter.currentFilter)}
              onclick={() => selectFilter(index + 1)}
            >{choice}</button>
          {/each}
          {#if pendingFilter !== null}
            <span id={pendingFilterId} class="sr-only">{t('core.filter.select.pendingAnnouncement')}</span>
          {/if}
        </div>
      {/if}
      {#if modeFilter.filterWidth.availability.structural}
        <label class="filter-level" data-testid="filter-width" data-disabled-reason={reasonOf(modeFilter.filterWidth)}>
          <span class="filter-level-name">Width</span>
          <input
            type="range"
            min={numberOf(modeFilter.filterWidthMin, 50)} max={numberOf(modeFilter.filterWidthMax, 9999)} step={50}
            value={numberOf(modeFilter.filterWidth, 0)}
            disabled={!usable(modeFilter.filterWidth)}
            oninput={(event) => changeWidth(event.currentTarget.valueAsNumber)}
          />
          <output>{textOf(modeFilter.filterWidth)}</output>
        </label>
      {/if}
    {/if}

    {#if filterPassband}
      {#if filterPassband.filterShapeControlStructural}
        <div
          class="filter-choice-group" data-testid="filter-shape"
          data-disabled-reason={reasonOf(filterPassband.filterShape)}
        >
          {#each FILTER_SHAPES as [value, label] (value)}
            <button
              type="button" class="filter-choice" data-testid={`filter-shape-${value}`}
              aria-pressed={isSelected(filterPassband.filterShape, value)}
              disabled={!usable(filterPassband.filterShape)}
              onclick={() => selectShape(value)}
            >{label}</button>
          {/each}
        </div>
      {/if}
      {#each FILTER_PASSBAND_LEVELS as [field, label, min, max, step] (field)}
        {#if field === 'ifShift' ? filterPassband.ifShiftControlStructural : filterPassband[field].availability.structural}
          <label
            class="filter-level" data-testid={`filter-${field}`}
            data-disabled-reason={reasonOf(filterPassband[field])}
          >
            <span class="filter-level-name">{label}</span>
            <input
              type="range" {min} {max} {step}
              value={numberOf(filterPassband[field], min)}
              disabled={!usable(filterPassband[field])}
              oninput={(event) => changePassband(field, event.currentTarget.valueAsNumber)}
            />
            <output>{textOf(filterPassband[field])}</output>
          </label>
        {/if}
      {/each}
      {#if filterPassband.dataMode.availability.structural}
        <div
          class="filter-readout" data-testid="filter-data-mode"
          data-disabled-reason={reasonOf(filterPassband.dataMode)}
        >
          <span class="filter-level-name">DATA</span><output>{textOf(filterPassband.dataMode)}</output>
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
