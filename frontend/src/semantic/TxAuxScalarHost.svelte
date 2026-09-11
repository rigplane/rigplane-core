<script lang="ts">
  import { onDestroy, type Snippet } from 'svelte';
  import type { ScalarAppearance } from '../../component-kit-api/src/index';
  import { ValueControl } from '../components-v2/controls/value-control';
  import type {
    HBarIssuedStatusPresentation,
    HBarIssuedStatusSnapshot,
  } from '../components-v2/controls/value-control/skin';
  import {
    createContinuousScalar,
    createHBarContinuousScalarPolicy,
    type ContinuousScalarInput,
    type ContinuousScalarPolicy,
    type ScalarDomain,
  } from '../primitives/scalar/continuous-scalar.svelte';
  import { rawToPercentDisplay } from '../primitives/scalar/value-control-core';
  import { disabledReasonText } from './disabled-reason';
  import type { RadioViewModel, TxAuxField } from './radio-view-model';
  import {
    TX_AUX_FEEDBACK_LEVELS,
    TX_AUX_LEVELS,
    type TxAuxFeedbackLevelField,
    type TxAuxLevelFeedback,
    type TxAuxLevelField,
    type TxAuxScalarPresentation,
    type TxAuxScalarHandles,
  } from './tx-aux-scalar';

  interface Props {
    view: RadioViewModel | null;
    levelFeedback?: TxAuxLevelFeedback;
    onLevelChange?: (field: TxAuxLevelField, value: number) => void;
    scalarAppearance?: ScalarAppearance;
    presentationIsCurrent?: () => boolean;
    children: Snippet<[TxAuxScalarHandles]>;
  }

  let {
    view, levelFeedback, onLevelChange, scalarAppearance, presentationIsCurrent, children,
  }: Props = $props();
  let txAux = $derived(view?.txAux);

  const LEVEL_COMMAND: Readonly<Record<TxAuxFeedbackLevelField, string>> = {
    micGain: 'set_mic_gain', driveGain: 'set_drive_gain', voxGain: 'set_vox_gain',
    antiVoxGain: 'set_anti_vox_gain', voxDelay: 'set_vox_delay',
    compressorLevel: 'set_compressor_level', monitorLevel: 'set_monitor_gain',
  };
  const feedbackIntegratedControl = { 'feedback-policy': 'feedback-integrated' } as const;
  const row = (field: TxAuxLevelField) => TX_AUX_LEVELS.find(([candidate]) => candidate === field)!;
  const usable = (field: TxAuxField<unknown>): boolean => field.availability.structural
    && field.availability.operational && field.reading.status === 'known';
  const reasonText = (field: TxAuxField<unknown>): string | undefined =>
    (!field.availability.structural
      ? disabledReasonText(field.availability)
      : usable(field) ? undefined : disabledReasonText({ structural: true, operational: false }));
  const formatValue = (field: TxAuxLevelField, value: number | null): string => {
    if (value === null || !Number.isFinite(value)) return '?';
    const [, , min, max, , format] = row(field);
    return (format ?? ((raw: number) => rawToPercentDisplay(raw, min, max)))(value);
  };

  function request(field: TxAuxLevelField, value: number): void {
    const current = txAux?.[field];
    if (current !== undefined && usable(current)) onLevelChange?.(field, value);
  }

  function input(field: TxAuxLevelField): Readonly<ContinuousScalarInput> {
    const current = txAux?.[field];
    const [, , min, max, step] = row(field);
    const common = {
      domain: { min, max, step, defaultValue: null, fineStepDivisor: 1 },
      enabled: current !== undefined && usable(current),
      request: (value: number) => request(field, value),
    } as const;
    if (field !== 'rfPower' && levelFeedback !== undefined) return {
      ...common,
      evidence: 'command-feedback',
      feedback: levelFeedback[field],
      command: LEVEL_COMMAND[field],
    };
    return {
      ...common,
      evidence: 'reading',
      ownerKey: `tx-aux-${field}-reading`,
      reading: current?.reading.status === 'known'
        ? { status: 'known', value: current.reading.value }
        : { status: 'unknown' },
    };
  }

  function policy(field: TxAuxLevelField): Readonly<ContinuousScalarPolicy> {
    const base = createHBarContinuousScalarPolicy({
      preview: 'optimistic', debounceMs: 0,
      describeTarget: (value) => formatValue(field, value),
    });
    return Object.freeze({
      ...base,
      reset: (domain: ScalarDomain) => domain.defaultValue,
    });
  }

  const bindings = Object.fromEntries(TX_AUX_LEVELS.map(([field]) => [
    field, createContinuousScalar(() => input(field), policy(field)),
  ])) as Record<TxAuxLevelField, ReturnType<typeof createContinuousScalar>>;

  const feedbackRecord = <T,>(value: T) => Object.fromEntries(
    TX_AUX_FEEDBACK_LEVELS.map((field) => [field, value]),
  ) as Record<TxAuxFeedbackLevelField, T>;
  let issuedStatus = $state(feedbackRecord<string | null>(null));
  let issuedStatusKey = $state(feedbackRecord(''));
  function statusPresentation(field: TxAuxFeedbackLevelField): Readonly<HBarIssuedStatusPresentation> {
    return {
      get text() { return null; },
      format({ view: scalarView, announcement }: Readonly<HBarIssuedStatusSnapshot>) {
        issuedStatusKey[field] = JSON.stringify([
          scalarView.feedback.providerGeneration ?? null,
          scalarView.feedback.sessionEpoch,
          scalarView.feedback.scope.control,
          scalarView.feedback.scope.receiver,
          scalarView.feedback.scope.slot ?? null,
          announcement.transitionId,
        ]);
        return scalarView.error === null
          ? announcement.message
          : `${announcement.message.replace(/[.!?]$/, '')}: ${scalarView.error}`;
      },
      accept(text) { issuedStatus[field] = text; },
    };
  }
  const statusPresentations = Object.fromEntries(TX_AUX_FEEDBACK_LEVELS.map((field) => [
    field, statusPresentation(field),
  ])) as Record<TxAuxFeedbackLevelField, Readonly<HBarIssuedStatusPresentation>>;

  function retireHBarStatus(
    _node: HTMLElement,
    placement: Readonly<{ field: TxAuxLevelField; form: 'hbar' | 'knob' }>,
  ) {
    const retire = (next: typeof placement) => {
      if (next.form !== 'knob' || next.field === 'rfPower') return;
      issuedStatus[next.field] = null;
    };
    retire(placement);
    return { update: retire };
  }

  function canonical(field: TxAuxLevelField): number | null {
    if (field !== 'rfPower' && levelFeedback !== undefined) {
      const feedback = levelFeedback[field];
      return feedback.availability === 'available' ? feedback.confirmed : null;
    }
    const reading = txAux?.[field].reading;
    return reading?.status === 'known' ? reading.value : null;
  }
  function status(field: TxAuxFeedbackLevelField): string {
    if (levelFeedback === undefined) return '';
    const feedback = levelFeedback[field];
    if (feedback.phase === 'idle') return '';
    const [, label] = row(field);
    const target = feedback.target ?? feedback.requestedTarget;
    const requested = target === null ? '' : `; requested ${formatValue(field, target)}`;
    const confirmed = `; confirmed ${formatValue(field, feedback.confirmed)}`;
    const error = feedback.outcome?.error === undefined ? '' : `; ${feedback.outcome.error}`;
    return `${label}: ${feedback.phase.replaceAll('-', ' ')}${requested}${confirmed}${error}`;
  }
  function reason(field: TxAuxLevelField): string | undefined {
    const current = txAux?.[field];
    if (current === undefined) return 'Not yet observed';
    if (field !== 'rfPower' && levelFeedback?.[field].availability === 'unavailable') {
      return reasonText(current) ?? 'Not yet observed';
    }
    return reasonText(current);
  }

  onDestroy(() => {
    for (const [, binding] of Object.entries(bindings)) binding.destroy();
  });
</script>

{#snippet scalar(
  field: TxAuxLevelField,
  presentation?: Readonly<TxAuxScalarPresentation>,
)}
  {@const current = txAux?.[field]}
  {#if current?.availability.structural}
    {@const explicitPresentation = presentation !== undefined}
    {@const form = presentation?.form ?? 'hbar'}
    {@const [, label, min, max, step] = row(field)}
    {@const currentStatus = field === 'rfPower' ? '' : status(field)}
    {@const currentFeedback = field === 'rfPower' ? undefined : levelFeedback?.[field]}
    {@const disabledReason = reason(field)}
    {@const accessibility = {
      description: disabledReason ?? null,
      valueText: `${label}: ${formatValue(field, canonical(field))}${currentStatus === '' ? '' : `; ${currentStatus}`}`,
    }}
    <div
      class="tx-aux-level"
      class:tx-aux-level--presented={explicitPresentation}
      data-testid={`tx-aux-${field}`}
      data-field={field}
      data-scalar-form={form}
      data-disabled-reason={disabledReason === undefined ? undefined : 'field-not-observed'}
      data-feedback-control={currentFeedback?.scope.control}
      data-min={min}
      data-max={max}
      data-step={step}
      aria-busy={currentFeedback?.busy}
      title={disabledReason}
      use:retireHBarStatus={{ field, form }}
    >
      <span class="tx-aux-name" class:sr-only={explicitPresentation}
        aria-hidden={explicitPresentation ? 'true' : undefined}>{label}</span>
      {#if field === 'rfPower'}
        <ValueControl
          binding={bindings[field]} label="RF Power" renderer={form}
          displayFn={(value) => formatValue(field, value)}
          showLabel={explicitPresentation ? presentation?.showLabel ?? true : false}
          showValue={explicitPresentation ? presentation?.showValue ?? true : false}
          compact={explicitPresentation ? presentation?.compact ?? false : true}
          title={disabledReason}
          {accessibility}
          skin={scalarAppearance}
          {presentationIsCurrent}
        />
      {:else}
        <ValueControl
          {...feedbackIntegratedControl}
          binding={bindings[field]} {label} renderer={form}
          displayFn={(value) => formatValue(field, value)}
          showLabel={explicitPresentation ? presentation?.showLabel ?? true : false}
          showValue={explicitPresentation ? presentation?.showValue ?? true : false}
          compact={explicitPresentation ? presentation?.compact ?? false : true}
          title={disabledReason}
          {accessibility}
          skin={scalarAppearance}
          {presentationIsCurrent}
          issuedStatusPresentation={form === 'hbar'
            ? statusPresentations[field as TxAuxFeedbackLevelField]
            : undefined}
        />
      {/if}
      <output data-canonical-value class:sr-only={explicitPresentation}
        aria-hidden={explicitPresentation ? 'true' : undefined}
      >{formatValue(field, canonical(field))}</output>
      {#if currentStatus !== ''}
        <span
          data-command-status
          class:command-pending={currentFeedback?.busy}
          class:sr-only={explicitPresentation || currentFeedback?.phase === 'unavailable'}
        >{currentStatus}</span>
      {/if}
    </div>
  {/if}
{/snippet}

{#snippet rfPower(presentation?: Readonly<TxAuxScalarPresentation>)}
  {@render scalar('rfPower', presentation)}
{/snippet}
{#snippet micGain(presentation?: Readonly<TxAuxScalarPresentation>)}
  {@render scalar('micGain', presentation)}
{/snippet}
{#snippet driveGain(presentation?: Readonly<TxAuxScalarPresentation>)}
  {@render scalar('driveGain', presentation)}
{/snippet}
{#snippet voxGain(presentation?: Readonly<TxAuxScalarPresentation>)}
  {@render scalar('voxGain', presentation)}
{/snippet}
{#snippet antiVoxGain(presentation?: Readonly<TxAuxScalarPresentation>)}
  {@render scalar('antiVoxGain', presentation)}
{/snippet}
{#snippet voxDelay(presentation?: Readonly<TxAuxScalarPresentation>)}
  {@render scalar('voxDelay', presentation)}
{/snippet}
{#snippet compressorLevel(presentation?: Readonly<TxAuxScalarPresentation>)}
  {@render scalar('compressorLevel', presentation)}
{/snippet}
{#snippet monitorLevel(presentation?: Readonly<TxAuxScalarPresentation>)}
  {@render scalar('monitorLevel', presentation)}
{/snippet}

{@render children({
  rfPower, micGain, driveGain, voxGain, antiVoxGain, voxDelay, compressorLevel, monitorLevel,
})}

{#each TX_AUX_FEEDBACK_LEVELS as field (field)}
  {#if issuedStatus[field] !== null}
    {#key issuedStatusKey[field]}
      <span class="sr-only" role="status" aria-live="polite" aria-atomic="true"
        data-control-feedback-status data-feedback-lane={field}>{issuedStatus[field]}</span>
    {/key}
  {/if}
{/each}

<style>
  .tx-aux-level { display: grid; grid-template-columns: 10ch 8rem auto; align-items: center; gap: 0.5rem; }
  .tx-aux-level--presented { display: flex; width: 100%; min-width: 0; max-width: 100%; }
  .tx-aux-name { white-space: nowrap; }
  .tx-aux-level :global(.vc-hbar) { width: 100%; min-width: 0; }
  .sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
</style>
