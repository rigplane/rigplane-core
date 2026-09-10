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
  import type { RadioViewModel, RfFrontEndField } from './radio-view-model';
  import {
    DISABLED_REASON_LABEL,
    RF_FRONT_END_LEVELS,
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
  const pendingPreampId = $props.id();

  const safeGeneration = (value: unknown): value is number =>
    typeof value === 'number' && Number.isSafeInteger(value) && value >= 0;
  const usable = (field: RfFrontEndField<unknown> | undefined): boolean =>
    field?.availability.structural === true
    && field.availability.operational
    && field.reading.status === 'known';
  const finiteText = (field: RfFrontEndField<unknown> | undefined): string =>
    field?.reading.status === 'known' ? String(field.reading.value) : '?';
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
    const view = toRadioViewModel(source.state, source.caps);
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
  const domain = {
    min: RF_FRONT_END_LEVELS[0][2], max: RF_FRONT_END_LEVELS[0][3],
    step: RF_FRONT_END_LEVELS[0][4], defaultValue: null, fineStepDivisor: 10,
  } as const;
  const feedbackIntegratedControl = { 'feedback-policy': 'feedback-integrated' } as const;
  const currentAuthority = (): RfAuthority | null => published === null ? null : authority(published);
  const presentedAuthority = (): RfAuthority | null => authority(
    presentation, presentation.controlModel,
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
      domain,
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
      ...common, evidence: 'command-feedback', rf: feedback.rf, sql: feedback.sql,
      enabled: authorityCurrent() && currentForm === 'combined' && onLevelChange !== undefined,
    };
    const availability = (field: RfFrontEndField<number> | undefined) =>
      field?.availability.structural && field.availability.operational
        ? 'available' as const : 'unavailable' as const;
    return {
      ...common, evidence: 'reading', ownerKey: authorityKey(currentAuthority()),
      enabled: authorityCurrent() && currentForm === 'combined' && onLevelChange !== undefined,
      rf: {
        reading: rf?.rfGain.reading ?? { status: 'unknown' },
        availability: availability(rf?.rfGain),
      },
      sql: {
        reading: rf?.squelch.reading ?? { status: 'unknown' },
        availability: availability(rf?.squelch),
      },
    };
  }
  function scalarInput(field: RfFrontEndLevelField): Readonly<ContinuousScalarInput> {
    const common = { domain, request: (value: number) => request(field, value) };
    const feedback = presentation.rfSqlFeedback;
    if (feedback === null) return {
      ...common, evidence: 'reading', enabled: false,
      ownerKey: authorityKey(currentAuthority(), field), reading: { status: 'unknown' },
    };
    if (feedback !== undefined) {
      const lane = feedback[laneFor(field)];
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
      reading: fact?.reading ?? { status: 'unknown' },
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
  const valueText = (value: number | null): string => value === null
    ? '?' : formatKnownLevel(value, domain.min, domain.max);
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
    if (view.evidence !== 'command-feedback' || view.phase === 'idle') return '';
    const target = view.feedback.target ?? view.feedback.requestedTarget;
    return `${view.phase.replaceAll('-', ' ')}${target === null ? '' : `; requested ${valueText(target)}`}`
      + `; confirmed ${valueText(view.canonical)}${view.error === null ? '' : `; ${view.error}`}`;
  }
  function pairStatus(view: Readonly<ContinuousPairView>, lane: DualParamLane): string {
    const laneView = view.lanes[lane];
    if (laneView.evidence !== 'command-feedback' || laneView.phase === 'idle') return '';
    const target = laneView.feedback.target ?? laneView.feedback.requestedTarget;
    return `${laneView.phase.replaceAll('-', ' ')}${target === null ? '' : `; requested ${valueText(target)}`}`
      + `; confirmed ${valueText(laneView.canonical)}`
      + `${laneView.error === null ? '' : `; ${laneView.error}`}`;
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

  const preampMutexReason = (): string | undefined =>
    preMutex === null ? undefined : DISABLED_REASON_LABEL[preMutex.code];
  const preampSeat = createChoiceRendererSeat<number>(() => ({
    context: rendererContext ?? null, field: rf?.preamp, label: 'Preamp', blocked: preMutex !== null,
    options: preampChoices().map((value) => ({
      value, label: preampChoiceText(value),
      ...(preampMutexReason() === undefined ? {} : { disabledReason: preampMutexReason() }),
    })),
    ...(pendingPreamp === null ? {} : {
      requested: { kind: 'requested-target' as const, target: pendingPreamp },
    }),
    invoke: (level) => onPreChange?.(level),
  }));
  const attenuatorSeat = createChoiceRendererSeat<number>(() => ({
    context: rendererContext ?? null, field: rf?.attenuator, label: 'Attenuator',
    options: attenuatorChoices().map((value) => ({ value, label: attenuatorChoiceText(value) })),
    invoke: (db) => onAttChange?.(db),
  }));
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

{#snippet rfSql()}
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
      <span class="rf-front-end-reading">RF <output data-testid="rf-front-end-rf-sql-rf-value">{valueText(pairLaneValue(view, 'rf'))}</output></span>
      <span class="rf-front-end-reading">SQL <output data-testid="rf-front-end-rf-sql-sql-value">{valueText(pairLaneValue(view, 'sql'))}</output></span>
    </div>
    <div class="rf-front-end-slider">
      {#key rendererEpoch}
        <DualParamRenderer binding={pair} showValues={false}
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

{#snippet scalar(field: RfFrontEndLevelField)}
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
        <span class="rf-front-end-reading">{label} <output>{valueText(scalarValue(view))}</output></span>
      </div>
      <div class="rf-front-end-slider">
        {#key rendererEpoch}
          <ValueControl
            {...feedbackIntegratedControl}
            binding={binding} {label} renderer="hbar" showLabel={false} showValue={false} compact={true}
            displayFn={valueText} issuedStatusPresentation={scalarIssuedStatuses[field]}
          />
        {/key}
      </div>
      {#if currentStatus !== ''}
        <output data-testid={`rf-front-end-${field}-status`}>{currentStatus}</output>
      {/if}
    </div>
  {/if}
{/snippet}

{#snippet rfGain()}{@render scalar('rfGain')}{/snippet}
{#snippet squelch()}{@render scalar('squelch')}{/snippet}

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
        data-disabled-reason={preMutex?.code}
        data-preamp-status={pendingPreamp !== null ? 'pending' : 'confirmed'}
        aria-describedby={pendingPreamp !== null ? pendingPreampId : undefined}
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
        {#if rf.preamp.reading.status === 'known' && rf.preValues.includes(rf.preamp.reading.value)}
          <output class="sr-only" aria-label="PRE value" data-testid="rf-front-end-preamp-value">{preampChoiceText(rf.preamp.reading.value)}</output>
        {:else}
          <output class="rf-front-end-unknown" aria-label="PRE value" data-testid="rf-front-end-preamp-value">{rf.preamp.reading.status === 'known' ? preampChoiceText(rf.preamp.reading.value) : '?'}</output>
        {/if}
        {#if preMutex}
          <p data-testid="rf-front-end-preamp-mutex-reason">{DISABLED_REASON_LABEL[preMutex.code]}</p>
        {/if}
        {#if pendingPreamp !== null}
          <span id={pendingPreampId} class="sr-only">{t('core.rfFrontEnd.preamp.pendingAnnouncement')}</span>
        {/if}
      </div>
    {/if}
  {/if}
{/snippet}

{#snippet attenuator()}
  {#if rf?.attenuator.availability.structural}
    {#if finiteAppearance}
      {#key rendererContext}{#key finiteAppearance.choice}<ControlInstrumentRendererHost
        seat={attenuatorSeat} renderer={finiteAppearance.choice}
      />{/key}{/key}
    {:else}
      <div
        class="rf-front-end-row" role="radiogroup" aria-label="Attenuator"
        data-testid="rf-front-end-attenuator" data-observed={usable(rf.attenuator)}
      >
        <span class="rf-front-end-row-label">ATT</span>
        <div class="rf-front-end-choices">
          {#each rf.attValues as value (value)}
            <button
              type="button" role="radio" class="rf-front-end-choice"
              data-testid={`rf-front-end-attenuator-${value}`}
              aria-checked={attenuatorBehavior.isSelected(value)}
              disabled={!attenuatorBehavior.available}
              onclick={() => attenuatorBehavior.invoke(value)}
            >{attenuatorChoiceText(value)}</button>
          {/each}
        </div>
        {#if rf.attenuator.reading.status === 'known' && rf.attValues.includes(rf.attenuator.reading.value)}
          <output class="sr-only" aria-label="ATT value" data-testid="rf-front-end-attenuator-value">{attenuatorChoiceText(rf.attenuator.reading.value)}</output>
        {:else}
          <output class="rf-front-end-unknown" aria-label="ATT value" data-testid="rf-front-end-attenuator-value">{rf.attenuator.reading.status === 'known' ? attenuatorChoiceText(rf.attenuator.reading.value) : '?'}</output>
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
      <button
        type="button" class="rf-front-end-toggle"
        data-testid={`rf-front-end-${field}`} data-observed={usable(current)}
        aria-pressed={behavior.confirmed}
        disabled={!behavior.available}
        onclick={() => behavior.invoke()}
      >{label}: {finiteText(current)}</button>
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
  .rf-front-end-reading output { color: var(--v2-text-primary); font-size: 10px; }
  .rf-front-end-slider { grid-column: 1 / -1; width: 100%; min-width: 0; }
  .rf-front-end-level :global(.vc-hbar),
  .rf-front-end-level :global(.vc-dual) { width: 100%; min-width: 0; }
  .rf-front-end-row { display: grid; grid-template-columns: 34px minmax(0, 1fr); align-items: center; gap: 0.5rem; margin: 0; min-width: 0; }
  .rf-front-end-row-label { color: var(--v2-text-dim); font-size: 11px; font-weight: 700; letter-spacing: 0.06em; }
  .rf-front-end-choices { display: flex; flex-wrap: wrap; gap: 4px; min-width: 0; }
  .rf-front-end-choices > button { flex: 1 1 4.5ch; }
  .rf-front-end-unknown { grid-column: 2; color: var(--v2-text-primary); }
  .rf-front-end-choice[aria-checked='true'] { font-weight: 700; }
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
