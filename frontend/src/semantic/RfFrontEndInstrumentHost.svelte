<script lang="ts">
  import { onDestroy, onMount, untrack, type Snippet } from 'svelte';
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
    RF_FRONT_END_LEVELS,
    type RfFrontEndAuthorityPublication,
    type RfFrontEndInstrumentPresentation,
    type RfFrontEndLevelField,
    type RfFrontEndLevelHandles,
    type RfSqlControlModel,
    type SubscribeRfFrontEndAuthority,
  } from './rf-front-end-instruments';

  interface Props {
    presentation: RfFrontEndInstrumentPresentation;
    subscribeControlAuthority: SubscribeRfFrontEndAuthority;
    onLevelChange?: (field: RfFrontEndLevelField, value: number) => void;
    children: Snippet<[RfFrontEndLevelHandles]>;
  }

  type Receiver = 'MAIN' | 'SUB';
  interface RfAuthority {
    readonly epoch: number;
    readonly generation: number;
    readonly topologyId: string;
    readonly receiver: Receiver;
    readonly form: RfSqlControlModel;
  }

  let { presentation, subscribeControlAuthority, onLevelChange, children }: Props = $props();
  let published = $state.raw<RfFrontEndAuthorityPublication | null>(null);
  let lastAuthority: RfAuthority | null | undefined;
  let stop: (() => void) | null = null;
  let rf = $derived(presentation.view?.rfFrontEnd);

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
  const authorityCurrent = (): boolean => same(currentAuthority(), presentedAuthority());

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
    <span class="rf-front-end-name">RF/SQL</span>
    {#key rendererEpoch}
      <DualParamRenderer binding={pair} showValues={false}
        issuedStatusPresentation={pairIssuedStatus} />
    {/key}
    <output data-testid="rf-front-end-rf-sql-rf-value">{valueText(pairLaneValue(view, 'rf'))}</output>
    /
    <output data-testid="rf-front-end-rf-sql-sql-value">{valueText(pairLaneValue(view, 'sql'))}</output>
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
      <span class="rf-front-end-name">{label}</span>
      {#key rendererEpoch}
        <ValueControl
          {...feedbackIntegratedControl}
          binding={binding} {label} renderer="hbar" showLabel={false} showValue={false} compact={true}
          displayFn={valueText} issuedStatusPresentation={scalarIssuedStatuses[field]}
        />
      {/key}
      <output>{valueText(scalarValue(view))}</output>
      {#if currentStatus !== ''}
        <output data-testid={`rf-front-end-${field}-status`}>{currentStatus}</output>
      {/if}
    </div>
  {/if}
{/snippet}

{#snippet rfGain()}{@render scalar('rfGain')}{/snippet}
{#snippet squelch()}{@render scalar('squelch')}{/snippet}

{@render children(currentForm === 'combined'
  ? { kind: 'combined', rfSql }
  : { kind: 'separate', rfGain, squelch })}

{#each RF_FRONT_END_LEVELS as [field] (field)}
  {#if issuedStatus[field] !== null}
    {#key issuedStatusKey[field]}
      <span class="sr-only" role="status" aria-live="polite" aria-atomic="true"
        data-control-feedback-status data-feedback-lane={laneFor(field)}>{issuedStatus[field]}</span>
    {/key}
  {/if}
{/each}

<style>
  .rf-front-end-level { display: flex; align-items: baseline; gap: 0.5rem; }
  .rf-front-end-name { min-width: 6ch; }
  .rf-front-end-level :global(.vc-hbar),
  .rf-front-end-level :global(.vc-dual) { width: 100%; min-width: 0; }
  .sr-only {
    position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
    overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0;
  }
</style>
