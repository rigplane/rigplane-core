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

  DOUBLE-CLICK RESET (MOR-2535). The reset gesture lives on the row's LABEL
  and VALUE cell, never on the range track — a track double-click would fire
  native `input` events (position commands) before the reset, so the track
  keeps its untouched single-gesture behaviour. Two mechanisms, by row:
  - IF shift resets through the scalar lease's `reset()`: the row carries
    `defaultValue: 0` (zero offset).
  - Width has two reset paths, chosen by prop presence. When the wiring
    seam passes `onWidthReset` (the connected profile declares a writeable
    radio-default width code — the `filter_width_radio_default`
    capability), the double-click takes that ONE atomic dispatch site:
    `reset_filter_width` returns the width to the RADIO's own
    mode-dependent default (owner ruling 2026-09-22), and the slider moves
    when the post-write readback arrives — no draft, no pending target, no
    lifecycle wait. Without the prop the width row keeps its lease
    `reset()`: the profile's factory default for the CURRENT filter
    selection where the mode-keyed configuration declares one — and none
    where it does not, in which case the policy's `reset` returns null, no
    candidate forms, and nothing is dispatched (no invented default,
    nothing shown).
  - PBT resets stay ATOMIC through the `onPbtReset` prop: the button AND a
    row double-click call that ONE handler, which gates the combined
    operation and emits inner+outer in its established order. The PBT rows
    carry no lease-level default, because a per-row lease reset is not the
    PBT mechanism.
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
    bindToggleInstrument,
  } from '../primitives/control-instruments/control-instrument-behavior';
  import {
    createContinuousScalar, nativeRangeContinuousScalarPolicy,
    type CommandScalarFeedback, type ContinuousScalarInput,
    type ContinuousScalarRendererLease, type ContinuousScalarView,
  } from '../primitives/scalar/continuous-scalar.svelte';
  import type { PoliteControlAnnouncement } from '../primitives/control-feedback/control-feedback-presentation';
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
    /** MOR-2535 follow-up: the width row's radio-default reset dispatch
     *  site, passed by the wiring seam ONLY when the connected profile
     *  carries the `filter_width_radio_default` capability. Prop presence
     *  selects the double-click path: present → this ONE call (the radio
     *  resolves its own default); absent → the lease `reset()` to the
     *  profile-declared factory width, exactly as before. Same
     *  atomic-prop shape as `onPbtReset`. */
    onWidthReset?: () => void;
    onIfShiftChange?: (value: number) => void;
    onPbtInnerChange?: (value: number) => void;
    onPbtOuterChange?: (value: number) => void;
    /**
     * MOR-2640: NARROW for the ACTIVE receiver. The confirmed `narrow`
     * reading stays the pressed state's sole source (never the pending
     * target — MOR-1441 leg-1 doctrine); `pendingNarrow` is the
     * display-only in-flight marker. The wiring seam passes the handler
     * (which owns the zero-argument inversion) and reads the pending
     * target off the command lifecycle, like the DSP toggles' path.
     */
    onNarrowToggle?: () => void;
    pendingNarrow?: boolean | null;
    /** MOR-2535: the ONE PBT reset dispatch site — the reset button and a
     *  double-click on either PBT row's label/value cell both call this, so
     *  the atomic inner+outer reset (and its gating) stays in the wiring
     *  seam's handler, not split across two lease resets. */
    onPbtReset?: () => void;
  }
  let {
    view, handles, finiteLayout, part = 'all', filterWidthFeedback,
    ifShiftFeedback, pbtInnerFeedback, pbtOuterFeedback,
    onFilterWidthChange, onWidthReset, onIfShiftChange, onPbtInnerChange, onPbtOuterChange,
    onPbtReset, onNarrowToggle, pendingNarrow = null,
  }: Props = $props();

  const pendingFilterId = $props.id();
  const feedbackIntegratedRange = { 'feedback-policy': 'feedback-integrated' } as const;

  let modeFilter = $derived(view.modeFilter);
  let filterPassband = $derived(view.filterPassband);
  let fixedWidth = $derived(modeFilter?.activeFilterConfiguration?.fixed === true);

  /** MOR-2535: the width row's double-click reset target — the factory width
   *  the profile's mode-keyed filter configuration declares for the CURRENT
   *  filter selection (`activeFilterConfiguration.slots[].factoryWidthHz`,
   *  itself the adapter's `resolveFilterModeConfig` output; this file only
   *  joins the two published facts, it never re-derives the config). `null`
   *  when there is no configuration, no observed filter selection, or no
   *  declared default for that slot — the double-click is then a no-op and
   *  nothing is shown, never a fallback to an invented value. This lease
   *  path runs only when the wiring seam passes NO `onWidthReset`; with the
   *  radio-default capability the gesture routes there instead. */
  function filterWidthDefaultHz(): number | null {
    const config = modeFilter?.activeFilterConfiguration;
    const current = modeFilter?.currentFilter;
    if (config === null || config === undefined
      || current === undefined || current.reading.status !== 'known') return null;
    // Bound once: property-chain narrowing does not survive into the `find`
    // callback below.
    const selected = current.reading.value;
    return config.slots.find((slot) => slot.filter === selected)?.factoryWidthHz ?? null;
  }

  function filterWidthInput(): Readonly<ContinuousScalarInput> {
    const field = modeFilter?.filterWidth;
    const domain = {
      min: modeFilter ? numberOf(modeFilter.filterWidthMin, 50) : 50,
      max: modeFilter ? numberOf(modeFilter.filterWidthMax, 9999) : 9999,
      step: 50, defaultValue: filterWidthDefaultHz(), fineStepDivisor: 1,
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
  type IssuedAnnouncement = Readonly<{
    authorityKey: string;
    eventKey: string;
    text: string;
  }>;
  function scalarAuthorityKey(current: Readonly<ContinuousScalarView>): string {
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
  function formatExactWidth(value: number): string {
    return Number.isFinite(value) ? `${value} Hz` : '--- Hz';
  }
  function formatWidthAnnouncementText(
    feedback: Readonly<CommandScalarFeedback>,
    phase: Readonly<PoliteControlAnnouncement['phase']>,
    error: string | null,
  ): string {
    const target = Number.isFinite(feedback.requestedTarget ?? Number.NaN)
      ? String(feedback.requestedTarget) : '---';
    const confirmed = Number.isFinite(feedback.confirmed ?? Number.NaN)
      ? String(feedback.confirmed) : '---';
    let message: string;
    switch (phase) {
      case 'submitted':
      case 'queued':
      case 'dispatched':
      case 'awaiting-confirmation':
        message = t('core.filter.width.pendingAnnouncement', { target });
        break;
      case 'confirmed':
        message = t('core.filter.width.confirmedAnnouncement', { confirmed });
        break;
      case 'failed':
        message = t('core.filter.width.failedAnnouncement', { target, confirmed });
        break;
      case 'timed-out':
        message = t('core.filter.width.timedOutAnnouncement', { target, confirmed });
        break;
      case 'cancelled':
        message = t('core.filter.width.cancelledAnnouncement', { target, confirmed });
        break;
      case 'superseded':
        message = t('core.filter.width.supersededAnnouncement', { target, confirmed });
        break;
      default:
        message = '';
    }
    return error === null || message.length === 0 ? message : `${message}: ${error}`;
  }
  function nextFilterWidthAnnouncement(
    current: Readonly<ContinuousScalarView>,
    previous: IssuedAnnouncement | null,
  ): IssuedAnnouncement | null {
    const authorityKey = scalarAuthorityKey(current);
    const issued = current.presentation?.politeAnnouncement;
    if (issued === null || issued === undefined || current.evidence !== 'command-feedback') {
      return previous?.authorityKey === authorityKey ? previous : null;
    }
    return Object.freeze({
      authorityKey,
      eventKey: JSON.stringify([authorityKey, issued.transitionId]),
      text: formatWidthAnnouncementText(current.feedback, issued.phase, current.error),
    });
  }
  let filterWidthAnnouncement: IssuedAnnouncement | null = $state(
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
  /** MOR-2535 follow-up: the ONE width-row double-click routing decision.
   *  A fixed-width mode has no width to set (the track is not drawn
   *  either), so the gesture stays gated off there on both paths. With the
   *  radio-default prop wired, the reset is that single atomic dispatch —
   *  the lease `reset()` (and its profile-table default) is bypassed, per
   *  the owner ruling that the target is the RADIO's own default, not a
   *  profile entry. */
  function resetWidthRow(): void {
    if (fixedWidth) return;
    if (onWidthReset !== undefined) {
      onWidthReset();
      return;
    }
    filterWidthLease?.reset();
  }

  function changePassband(field: FilterPassbandLevelField, value: number): void {
    if (!filterPassband || !usable(filterPassband[field])) return;
    if (field !== 'ifShift' && !pbtUsable(filterPassband[field])) return;
    if (field === 'ifShift') onIfShiftChange?.(value);
    else if (field === 'pbtInner') onPbtInnerChange?.(value);
    else onPbtOuterChange?.(value);
  }

  /** MOR-2640: the NARROW key for the active receiver — the same
   *  toggle shape `DspInstrumentHost`'s NR/NB keys use
   *  (`bindToggleInstrument`, `aria-pressed={behavior.confirmed}`): the
   *  confirmed reading is the pressed state's sole source, so an unread
   *  reading draws the unlit label with no `aria-pressed` at all (never a
   *  fabricated "off"), and the pending target never lights the key. The
   *  handler owns its zero-argument inversion; the binding's next value
   *  is intentionally not forwarded. */
  const narrowBehavior = bindToggleInstrument(() => ({
    field: filterPassband?.narrow,
    invoke: () => onNarrowToggle?.(),
  }));
  const narrowPendingId = `${pendingFilterId}-narrow`;

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
   *  (MOR-2425/R40). The field's own availability stays the enabled gate
   *  in both branches. */
  function passbandInput(field: FilterPassbandLevelField): Readonly<ContinuousScalarInput> {
    const row = FILTER_PASSBAND_LEVELS.find(([name]) => name === field)!;
    const limits = passbandLimits(field, row[2], row[3], row[4]);
    const f = filterPassband?.[field];
    const enabled = field === 'ifShift'
      ? f !== undefined && usable(f)
      : f !== undefined && pbtUsable(f);
    const domain = {
      min: limits.min, max: limits.max, step: limits.step,
      // MOR-2535: the IF-shift row's lease reset target is the 0 Hz zero
      // offset — it sits on every lattice this row uses (the fallback rows
      // and the measured twin-PBT lattice are both centred on it), so the
      // target is never invented. The PBT rows declare NO lease-level
      // default: their reset is the atomic `onPbtReset` prop call (see the
      // file header), so a null here also keeps `lease.reset()` a no-op on
      // them.
      defaultValue: field === 'ifShift' ? 0 : null, fineStepDivisor: 1,
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

  /** The width row's announcement contract, formatted through the ONE
   *  passband message family (`core.filter.passband.*Announcement`) whose
   *  `{control}` placeholder resolves from `core.filter.passband.control.*`
   *  — same keys the v2 FilterPanel's issued-status formatter reads. */
  function formatPassbandAnnouncementText(
    field: FilterPassbandLevelField,
    feedback: Readonly<CommandScalarFeedback>,
    phase: Readonly<PoliteControlAnnouncement['phase']>,
    error: string | null,
  ): string {
    const controlName = t(`core.filter.passband.control.${field}`);
    const target = feedback.requestedTarget !== null && Number.isFinite(feedback.requestedTarget)
      ? `${feedback.requestedTarget} Hz` : '--- Hz';
    const confirmed = feedback.confirmed !== null && Number.isFinite(feedback.confirmed)
      ? `${feedback.confirmed} Hz` : '--- Hz';
    let message: string;
    switch (phase) {
      case 'submitted':
      case 'queued':
      case 'dispatched':
      case 'awaiting-confirmation':
        message = t('core.filter.passband.pendingAnnouncement', { control: controlName, target });
        break;
      case 'confirmed':
        message = t('core.filter.passband.confirmedAnnouncement', { control: controlName, confirmed });
        break;
      case 'failed':
        message = t('core.filter.passband.failedAnnouncement', { control: controlName, target, confirmed });
        break;
      case 'timed-out':
        message = t('core.filter.passband.timedOutAnnouncement', { control: controlName, target, confirmed });
        break;
      case 'cancelled':
        message = t('core.filter.passband.cancelledAnnouncement', { control: controlName, target, confirmed });
        break;
      case 'superseded':
        message = t('core.filter.passband.supersededAnnouncement', { control: controlName, target, confirmed });
        break;
      default:
        message = '';
    }
    return error === null ? message : `${message.replace(/[.!?]$/, '')}: ${error}`;
  }
  function nextPassbandAnnouncement(
    field: FilterPassbandLevelField,
    current: Readonly<ContinuousScalarView>,
    previous: IssuedAnnouncement | null,
  ): IssuedAnnouncement | null {
    const authorityKey = scalarAuthorityKey(current);
    if (current.evidence !== 'command-feedback') {
      return previous?.authorityKey === authorityKey ? previous : null;
    }
    const issued = current.presentation?.politeAnnouncement;
    if (issued === null || issued === undefined || current.announcement === null) {
      return previous?.authorityKey === authorityKey ? previous : null;
    }
    return Object.freeze({
      authorityKey,
      eventKey: JSON.stringify([authorityKey, issued.transitionId]),
      text: formatPassbandAnnouncementText(field, current.feedback, issued.phase, current.error),
    });
  }

  /** The width row's lease/view plumbing, once per passband row. */
  function createPassbandRow(field: FilterPassbandLevelField): Readonly<{
    lease: ContinuousScalarRendererLease | null;
    view: Readonly<ContinuousScalarView>;
    announcement: IssuedAnnouncement | null;
  }> {
    const scalar = createContinuousScalar(
      () => passbandInput(field), nativeRangeContinuousScalarPolicy,
    );
    let lease: ContinuousScalarRendererLease | null = $state(null);
    const initialView = untrack(() => scalar.view);
    let view: Readonly<ContinuousScalarView> = $state(initialView);
    let announcement: IssuedAnnouncement | null = $state(
      nextPassbandAnnouncement(field, initialView, null),
    );
    $effect(() => {
      const attached = scalar.attachRenderer();
      lease = attached;
      return () => attached.dispose();
    });
    $effect(() => {
      const next = lease === null ? scalar.view : lease.view;
      view = next;
      announcement = nextPassbandAnnouncement(field, next, untrack(() => announcement));
    });
    onDestroy(() => scalar.destroy());
    return {
      get lease() { return lease; },
      get view() { return view; },
      get announcement() { return announcement; },
    };
  }
  const passbandRows: Record<FilterPassbandLevelField, Readonly<{
    lease: ContinuousScalarRendererLease | null;
    view: Readonly<ContinuousScalarView>;
    announcement: IssuedAnnouncement | null;
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
          <!-- MOR-2535: the reset gesture lives on the label and the value
               cell, NOT on the track — a track double-click fires native
               `input` events (position commands) before `dblclick` would
               run. `resetWidthRow()` picks the path: the atomic
               `onWidthReset` dispatch when the radio-default capability is
               wired, else the lease reset — which dispatches nothing when
               no declared factory default exists, so the hint appears only
               when a reset would actually do something. A FIXED-width radio
               has no width to set (the track is not drawn either), so the
               gesture is gated off there too. -->
          <!-- svelte-ignore a11y_no_static_element_interactions -->
          <span
            class="filter-level-name"
            title={!fixedWidth && (onWidthReset !== undefined || filterWidthDefaultHz() !== null) ? 'Double-click: default' : undefined}
            ondblclick={resetWidthRow}
          >Width</span>
          {#if !fixedWidth}
            <input
              type="range"
              {...feedbackIntegratedRange}
              min={numberOf(modeFilter.filterWidthMin, 50)} max={numberOf(modeFilter.filterWidthMax, 9999)} step={50}
              value={filterWidthView.displayed ?? numberOf(modeFilter.filterWidth, 0)}
              disabled={!filterWidthView.editable}
              data-command-phase={filterWidthView.phase ?? undefined}
              aria-busy={filterWidthView.evidence === 'command-feedback'
                ? filterWidthView.presentation.attributes['aria-busy'] : undefined}
              aria-valuenow={filterWidthView.canonical ?? undefined}
              aria-valuetext={filterWidthView.canonical === null
                ? undefined : formatExactWidth(filterWidthView.canonical)}
              oninput={(event) => filterWidthLease?.nativeInput(event.currentTarget.valueAsNumber)}
            />
          {/if}
          <!-- svelte-ignore a11y_no_static_element_interactions -->
          <output ondblclick={resetWidthRow}>{textOf(modeFilter.filterWidth)}</output>
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
             thumb (MOR-1706): a conflicting state replay must not move it.
             Above it, two explicitly unconfirmed sources may: the scalar's
             own gesture draft, and — while a command is in flight — the
             pending target in Hz (MOR-1691: the thumb shows the operator's
             requested value until readback confirms or terminates). -->
        {@const pendingTarget = row.view.busy && row.view.target !== null ? row.view.target : null}
          {#key display?.state}
          <input
            id={`${pendingFilterId}-${field}-input`} type="range" {min} {max} {step}
            {...feedbackIntegratedRange}
            value={row.view.draft ?? pendingTarget ?? numberOf(filterPassband[field], min)}
            disabled={!row.view.editable}
            data-command-phase={row.view.phase ?? undefined}
            aria-busy={row.view.busy}
            oninput={(event) => row.lease?.nativeInput(event.currentTarget.valueAsNumber)}
          />
          {/key}
      {/snippet}
      {#snippet passbandStatus(field: FilterPassbandLevelField)}
        {@const announcement = passbandRows[field].announcement}
        {#if announcement !== null}
          {#key announcement.eventKey}
            <span class="sr-only" role="status" aria-live="polite" aria-atomic="true"
              data-control-feedback-status>{announcement.text}</span>
          {/key}
        {/if}
      {/snippet}
      {#if !finiteLayout}
        {@render handles.shape()}
      {/if}
      {#if filterPassband.narrow.availability.structural}
        <!-- MOR-2640: the NARROW key — a KEY, not a `NAME: value` row
             (MOR-2527 owner rule, same as `DspInstrumentHost`'s NR/NB keys
             and the RF front-end's DIGI-SEL/IP+ keys). The label only, lit
             by the confirmed reading: an unread reading draws the unlit
             label with no `: ?`/`: on`/`: off` text at all, and the pending
             target is a display-only marker — it never lights the key. -->
        <button
          type="button" class="filter-toggle" data-testid="filter-narrow"
          data-disabled-reason={usable(filterPassband.narrow) ? undefined : 'field-not-observed'}
          aria-pressed={narrowBehavior.confirmed}
          data-pending-status={pendingNarrow !== null ? 'pending' : 'confirmed'}
          aria-describedby={pendingNarrow !== null ? narrowPendingId : undefined}
          disabled={!narrowBehavior.available}
          onclick={() => narrowBehavior.invoke()}
        >NARROW</button>
        {#if pendingNarrow !== null}<span id={narrowPendingId} class="sr-only">{t('core.dsp.pendingAnnouncement')}</span>{/if}
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
              <!-- MOR-2535: double-click on a PBT row calls the SAME atomic
                   `onPbtReset` prop the reset button uses — one dispatch
                   site, never two independent lease resets. The gesture
                   targets the label/value cell, not the track. -->
              <!-- svelte-ignore a11y_no_static_element_interactions -->
              <label
                class="filter-level-name" for={`${pendingFilterId}-${field}-input`}
                title="Double-click: default"
                ondblclick={() => onPbtReset?.()}
              >{label}</label>
              <span class="pbt-slot" data-pbt-slot>
                {#if display.state === 'current' || display.state === 'stale'}
                  {@render passbandRange(field, limits.min, limits.max, limits.step)}
                {:else}
                  <span class="pbt-unknown">{t('core.vfo.state.unknown')}</span>
                {/if}
              </span>
              <!-- svelte-ignore a11y_no_static_element_interactions -->
              <output class="pbt-value" aria-live="off" ondblclick={() => onPbtReset?.()}>{measured && 'value' in display ? display.value : '—'}</output>
              {@render passbandStatus(field)}
            </div>
          {:else}
            <label
              class="filter-level" data-testid={`filter-${field}`}
              data-disabled-reason={reasonOf(filterPassband[field])}
              data-presentation={presentationOf(filterPassband[field])}
            >
              <!-- svelte-ignore a11y_no_static_element_interactions -->
              <span
                class="filter-level-name"
                title="Double-click: default"
                ondblclick={() => passbandRows.ifShift.lease?.reset()}
              >{label}</span>
              {@render passbandRange(field, limits.min, limits.max, limits.step)}
              <!-- svelte-ignore a11y_no_static_element_interactions -->
              <output ondblclick={() => passbandRows.ifShift.lease?.reset()}>{textOf(filterPassband[field])}</output>
              {@render passbandStatus(field)}
            </label>
          {/if}
        {/if}
      {/each}
      {#if filterPassband.pbtInner.availability.structural && filterPassband.pbtOuter.availability.structural}
        <!-- MOR-2535: the button and the PBT rows' double-click share ONE
             dispatch site — the `onPbtReset` prop — whose handler gates the
             combined operation, converts 0 Hz to the lattice centre raw, and
             emits inner then outer in its established order. -->
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
  .filter-level[data-testid="filter-width"] input {
    border: 1px solid CanvasText;
  }
  @media (forced-colors: active) {
    .filter-level[data-testid="filter-width"] input { forced-color-adjust: none; }
    .filter-level[data-testid="filter-width"] [data-control-feedback-status] {
      forced-color-adjust: none;
      color: CanvasText;
    }
  }
  @media (prefers-reduced-motion: reduce) {
    .filter-level[data-testid="filter-width"],
    .filter-level[data-testid="filter-width"] * { transition: none; animation: none; }
  }
  /* MOR-2640: the NARROW key's light — the same structural
     (forced-colors safe) weight the DSP toggles use, on the state
     attribute itself. */
  .filter-toggle[aria-pressed='true'] { font-weight: 700; }
  .filter-toggle[data-pending-status='pending'] { font-style: italic; opacity: 0.75; }
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
