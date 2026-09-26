<!--
  RF front-end finite handles (MOR-2425 RF-B). Relocated here from
  `RfFrontEndSurface.svelte` when preamp/attenuator/DIGI-SEL/IP+ became
  finite-seat-owned, so this host now owns BOTH the continuous RF/SQL
  machinery below AND these four (DSP-shaped: one evolving host file, see
  `DspInstrumentHost.svelte`).

  CARRY-FORWARDS preserved from `RfFrontEndSurface.svelte`'s prior header
  (MOR-1292/MOR-1293 review rulings):

  (2)+(3) THE PREAMP MUTEX. PRE is genuinely disabled while DIGI-SEL is
      unobserved, by design (MOR-479 hardware mutex, IC-7610). Rendered as a
      disabled control WITH AN EXPLANATION, read from `presentation.view`'s
      `disabledReasons` matched on the DOTTED path `'rfFrontEnd.preamp'` —
      never a bespoke `preDisabled` boolean, and never `?? false`. The mutex
      disables the control on TOP of its own field usability.
  (4) The explanation is keyed off `DisabledReasonCode`, not off "DIGI-SEL" —
      `'mutually-exclusive-control'` names the SHAPE of the conflict, never
      this radio's specific peer control.

  PENDING AFFORDANCE (MOR-1441 leg 2). `pendingPreamp` is a plain, command-
  bus-blind display prop. It never touches the choice seat's own invoke
  path; a click while pending still dispatches the CLICKED (explicit) value.
-->
<script lang="ts">
  import { onDestroy, onMount, untrack, type Snippet } from 'svelte';
  import { t } from '$lib/i18n';
  import { toRadioViewModel } from '$lib/runtime/adapters/radio-view-model-adapter';
  import DualParamRenderer from '../components-v2/controls/value-control/DualParamRenderer.svelte';
  import { ValueControl } from '../components-v2/controls/value-control';
  import AttenuatorControl from '../components-v2/controls/AttenuatorControl.svelte';
  import type {
    DualParamIssuedStatusPresentation,
    DualParamIssuedStatusSnapshot,
    DualParamLane,
  } from '../components-v2/controls/value-control/dual-param-issued-status';
  import type {
    HBarIssuedStatusPresentation,
    HBarIssuedStatusSnapshot,
  } from '../components-v2/controls/value-control/skin';
  import { bindChoiceInstrument, bindToggleInstrument } from '../primitives/control-instruments/control-instrument-behavior';
  import ControlInstrumentRendererHost from '../primitives/control-instruments/ControlInstrumentRendererHost.svelte';
  import {
    createChoiceRendererSeat, createToggleRendererSeat,
    type FiniteControlAppearance, type FiniteRendererContext,
  } from '../primitives/control-instruments/control-instrument-renderer.svelte';
  import {
    createContinuousPair,
    createRenderedNativeRangeContinuousPairPolicy,
    type ContinuousPairInput,
    type ContinuousPairView,
  } from '../primitives/scalar/continuous-pair.svelte';
  import {
    createContinuousScalar,
    createRenderedNativeRangeContinuousScalarPolicy,
    type CommandScalarFeedback,
    type ContinuousScalarInput,
    type ContinuousScalarView,
  } from '../primitives/scalar/continuous-scalar.svelte';
  import { formatKnownLevel } from './format-level';
  import type { RadioViewModel, RfFrontEndField, DisabledReason, DisabledReasonCode } from './radio-view-model';
  import {
    DISABLED_REASON_LABEL,
    RF_FRONT_END_LEVELS,
    RF_FRONT_END_RAW_CONTROL_KEY,
    RF_FRONT_END_TOGGLES,
    type RfFrontEndAuthorityPublication,
    type RfFrontEndFiniteChoiceValue,
    type RfFrontEndInstrumentPresentation,
    type RfFrontEndLevelField,
    type RfFrontEndLevelHandles,
    type RfFrontEndToggleField,
    type RfSqlControlModel,
    type SubscribeRfFrontEndAuthority,
  } from './rf-front-end-instruments';

  interface ExistingProps {
    presentation: RfFrontEndInstrumentPresentation;
    subscribeControlAuthority: SubscribeRfFrontEndAuthority;
    onLevelChange?: (field: RfFrontEndLevelField, value: number) => void;
    pendingPreamp?: number | null;
    onPreChange?: (level: number) => void;
    onAttChange?: (db: number) => void;
    onDigiSelToggle?: (on: boolean) => void;
    onIpPlusToggle?: (on: boolean) => void;
    children: Snippet<[RfFrontEndLevelHandles]>;
  }
  type FiniteRendererSelection = { finiteAppearance?: undefined; rendererContext?: undefined } | {
    finiteAppearance: FiniteControlAppearance<RfFrontEndFiniteChoiceValue>;
    rendererContext: FiniteRendererContext | null;
  };
  type Props = ExistingProps & FiniteRendererSelection;

  type Receiver = 'MAIN' | 'SUB';
  interface RfAuthority {
    readonly epoch: number;
    readonly generation: number;
    readonly topologyId: string;
    readonly receiver: Receiver;
    readonly form: RfSqlControlModel;
  }

  let {
    presentation, subscribeControlAuthority, onLevelChange,
    pendingPreamp = null, onPreChange, onAttChange, onDigiSelToggle, onIpPlusToggle,
    finiteAppearance, rendererContext, children,
  }: Props = $props();
  let published = $state.raw<RfFrontEndAuthorityPublication | null>(null);
  let lastAuthority: RfAuthority | null | undefined;
  let stop: (() => void) | null = null;
  let rf = $derived(presentation.view?.rfFrontEnd);
  /** Carry-forwards 2/3: matched on the DOTTED field path, never re-derived
   *  from a raw DIGI-SEL read — the fact layer already decided this. */
  let preMutex = $derived(
    presentation.view?.disabledReasons.find((reason) => reason.field === 'rfFrontEnd.preamp') ?? null,
  );
  const preampId = $props.id();
  const pendingPreampId = `${preampId}-pending`;
  const preampMutexId = `${preampId}-mutex`;
  const attenuatorMutexId = `${preampId}-att-mutex`;

  const safeGeneration = (value: unknown): value is number =>
    typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
  const usable = (field: RfFrontEndField<unknown> | undefined): boolean =>
    field?.availability.structural === true
    && field.availability.operational
    && field.reading.status === 'known';
  function formOf(view: RadioViewModel, requested: RfSqlControlModel): RfSqlControlModel {
    return requested === 'combined'
      && view.rfFrontEnd?.rfGain.availability.structural === true
      && view.rfFrontEnd.squelch.availability.structural
      ? 'combined' : 'separate';
  }
  function authority(
    source: RfFrontEndAuthorityPublication,
    requested = source.caps?.rfSqlControlModel ?? 'separate',
  ): RfAuthority | null {
    const stateGeneration = source.state?.providerGeneration;
    const capsGeneration = source.caps?.providerGeneration;
    if (source.session.state !== 'connected'
      || !safeGeneration(source.session.epoch)
      || !safeGeneration(stateGeneration)
      || !safeGeneration(capsGeneration)
      || stateGeneration !== capsGeneration) return null;
    const view = source.view === undefined
      ? toRadioViewModel(source.state, source.caps) : source.view;
    if (view === null || view.activeReceiver.status !== 'known') return null;
    return {
      epoch: source.session.epoch,
      generation: stateGeneration,
      topologyId: view.topologyId,
      receiver: view.activeReceiver.receiver,
      form: formOf(view, requested),
    };
  }
  function same(left: RfAuthority | null, right: RfAuthority | null): boolean {
    if (left === null || right === null) return left === right;
    return left.epoch === right.epoch
      && left.generation === right.generation
      && left.topologyId === right.topologyId
      && left.receiver === right.receiver
      && left.form === right.form;
  }
  const authorityKey = (value: RfAuthority | null, field?: RfFrontEndLevelField): string =>
    value === null ? `rf-front-end:inactive:${field ?? 'pair'}` : JSON.stringify([
      'rf-front-end', field ?? 'pair', value.epoch, value.generation,
      value.topologyId, value.receiver, value.form,
    ]);

  let currentForm = $derived(presentation.view === null
    ? 'separate' : formOf(presentation.view, presentation.controlModel));
  const laneFor = (field: RfFrontEndLevelField): DualParamLane =>
    field === 'rfGain' ? 'rf' : 'sql';
  const PAIR_LANES: readonly DualParamLane[] = ['rf', 'sql'];
  const row = (field: RfFrontEndLevelField) =>
    RF_FRONT_END_LEVELS.find(([candidate]) => candidate === field)!;
  /** MOR-1676 part R: the slider domain comes from the published raw range
   *  (`caps.controls.rf_gain` / `caps.controls.squelch`), read the same way
   *  other controls read their ranges (`SemanticRadioSurfaces.svelte`'s
   *  `nbLevelRange` precedent: `runtime.caps?.controls?.<key> ?? null`).
   *  With the entry published the slider moves on the radio's raw integer
   *  lattice (step 1); without it the slider keeps the legacy normalized
   *  domain. The reading text stays a percentage either way. */
  const safeRawBound = (value: unknown): number | null =>
    typeof value === 'number' && Number.isSafeInteger(value) ? value : null;
  function rawRangeOf(key: string): { rawMin: number; rawMax: number } | null {
    const entry = (presentation.caps?.controls as
      Record<string, { raw_min?: unknown; raw_max?: unknown }> | undefined)?.[key];
    const rawMin = safeRawBound(entry?.raw_min);
    const rawMax = safeRawBound(entry?.raw_max);
    return rawMin !== null && rawMax !== null && rawMax > rawMin
      ? { rawMin, rawMax } : null;
  }
  function levelDomain(field: RfFrontEndLevelField): {
    min: number; max: number; step: number; defaultValue: null; fineStepDivisor: number;
  } {
    const raw = rawRangeOf(RF_FRONT_END_RAW_CONTROL_KEY[field]);
    if (raw !== null) {
      return { min: raw.rawMin, max: raw.rawMax, step: 1, defaultValue: null, fineStepDivisor: 1 };
    }
    return {
      min: RF_FRONT_END_LEVELS[0][2], max: RF_FRONT_END_LEVELS[0][3],
      step: RF_FRONT_END_LEVELS[0][4], defaultValue: null, fineStepDivisor: 10,
    };
  }
  const domainOf = (field: RfFrontEndLevelField): {
    min: number; max: number; step: number; defaultValue: null; fineStepDivisor: number;
  } => levelDomain(field);
  const normalizedToRaw = (field: RfFrontEndLevelField, normalized: number): number | null => {
    const raw = rawRangeOf(RF_FRONT_END_RAW_CONTROL_KEY[field]);
    if (raw === null || !Number.isFinite(normalized)) return null;
    return Math.round(normalized * raw.rawMax);
  };
  const rawToPercent = (field: RfFrontEndLevelField, raw: number): string => {
    const range = rawRangeOf(RF_FRONT_END_RAW_CONTROL_KEY[field]);
    if (range === null) return formatKnownLevel(raw, 0, 1);
    const span = range.rawMax - range.rawMin;
    return span <= 0 ? '0%' : `${Math.round(((raw - range.rawMin) / span) * 100)}%`;
  };
  const readingOf = (field: RfFrontEndLevelField): { status: 'known'; value: number } | { status: 'unknown' } => {
    const reading = rf?.[field].reading;
    if (reading?.status !== 'known' || typeof reading.value !== 'number'
      || !Number.isFinite(reading.value)) return { status: 'unknown' };
    const raw = normalizedToRaw(field, reading.value);
    return raw === null ? reading : { status: 'known', value: raw };
  };
  /** MOR-1676 part R: command-feedback lanes carry normalized 0..1
   *  `confirmed`/`target`/`requestedTarget` (the `set_rf_gain` /
   *  `set_squelch` descriptors' own unit — the snapshot contract is
   *  unchanged). On the raw lattice they project to raw ints with the same
   *  `Math.round(normalized * raw_max)` rule as the reading; without a
   *  published range the lane passes through untouched. */
  function feedbackLaneOf(
    field: RfFrontEndLevelField,
    lane: Readonly<{ command: string; feedback: Readonly<CommandScalarFeedback> }>,
  ): Readonly<{ command: string; feedback: Readonly<CommandScalarFeedback> }> {
    const raw = rawRangeOf(RF_FRONT_END_RAW_CONTROL_KEY[field]);
    if (raw === null) return lane;
    const project = (value: number | null): number | null =>
      value === null || !Number.isFinite(value) ? value : Math.round(value * raw.rawMax);
    const feedback = lane.feedback;
    return {
      command: lane.command,
      feedback: {
        ...feedback,
        confirmed: project(feedback.confirmed),
        target: project(feedback.target),
        requestedTarget: project(feedback.requestedTarget),
      },
    };
  };
  const feedbackIntegratedControl = { 'feedback-policy': 'feedback-integrated' } as const;
  const currentAuthority = (): RfAuthority | null => published === null ? null : authority(published);
  const presentedAuthority = (): RfAuthority | null => authority(
    { ...presentation, view: undefined }, presentation.controlModel,
  );
  const authorityCurrent = (): boolean => {
    const current = currentAuthority();
    return current !== null && same(current, presentedAuthority());
  };

  function request(field: RfFrontEndLevelField, value: number): void {
    if (!authorityCurrent()) return;
    if (presentation.rfSqlFeedback === undefined) {
      if (usable(rf?.[field])) onLevelChange?.(field, value);
    } else if (presentation.rfSqlFeedback !== null) {
      onLevelChange?.(field, value);
    }
  }
  function pairInput(): Readonly<ContinuousPairInput> {
    const common = {
      domain: levelDomain('rfGain'),
      requestRf: (value: number) => request('rfGain', value),
      requestSql: (value: number) => request('squelch', value),
    };
    const feedback = presentation.rfSqlFeedback;
    if (feedback === null) return {
      ...common, evidence: 'reading', enabled: false,
      ownerKey: authorityKey(currentAuthority()),
      rf: { reading: { status: 'unknown' }, availability: 'unavailable' },
      sql: { reading: { status: 'unknown' }, availability: 'unavailable' },
    };
    if (feedback !== undefined) return {
      ...common, evidence: 'command-feedback',
      rf: feedbackLaneOf('rfGain', feedback.rf), sql: feedbackLaneOf('squelch', feedback.sql),
      enabled: authorityCurrent() && currentForm === 'combined' && onLevelChange !== undefined,
    };
    const availability = (field: RfFrontEndField<number> | undefined) =>
      field?.availability.structural && field.availability.operational
        ? 'available' as const : 'unavailable' as const;
    return {
      ...common, evidence: 'reading', ownerKey: authorityKey(currentAuthority()),
      enabled: authorityCurrent() && currentForm === 'combined' && onLevelChange !== undefined,
      rf: {
        reading: readingOf('rfGain'),
        availability: availability(rf?.rfGain),
      },
      sql: {
        reading: readingOf('squelch'),
        availability: availability(rf?.squelch),
      },
    };
  }
  function scalarInput(field: RfFrontEndLevelField): Readonly<ContinuousScalarInput> {
    const common = { domain: levelDomain(field), request: (value: number) => request(field, value) };
    const feedback = presentation.rfSqlFeedback;
    if (feedback === null) return {
      ...common, evidence: 'reading', enabled: false,
      ownerKey: authorityKey(currentAuthority(), field), reading: { status: 'unknown' },
    };
    if (feedback !== undefined) {
      const lane = feedbackLaneOf(field, feedback[laneFor(field)]);
      return {
        ...common, evidence: 'command-feedback', command: lane.command, feedback: lane.feedback,
        enabled: authorityCurrent() && currentForm === 'separate'
          && rf?.[field].availability.structural === true && onLevelChange !== undefined,
      };
    }
    const fact = rf?.[field];
    return {
      ...common, evidence: 'reading', ownerKey: authorityKey(currentAuthority(), field),
      enabled: authorityCurrent() && currentForm === 'separate'
        && usable(fact) && onLevelChange !== undefined,
      reading: readingOf(field),
    };
  }

  const pair = createContinuousPair(pairInput, createRenderedNativeRangeContinuousPairPolicy());
  const scalars = {
    rfGain: createContinuousScalar(
      () => scalarInput('rfGain'), createRenderedNativeRangeContinuousScalarPolicy(),
    ),
    squelch: createContinuousScalar(
      () => scalarInput('squelch'), createRenderedNativeRangeContinuousScalarPolicy(),
    ),
  } satisfies Record<RfFrontEndLevelField, ReturnType<typeof createContinuousScalar>>;
  let pairView = $state.raw<Readonly<ContinuousPairView>>(untrack(() => pair.view));
  let scalarViews = $state.raw<Record<RfFrontEndLevelField, Readonly<ContinuousScalarView>>>({
    rfGain: untrack(() => scalars.rfGain.view),
    squelch: untrack(() => scalars.squelch.view),
  });
  $effect(() => { pairView = pair.view; });
  $effect(() => {
    scalarViews = { rfGain: scalars.rfGain.view, squelch: scalars.squelch.view };
  });

  let issuedStatus = $state<Record<RfFrontEndLevelField, string | null>>({
    rfGain: null, squelch: null,
  });
  let rendererEpoch = $state(0);
  let issuedStatusKey = $state<Record<RfFrontEndLevelField, string>>({
    rfGain: '', squelch: '',
  });
  function acceptStatus(
    field: RfFrontEndLevelField,
    feedback: Readonly<CommandScalarFeedback>,
    transitionId: string,
    message: string,
    error: string | null,
  ): string {
    issuedStatusKey[field] = JSON.stringify([
      feedback.providerGeneration ?? null, feedback.sessionEpoch, feedback.scope.control,
      feedback.scope.receiver, feedback.scope.slot ?? null, transitionId,
    ]);
    return error === null ? message : `${message.replace(/[.!?]$/, '')}: ${error}`;
  }
  const pairIssuedStatus: Readonly<DualParamIssuedStatusPresentation> = Object.freeze({
    rf: {
      get text() { return null; },
      format: ({ laneView, announcement }: Readonly<DualParamIssuedStatusSnapshot>) => acceptStatus(
        'rfGain', laneView.feedback, announcement.transitionId,
        announcement.message, laneView.error,
      ),
      accept: (text: string | null) => { issuedStatus.rfGain = text; },
    },
    sql: {
      get text() { return null; },
      format: ({ laneView, announcement }: Readonly<DualParamIssuedStatusSnapshot>) => acceptStatus(
        'squelch', laneView.feedback, announcement.transitionId,
        announcement.message, laneView.error,
      ),
      accept: (text: string | null) => { issuedStatus.squelch = text; },
    },
  });
  function scalarIssuedStatus(field: RfFrontEndLevelField): Readonly<HBarIssuedStatusPresentation> {
    return {
      get text() { return null; },
      format({ view, announcement }: Readonly<HBarIssuedStatusSnapshot>) {
        return acceptStatus(
          field, view.feedback, announcement.transitionId, announcement.message, view.error,
        );
      },
      accept(text) { issuedStatus[field] = text; },
    };
  }
  const scalarIssuedStatuses = {
    rfGain: scalarIssuedStatus('rfGain'), squelch: scalarIssuedStatus('squelch'),
  } satisfies Record<RfFrontEndLevelField, Readonly<HBarIssuedStatusPresentation>>;

  const feedbackIntegration = (): string => presentation.rfSqlFeedback === undefined
    ? 'compatibility-reading'
    : presentation.rfSqlFeedback === null ? 'authority-unresolved' : 'command-feedback';
  /** MOR-2527: an unread level renders NO value text — an unlit slot, never
   *  a `?` stand-in. The heading labels stay, and each `<output>` keeps its
   *  box reserved (`display: inline-block; min-width: 4ch` on
   *  `.rf-front-end-reading output` below — the widest value is `100%`), so
   *  the SQL label cannot move when a value arrives.
   *  MOR-1676 part R: on the raw lattice the value is already the raw int —
   *  the text is the percentage of the published raw range, same unit as
   *  today's normalized path. */
  const valueText = (field: RfFrontEndLevelField, value: number | null): string => value === null
    ? '' : rawToPercent(field, value);
  function pairLaneValue(view: Readonly<ContinuousPairView>, lane: DualParamLane): number | null {
    const laneView = view.lanes[lane];
    return view.draft?.[lane]
      ?? (laneView.evidence === 'command-feedback' ? laneView.feedback.target : null)
      ?? laneView.canonical;
  }
  function scalarValue(view: Readonly<ContinuousScalarView>): number | null {
    return view.draft
      ?? (view.evidence === 'command-feedback' ? view.feedback.target : null)
      ?? view.canonical;
  }
  function status(view: Readonly<ContinuousScalarView>): string {
    return view.evidence === 'command-feedback' ? view.error ?? '' : '';
  }
  function pairStatus(view: Readonly<ContinuousPairView>, lane: DualParamLane): string {
    const laneView = view.lanes[lane];
    return laneView.evidence === 'command-feedback' ? laneView.error ?? '' : '';
  }

  const preampChoices = (): readonly number[] => rf?.preValues ?? [];
  const attenuatorChoices = (): readonly number[] => rf?.attValues ?? [];
  const preampChoiceText = (value: number): string => value === 0 ? 'OFF' : `P${value}`;
  const attenuatorChoiceText = (value: number): string => value === 0 ? 'OFF' : `${value} dB`;
  const preampBehavior = bindChoiceInstrument<number>(() => ({
    field: rf?.preamp, choices: preampChoices(), blocked: preMutex !== null,
    invoke: (level) => onPreChange?.(level),
  }));
  const attenuatorBehavior = bindChoiceInstrument<number>(() => ({
    field: rf?.attenuator, choices: attenuatorChoices(),
    invoke: (db) => onAttChange?.(db),
  }));
  const digiSelBehavior = bindToggleInstrument(() => ({
    field: rf?.digiSel, invoke: (next) => onDigiSelToggle?.(next),
  }));
  const ipPlusBehavior = bindToggleInstrument(() => ({
    field: rf?.ipPlus, invoke: (next) => onIpPlusToggle?.(next),
  }));

  /** MOR-2511 B2: matches any disabled reason on 'rfFrontEnd.preamp' (mutex or receiver-lacks-control). */
  const preampDisabledReason = (): DisabledReason | undefined =>
    presentation.view?.disabledReasons.find((reason) => reason.field === 'rfFrontEnd.preamp');
  const preampReasonText = (): string | undefined => {
    const reason = preampDisabledReason();
    return reason === undefined ? undefined : reasonLabel(reason.code);
  };

  /** MOR-2511 B2: safe lookup of disabled reason label — returns undefined if code not in map. */
  const reasonLabel = (code: DisabledReasonCode | undefined): string | undefined =>
    code === undefined ? undefined : (DISABLED_REASON_LABEL[code] ? t(DISABLED_REASON_LABEL[code]!) : undefined);

  const preampSeat = createChoiceRendererSeat<number>(() => {
    const reason = preampReasonText();
    return {
      context: rendererContext ?? null, field: rf?.preamp, label: 'Preamp', blocked: preampDisabledReason() !== undefined,
      options: preampChoices().map((value) => ({
        value, label: preampChoiceText(value),
        ...(reason === undefined ? {} : { disabledReason: reason }),
      })),
      ...(pendingPreamp === null ? {} : {
        requested: { kind: 'requested-target' as const, target: pendingPreamp },
      }),
      invoke: (level) => onPreChange?.(level),
    };
  });

  /** MOR-2511 B2: matches any disabled reason on 'rfFrontEnd.attenuator' (receiver-lacks-control today; lookup by field so future reasons on this field are picked up too). */
  const attenuatorDisabledReason = (): DisabledReason | undefined =>
    presentation.view?.disabledReasons.find((reason) => reason.field === 'rfFrontEnd.attenuator');
  const attenuatorReasonText = (): string | undefined => {
    const reason = attenuatorDisabledReason();
    return reason === undefined ? undefined : reasonLabel(reason.code);
  };
  const attenuatorSeat = createChoiceRendererSeat<number>(() => {
    const reason = attenuatorReasonText();
    return {
      context: rendererContext ?? null, field: rf?.attenuator, label: 'Attenuator', blocked: attenuatorDisabledReason() !== undefined,
      options: attenuatorChoices().map((value) => ({
        value, label: attenuatorChoiceText(value),
        ...(reason === undefined ? {} : { disabledReason: reason }),
      })),
      invoke: (db) => onAttChange?.(db),
    };
  });
  const digiSelSeat = createToggleRendererSeat(() => ({
    context: rendererContext ?? null, field: rf?.digiSel, label: RF_FRONT_END_TOGGLES[0][1],
    invoke: (next) => onDigiSelToggle?.(next),
  }));
  const ipPlusSeat = createToggleRendererSeat(() => ({
    context: rendererContext ?? null, field: rf?.ipPlus, label: RF_FRONT_END_TOGGLES[1][1],
    invoke: (next) => onIpPlusToggle?.(next),
  }));

  onMount(() => {
    stop = subscribeControlAuthority((next) => {
      const nextAuthority = authority(next);
      if (lastAuthority !== undefined && !same(lastAuthority, nextAuthority)) {
        pair.cancel('authority');
        scalars.rfGain.cancel('authority');
        scalars.squelch.cancel('authority');
        pair.attachRenderer().dispose();
        scalars.rfGain.attachRenderer().dispose();
        scalars.squelch.attachRenderer().dispose();
        issuedStatus = { rfGain: null, squelch: null };
        rendererEpoch += 1;
      }
      lastAuthority = nextAuthority;
      published = next;
    });
  });
  onDestroy(() => {
    try { stop?.(); } finally {
      pair.destroy();
      scalars.rfGain.destroy();
      scalars.squelch.destroy();
      preampSeat.destroy();
      attenuatorSeat.destroy();
      digiSelSeat.destroy();
      ipPlusSeat.destroy();
    }
  });
</script>

{#snippet rfSql(hardware = false)}
  {@const view = pairView}
  <div
    class="rf-front-end-level" data-testid="rf-front-end-rf-sql"
    data-feedback-integration={feedbackIntegration()}
    data-observed={view.lanes.rf.availability === 'available'
      && view.lanes.sql.availability === 'available'
      && view.canonical.rf !== null && view.canonical.sql !== null}
    data-rf-command-phase={view.lanes.rf.phase ?? undefined}
    data-sql-command-phase={view.lanes.sql.phase ?? undefined}
    aria-busy={view.busy}
  >
    <div class="rf-front-end-heading">
      <span class="rf-front-end-reading">RF <output data-testid="rf-front-end-rf-sql-rf-value">{valueText('rfGain', pairLaneValue(view, 'rf'))}</output></span>
      <span class="rf-front-end-reading">SQL <output data-testid="rf-front-end-rf-sql-sql-value">{valueText('squelch', pairLaneValue(view, 'sql'))}</output></span>
    </div>
    <div class="rf-front-end-slider">
      {#key rendererEpoch}
        <DualParamRenderer binding={pair} showValues={false}
          variant={hardware ? 'hardware-illuminated' : 'modern'}
          issuedStatusPresentation={pairIssuedStatus} />
      {/key}
    </div>
    {#each PAIR_LANES as lane (lane)}
      {@const currentStatus = pairStatus(view, lane)}
      {#if currentStatus !== ''}
        <output data-testid={`rf-front-end-rf-sql-${lane}-status`}>{currentStatus}</output>
      {/if}
    {/each}
  </div>
{/snippet}

{#snippet scalar(field: RfFrontEndLevelField, hardware: boolean)}
  {@const current = rf?.[field]}
  {#if current?.availability.structural}
    {@const binding = scalars[field]}
    {@const view = scalarViews[field]}
    {@const [, label] = row(field)}
    {@const currentStatus = status(view)}
    <div
      class="rf-front-end-level" data-testid={`rf-front-end-${field}`}
      data-feedback-integration={feedbackIntegration()}
      data-observed={view.canonical !== null}
      data-command-phase={view.phase ?? undefined}
      aria-busy={view.busy}
    >
      <div class="rf-front-end-heading">
        <span class="rf-front-end-reading">{label} <output>{valueText(field, scalarValue(view))}</output></span>
      </div>
      <div class="rf-front-end-slider">
        {#key rendererEpoch}
          <ValueControl
            {...feedbackIntegratedControl}
            binding={binding} {label} renderer="hbar" showLabel={false} showValue={false} compact={true}
            variant={hardware ? 'hardware-illuminated' : 'modern'}
            accentColor={hardware ? 'var(--v2-accent-cyan-alt)' : 'var(--v2-accent-cyan)'}
            displayFn={(value) => valueText(field, value)} issuedStatusPresentation={scalarIssuedStatuses[field]}
          />
        {/key}
      </div>
      {#if currentStatus !== ''}
        <output data-testid={`rf-front-end-${field}-status`}>{currentStatus}</output>
      {/if}
    </div>
  {/if}
{/snippet}

{#snippet rfGain(hardware = false)}{@render scalar('rfGain', hardware)}{/snippet}
{#snippet squelch(hardware = false)}{@render scalar('squelch', hardware)}{/snippet}

{#snippet preamp()}
  {#if rf?.preamp.availability.structural}
    {#if finiteAppearance}
      {#key rendererContext}{#key finiteAppearance.choice}<ControlInstrumentRendererHost
        seat={preampSeat} renderer={finiteAppearance.choice}
      />{/key}{/key}
    {:else}
      <div
        class="rf-front-end-row" role="radiogroup" aria-label="Preamp"
        data-testid="rf-front-end-preamp"
        data-observed={usable(rf.preamp)}
        data-disabled-reason={preampDisabledReason()?.code}
        data-preamp-status={pendingPreamp !== null ? 'pending' : 'confirmed'}
        aria-describedby={[
          preampDisabledReason() === undefined ? null : preampMutexId,
          pendingPreamp === null ? null : pendingPreampId,
        ].filter(Boolean).join(' ') || undefined}
      >
        <span class="rf-front-end-row-label">PRE</span>
        <div class="rf-front-end-choices">
          {#each rf.preValues as value (value)}
            <button
              type="button" role="radio" class="rf-front-end-choice"
              data-testid={`rf-front-end-preamp-${value}`}
              aria-checked={preampBehavior.isSelected(value)}
              data-pending={pendingPreamp === value}
              disabled={!preampBehavior.available}
              onclick={() => preampBehavior.invoke(value)}
            >{preampChoiceText(value)}</button>
          {/each}
        </div>
        {#if preampDisabledReason()?.code !== 'receiver-lacks-control'}
          {#if rf.preamp.reading.status === 'known' && !rf.preValues.includes(rf.preamp.reading.value)}
            <!-- MOR-2527: a KNOWN level the model does not list shows its
                 true code in the visible slot — never a fabricated choice
                 name, never a `?`. -->
            <output class="rf-front-end-unknown" aria-label="PRE value" data-testid="rf-front-end-preamp-value">{preampChoiceText(rf.preamp.reading.value)}</output>
          {:else}
            <!-- MOR-2527: an unread reading renders the SAME sr-only output
                 a known reading renders, EMPTY — no value text and no extra
                 grid row, so the row's element set, row count and height are
                 identical unread vs known. -->
            <output class="sr-only" aria-label="PRE value" data-testid="rf-front-end-preamp-value">{rf.preamp.reading.status === 'known' ? preampChoiceText(rf.preamp.reading.value) : ''}</output>
          {/if}
        {/if}
        {#if preampDisabledReason()}
          <p id={preampMutexId} data-testid="rf-front-end-preamp-mutex-reason">{reasonLabel(preampDisabledReason()!.code)}</p>
        {/if}
        {#if pendingPreamp !== null}
          <span id={pendingPreampId} class="sr-only">{t('core.rfFrontEnd.preamp.pendingAnnouncement')}</span>
        {/if}
      </div>
    {/if}
  {/if}
{/snippet}

{#snippet attenuator(compact = false)}
  {#if rf?.attenuator.availability.structural}
    {#if finiteAppearance}
      {#key rendererContext}{#key finiteAppearance.choice}<ControlInstrumentRendererHost
        seat={attenuatorSeat} renderer={finiteAppearance.choice}
      />{/key}{/key}
    {:else}
      <div
        class="rf-front-end-row" role={compact ? undefined : 'radiogroup'} aria-label="Attenuator"
        data-testid="rf-front-end-attenuator"
        data-observed={usable(rf.attenuator)}
        data-disabled-reason={attenuatorDisabledReason()?.code}
        aria-describedby={attenuatorDisabledReason() === undefined ? undefined : attenuatorMutexId}
      >
        <span class="rf-front-end-row-label">ATT</span>
        {#if compact}
          <fieldset class="rf-front-end-att-control" disabled={!attenuatorBehavior.available || attenuatorDisabledReason() !== undefined}>
            <AttenuatorControl
              values={[...rf.attValues]}
              selected={rf.attenuator.reading.status === 'known' ? rf.attenuator.reading.value : Number.NaN}
              onchange={(value) => attenuatorBehavior.invoke(value)}
              testIdPrefix="rf-front-end-attenuator"
              ariaLabel="Attenuator"
            />
          </fieldset>
        {:else}
          <div class="rf-front-end-choices">
            {#each rf.attValues as value (value)}
              <button
                type="button" role="radio" class="rf-front-end-choice"
                data-testid={`rf-front-end-attenuator-${value}`}
                aria-checked={attenuatorBehavior.isSelected(value)}
                disabled={!attenuatorBehavior.available || attenuatorDisabledReason() !== undefined}
                onclick={() => attenuatorBehavior.invoke(value)}
              >{attenuatorChoiceText(value)}</button>
            {/each}
          </div>
        {/if}
        {#if attenuatorDisabledReason()?.code !== 'receiver-lacks-control'}
          {#if rf.attenuator.reading.status === 'known' && !rf.attValues.includes(rf.attenuator.reading.value)}
            <!-- MOR-2527: a KNOWN level the model does not list shows its
                 true code in the visible slot — never a fabricated choice
                 name, never a `?`. -->
            <output class="rf-front-end-unknown" aria-label="ATT value" data-testid="rf-front-end-attenuator-value">{attenuatorChoiceText(rf.attenuator.reading.value)}</output>
          {:else}
            <!-- MOR-2527: an unread reading renders the SAME sr-only output
                 a known reading renders, EMPTY — no value text and no extra
                 grid row, so the row's element set, row count and height are
                 identical unread vs known. -->
            <output class="sr-only" aria-label="ATT value" data-testid="rf-front-end-attenuator-value">{rf.attenuator.reading.status === 'known' ? attenuatorChoiceText(rf.attenuator.reading.value) : ''}</output>
          {/if}
        {/if}
        {#if attenuatorDisabledReason()}
          <p id={attenuatorMutexId} data-testid="rf-front-end-attenuator-mutex-reason">{reasonLabel(attenuatorDisabledReason()!.code)}</p>
        {/if}
      </div>
    {/if}
  {/if}
{/snippet}

{#snippet toggleControl(field: RfFrontEndToggleField, label: string)}
  {@const current = rf?.[field]}
  {#if current?.availability.structural}
    {@const behavior = field === 'digiSel' ? digiSelBehavior : ipPlusBehavior}
    {@const seat = field === 'digiSel' ? digiSelSeat : ipPlusSeat}
    {#if finiteAppearance}
      {#key rendererContext}{#key finiteAppearance.toggle}<ControlInstrumentRendererHost
        seat={seat} renderer={finiteAppearance.toggle}
      />{/key}{/key}
    {:else}
      <!-- MOR-2527 owner rule: a toggle is a KEY, not a `NAME: value` row.
           The label only, lit by `aria-pressed`: an unread reading draws the
           unlit label with no `: ?` text and no `aria-pressed` at all, so
           nothing claims ON or OFF about a reading the radio never
           reported. -->
      <button
        type="button" class="rf-front-end-toggle"
        data-testid={`rf-front-end-${field}`} data-observed={usable(current)}
        aria-pressed={behavior.confirmed}
        disabled={!behavior.available}
        onclick={() => behavior.invoke()}
      >{label}</button>
    {/if}
  {/if}
{/snippet}
{#snippet digiSel()}{@render toggleControl('digiSel', RF_FRONT_END_TOGGLES[0][1])}{/snippet}
{#snippet ipPlus()}{@render toggleControl('ipPlus', RF_FRONT_END_TOGGLES[1][1])}{/snippet}

{@render children(currentForm === 'combined'
  ? { kind: 'combined', rfSql, preamp, attenuator, digiSel, ipPlus }
  : { kind: 'separate', rfGain, squelch, preamp, attenuator, digiSel, ipPlus })}

{#each RF_FRONT_END_LEVELS as [field] (field)}
  {#if issuedStatus[field] !== null}
    {#key issuedStatusKey[field]}
      <span class="sr-only" role="status" aria-live="polite" aria-atomic="true"
        data-control-feedback-status data-feedback-lane={laneFor(field)}>{issuedStatus[field]}</span>
    {/key}
  {/if}
{/each}

<style>
  .rf-front-end-level { display: grid; grid-template-columns: minmax(0, 1fr); gap: 0.25rem; min-width: 0; }
  .rf-front-end-heading { grid-column: 1 / -1; display: flex; justify-content: space-between; gap: 1rem; min-width: 0; }
  .rf-front-end-reading { color: var(--v2-text-dim); font-size: 9px; text-transform: uppercase; }
  /* MOR-2527: the value box stays reserved — `min-width: 4ch` covers the
     widest rendered value (`100%`), and `inline-block` is what makes the
     min-width apply to the inline <output>, so the heading's other label
     cannot shift when a value arrives. */
  .rf-front-end-reading output { display: inline-block; min-width: 4ch; color: var(--v2-text-primary); font-size: 10px; }
  .rf-front-end-slider { grid-column: 1 / -1; width: 100%; min-width: 0; }
  .rf-front-end-level :global(.vc-hbar),
  .rf-front-end-level :global(.vc-dual) { width: 100%; min-width: 0; }
  :global(.rf-front-end-row) { display: grid; grid-template-columns: 34px minmax(0, 1fr); align-items: center; gap: 0.5rem; margin: 0; min-width: 0; }
  :global(.rf-front-end-row-label) { color: var(--v2-text-dim); font-size: 11px; font-weight: 700; letter-spacing: 0.06em; }
  .rf-front-end-choices { display: flex; flex-wrap: wrap; gap: 4px; min-width: 0; }
  .rf-front-end-choices > button { flex: 1 1 4.5ch; }
  .rf-front-end-att-control { min-width: 0; margin: 0; padding: 0; border: 0; }
  .rf-front-end-unknown { grid-column: 2; color: var(--v2-text-primary); }
  .rf-front-end-choice[aria-checked='true'] { font-weight: 700; }
  /* MOR-2527: the toggle key's light — the same structural (forced-colors
     safe) weight the preamp choices use, on the state attribute itself. */
  .rf-front-end-toggle[aria-pressed='true'] { font-weight: 700; }
  [data-observed='false'] { font-style: italic; }
  button:disabled { cursor: not-allowed; }
  /* MOR-1441 leg 2 — same pending doctrine as `FilterSurface`'s
     `.filter-choice[data-pending='true']`: structural marker, never
     color-only. */
  .rf-front-end-choice[data-pending='true'] { font-style: italic; opacity: 0.75; }
  .sr-only {
    position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
    overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0;
  }
</style>
