<script module lang="ts">
  import type { Snippet } from 'svelte';

  export const CW_KEYER_SPEED = [
    'keyerSpeed', 'Keyer speed', 6, 48, 1, 'WPM', 'set_key_speed',
  ] as const;
  export const CW_PITCH_HZ = [
    'pitchHz', 'CW pitch', 300, 900, 5, 'Hz', 'set_cw_pitch',
  ] as const;
  export const CW_CONTINUOUS_LEVELS = [CW_KEYER_SPEED, CW_PITCH_HZ] as const;
  export type CwContinuousField = typeof CW_CONTINUOUS_LEVELS[number][0];
  export type CwContinuousForm = 'hbar' | 'knob';

  export interface CwContinuousPresentation {
    readonly form?: CwContinuousForm;
    readonly compact?: boolean;
    readonly showLabel?: boolean;
    readonly showValue?: boolean;
  }

  export type CwContinuousHandle = Snippet<[
    presentation?: Readonly<CwContinuousPresentation>,
  ]>;
  export type CwKeyerInstrumentHandles = Readonly<Record<CwContinuousField, CwContinuousHandle>>;
</script>

<script lang="ts">
  import { onDestroy } from 'svelte';
  import type { ScalarAppearance } from '../../component-kit-api/src/index';
  import { ValueControl } from '../components-v2/controls/value-control';
  import type {
    HBarIssuedStatusPresentation,
    HBarIssuedStatusSnapshot,
  } from '../components-v2/controls/value-control/skin';
  import {
    createContinuousScalar,
    createRenderedNativeRangeContinuousScalarPolicy,
    type CommandScalarFeedback,
    type ContinuousScalarInput,
  } from '../primitives/scalar/continuous-scalar.svelte';
  import type { CwKeyerField, RadioViewModel } from './radio-view-model';

  interface Props {
    view: RadioViewModel | null;
    keySpeedFeedback?: Readonly<CommandScalarFeedback>;
    pitchFeedback?: Readonly<CommandScalarFeedback>;
    onLevelChange?: (field: CwContinuousField, value: number) => void;
    scalarAppearance?: ScalarAppearance;
    presentationIsCurrent?: () => boolean;
    children: Snippet<[CwKeyerInstrumentHandles]>;
  }

  let {
    view, keySpeedFeedback, pitchFeedback, onLevelChange,
    scalarAppearance, presentationIsCurrent, children,
  }: Props = $props();

  const feedbackIntegratedControl = { 'feedback-policy': 'feedback-integrated' } as const;
  const row = (field: CwContinuousField) => CW_CONTINUOUS_LEVELS.find(([f]) => f === field)!;
  const usable = (current: CwKeyerField<unknown> | undefined): boolean =>
    current?.availability.structural === true
    && current.availability.operational
    && current.reading.status === 'known';
  const formatValue = (field: CwContinuousField, value: number | null): string => {
    if (value === null || !Number.isFinite(value)) return '—';
    const [, , , , , unit] = row(field);
    return `${value} ${unit}`;
  };
  const fieldOf = (field: CwContinuousField): CwKeyerField<number> | undefined =>
    view?.cwKeyer?.[field];
  const feedbackOf = (field: CwContinuousField): Readonly<CommandScalarFeedback> | undefined =>
    field === 'keyerSpeed' ? keySpeedFeedback : pitchFeedback;

  function request(field: CwContinuousField, value: number): void {
    const current = fieldOf(field);
    if (current !== undefined && usable(current)) onLevelChange?.(field, value);
  }

  function input(field: CwContinuousField): Readonly<ContinuousScalarInput> {
    const [, , min, max, step, , command] = row(field);
    const current = fieldOf(field);
    const common = {
      domain: { min, max, step, defaultValue: null, fineStepDivisor: 1 },
      enabled: usable(current),
      request: (value: number) => request(field, value),
    } as const;
    const feedback = feedbackOf(field);
    if (feedback !== undefined) {
      return {
        ...common, evidence: 'command-feedback', feedback, command,
      };
    }
    return {
      ...common,
      evidence: 'reading',
      ownerKey: `cw-keyer-${field}-reading`,
      reading: current?.reading.status === 'known'
        ? { status: 'known', value: current.reading.value }
        : { status: 'unknown' },
    };
  }

  const policies = Object.fromEntries(CW_CONTINUOUS_LEVELS.map(([field]) => [
    field, createRenderedNativeRangeContinuousScalarPolicy(),
  ])) as Record<CwContinuousField, ReturnType<typeof createRenderedNativeRangeContinuousScalarPolicy>>;
  const bindings = Object.fromEntries(CW_CONTINUOUS_LEVELS.map(([field]) => [
    field, createContinuousScalar(() => input(field), policies[field]),
  ])) as Record<CwContinuousField, ReturnType<typeof createContinuousScalar>>;

  const record = <T,>(value: T) => Object.fromEntries(
    CW_CONTINUOUS_LEVELS.map(([field]) => [field, value]),
  ) as Record<CwContinuousField, T>;
  let issuedStatus = $state(record<string | null>(null));
  let issuedStatusKey = $state(record(''));
  function statusPresentation(field: CwContinuousField): Readonly<HBarIssuedStatusPresentation> {
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
  const statusPresentations = Object.fromEntries(CW_CONTINUOUS_LEVELS.map(([field]) => [
    field, statusPresentation(field),
  ])) as Record<CwContinuousField, Readonly<HBarIssuedStatusPresentation>>;

  function retireHBarStatus(
    _node: HTMLElement,
    placement: Readonly<{ field: CwContinuousField; form: CwContinuousForm }>,
  ) {
    const retire = (next: typeof placement) => {
      if (next.form === 'knob') issuedStatus[next.field] = null;
    };
    retire(placement);
    return { update: retire };
  }

  function canonical(field: CwContinuousField): number | null {
    const feedback = feedbackOf(field);
    if (feedback !== undefined) {
      return feedback.availability === 'available' ? feedback.confirmed : null;
    }
    const current = fieldOf(field);
    return current?.reading.status === 'known' ? current.reading.value : null;
  }

  function status(field: CwContinuousField): string {
    const feedback = feedbackOf(field);
    if (feedback === undefined || feedback.phase === 'idle') return '';
    const target = feedback.target ?? feedback.requestedTarget;
    const requested = target === null ? '' : `; requested ${formatValue(field, target)}`;
    const confirmed = `; confirmed ${formatValue(field, feedback.confirmed)}`;
    const error = feedback.outcome?.error === undefined ? '' : `; ${feedback.outcome.error}`;
    return `${feedback.phase.replaceAll('-', ' ')}${requested}${confirmed}${error}`;
  }

  onDestroy(() => {
    for (const [, binding] of Object.entries(bindings)) binding.destroy();
  });
</script>

{#snippet scalar(field: CwContinuousField, presentation?: Readonly<CwContinuousPresentation>)}
  {@const current = fieldOf(field)}
  {#if current?.availability.structural}
    {@const [, label, min, max, step] = row(field)}
    {@const explicitPresentation = presentation !== undefined}
    {@const form = presentation?.form ?? 'hbar'}
    {@const currentStatus = status(field)}
    {@const currentFeedback = feedbackOf(field)}
    {@const disabledReason = usable(current) ? undefined : 'Not yet observed'}
    {@const accessibility = {
      description: disabledReason ?? null,
      valueText: `${label}: ${formatValue(field, canonical(field))}${currentStatus === '' ? '' : `; ${currentStatus}`}`,
    }}
    <div
      class="cw-keyer-level"
      class:cw-keyer-level--presented={explicitPresentation}
      data-testid={`cw-keyer-${field}`}
      data-field={field}
      data-scalar-form={form}
      data-observed={usable(current) && canonical(field) !== null}
      data-command-phase={currentFeedback?.phase}
      data-feedback-control={currentFeedback?.scope.control}
      data-min={min} data-max={max} data-step={step}
      aria-busy={currentFeedback?.busy}
      title={disabledReason}
      use:retireHBarStatus={{ field, form }}
    >
      <span class="cw-keyer-name" class:sr-only={explicitPresentation}
        aria-hidden={explicitPresentation ? 'true' : undefined}>{label}</span>
      <ValueControl
        {...feedbackIntegratedControl}
        binding={bindings[field]} {label} renderer={form}
        displayFn={(value) => formatValue(field, value)}
        showLabel={explicitPresentation ? presentation?.showLabel ?? true : false}
        showValue={explicitPresentation ? presentation?.showValue ?? true : false}
        compact={explicitPresentation ? presentation?.compact ?? false : true}
        title={disabledReason} {accessibility}
        skin={scalarAppearance}
        {presentationIsCurrent}
        issuedStatusPresentation={form === 'hbar' ? statusPresentations[field] : undefined}
      />
      <output data-testid={`cw-keyer-${field}-value`} data-canonical-value
        class:sr-only={explicitPresentation} aria-hidden={explicitPresentation ? 'true' : undefined}
      >{formatValue(field, canonical(field))}</output>
      {#if currentStatus !== ''}
        <span data-command-status class:command-pending={currentFeedback?.busy}
          class:sr-only={explicitPresentation || currentFeedback?.phase === 'unavailable'}
        >{currentStatus}</span>
      {/if}
    </div>
  {/if}
{/snippet}

{#snippet keyerSpeed(presentation?: Readonly<CwContinuousPresentation>)}
  {@render scalar('keyerSpeed', presentation)}
{/snippet}
{#snippet pitchHz(presentation?: Readonly<CwContinuousPresentation>)}
  {@render scalar('pitchHz', presentation)}
{/snippet}
{@render children({ keyerSpeed, pitchHz })}

{#each CW_CONTINUOUS_LEVELS as [field] (field)}
  {#if issuedStatus[field] !== null}
    {#key issuedStatusKey[field]}
      <span class="sr-only" role="status" aria-live="polite" aria-atomic="true"
        data-control-feedback-status data-cw-feedback-status data-feedback-lane={field}
      >{issuedStatus[field]}</span>
    {/key}
  {/if}
{/each}

<style>
  .cw-keyer-level { display: flex; align-items: baseline; gap: 0.5rem; }
  .cw-keyer-level--presented { display: inline-flex; min-width: 0; max-width: 100%; }
  .cw-keyer-name { min-width: 12ch; }
  .cw-keyer-level :global(.vc-hbar) { width: 100%; min-width: 0; }
  .command-pending { font-style: italic; }
  .sr-only {
    position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
    overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0;
  }
</style>
