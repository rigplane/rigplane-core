<script lang="ts">
  import { onDestroy, type Snippet } from 'svelte';
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
    type TxAuxScalarHandles,
  } from './tx-aux-scalar';

  interface Props {
    view: RadioViewModel | null;
    levelFeedback?: TxAuxLevelFeedback;
    onLevelChange?: (field: TxAuxLevelField, value: number) => void;
    children: Snippet<[TxAuxScalarHandles]>;
  }

  let { view, levelFeedback, onLevelChange, children }: Props = $props();
  let txAux = $derived(view?.txAux);

  const LEVEL_COMMAND: Readonly<Record<TxAuxFeedbackLevelField, string>> = {
    micGain: 'set_mic_gain', driveGain: 'set_drive_gain', voxGain: 'set_vox_gain',
    antiVoxGain: 'set_anti_vox_gain', voxDelay: 'set_vox_delay',
    compressorLevel: 'set_compressor_level', monitorLevel: 'set_monitor_gain',
  };
  const feedbackIntegratedControl = { 'feedback-policy': 'feedback-integrated' } as const;
  const hostId = $props.id();

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

{#snippet scalar(field: TxAuxLevelField)}
  {@const current = txAux?.[field]}
  {#if current?.availability.structural}
    {@const [, label, min, max, step] = row(field)}
    {@const currentStatus = field === 'rfPower' ? '' : status(field)}
    {@const currentFeedback = field === 'rfPower' ? undefined : levelFeedback?.[field]}
    {@const disabledReason = reason(field)}
    {@const reasonId = disabledReason === undefined ? undefined : `${hostId}-${field}-reason`}
    <div
      class="tx-aux-level"
      data-testid={`tx-aux-${field}`}
      data-field={field}
      data-disabled-reason={disabledReason === undefined ? undefined : 'field-not-observed'}
      data-feedback-control={currentFeedback?.scope.control}
      data-min={min}
      data-max={max}
      data-step={step}
      aria-busy={currentFeedback?.busy}
      aria-describedby={reasonId}
      title={disabledReason}
    >
      <span class="tx-aux-name">{label}</span>
      <ValueControl
        {...field !== 'rfPower' ? feedbackIntegratedControl : {}}
        binding={bindings[field]}
        {label}
        renderer="hbar"
        displayFn={(value) => formatValue(field, value)}
        showLabel={false}
        showValue={true}
        compact={true}
        title={disabledReason}
        feedbackDescription={field === 'rfPower' ? disabledReason ?? null : undefined}
        issuedStatusPresentation={field === 'rfPower'
          ? undefined
          : statusPresentations[field as TxAuxFeedbackLevelField]}
      />
      {#if disabledReason !== undefined}
        <span id={reasonId} class="sr-only">{disabledReason}</span>
      {/if}
      <output data-canonical-value>{formatValue(field, canonical(field))}</output>
      {#if currentStatus !== ''}
        <span
          data-command-status
          class:command-pending={currentFeedback?.busy}
          class:sr-only={currentFeedback?.phase === 'unavailable'}
        >{currentStatus}</span>
      {/if}
      {#if field !== 'rfPower' && issuedStatus[field] !== null}
        {#key issuedStatusKey[field]}
          <span
            class="sr-only" role="status" aria-live="polite" aria-atomic="true"
            data-control-feedback-status data-feedback-lane={field}
          >{issuedStatus[field]}</span>
        {/key}
      {/if}
    </div>
  {/if}
{/snippet}

{#snippet rfPower()}{@render scalar('rfPower')}{/snippet}
{#snippet micGain()}{@render scalar('micGain')}{/snippet}
{#snippet driveGain()}{@render scalar('driveGain')}{/snippet}
{#snippet voxGain()}{@render scalar('voxGain')}{/snippet}
{#snippet antiVoxGain()}{@render scalar('antiVoxGain')}{/snippet}
{#snippet voxDelay()}{@render scalar('voxDelay')}{/snippet}
{#snippet compressorLevel()}{@render scalar('compressorLevel')}{/snippet}
{#snippet monitorLevel()}{@render scalar('monitorLevel')}{/snippet}

{@render children({
  rfPower, micGain, driveGain, voxGain, antiVoxGain, voxDelay, compressorLevel, monitorLevel,
})}

<style>
  .tx-aux-level { display: flex; align-items: baseline; gap: 0.5rem; }
  .tx-aux-name { min-width: 8ch; }
  .sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
</style>
