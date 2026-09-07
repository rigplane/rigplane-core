<script module lang="ts">
  import type { Snippet } from 'svelte';

  export const CW_KEYER_SPEED = [
    'keyerSpeed', 'Keyer speed', 6, 48, 1, 'WPM', 'set_key_speed',
  ] as const;
  export const CW_CONTINUOUS_LEVELS = [CW_KEYER_SPEED] as const;
  export type CwContinuousField = typeof CW_KEYER_SPEED[0];
  export type CwContinuousForm = 'hbar' | 'knob';

  export interface CwContinuousPresentation {
    readonly form?: CwContinuousForm;
    readonly compact?: boolean;
    readonly showLabel?: boolean;
    readonly showValue?: boolean;
  }

  export interface CwKeyerInstrumentHandles {
    readonly keyerSpeed: Snippet<[
      presentation?: Readonly<CwContinuousPresentation>,
    ]>;
  }
</script>

<script lang="ts">
  import { onDestroy } from 'svelte';
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
    onLevelChange?: (field: CwContinuousField, value: number) => void;
    children: Snippet<[CwKeyerInstrumentHandles]>;
  }

  let { view, keySpeedFeedback, onLevelChange, children }: Props = $props();
  let keyerSpeedField = $derived(view?.cwKeyer?.keyerSpeed);
  const [field, label, min, max, step, unit, command] = CW_KEYER_SPEED;
  const feedbackIntegratedControl = { 'feedback-policy': 'feedback-integrated' } as const;
  const policy = createRenderedNativeRangeContinuousScalarPolicy();
  const usable = (current: CwKeyerField<unknown> | undefined): boolean =>
    current?.availability.structural === true
    && current.availability.operational
    && current.reading.status === 'known';
  const formatValue = (value: number | null): string =>
    value === null || !Number.isFinite(value) ? '—' : `${value} ${unit}`;

  function request(value: number): void {
    if (usable(keyerSpeedField)) onLevelChange?.(field, value);
  }

  function input(): Readonly<ContinuousScalarInput> {
    const common = {
      domain: { min, max, step, defaultValue: null, fineStepDivisor: 1 },
      enabled: usable(keyerSpeedField),
      request,
    } as const;
    if (keySpeedFeedback !== undefined) {
      return {
        ...common, evidence: 'command-feedback', feedback: keySpeedFeedback, command,
      };
    }
    return {
      ...common,
      evidence: 'reading',
      ownerKey: 'cw-keyer-keyerSpeed-reading',
      reading: keyerSpeedField?.reading.status === 'known'
        ? { status: 'known', value: keyerSpeedField.reading.value }
        : { status: 'unknown' },
    };
  }

  const binding = createContinuousScalar(input, policy);
  let issuedStatus = $state<string | null>(null);
  let issuedStatusKey = $state('');
  const issuedStatusPresentation: Readonly<HBarIssuedStatusPresentation> = {
    get text() { return null; },
    format({ view: scalarView, announcement }: Readonly<HBarIssuedStatusSnapshot>) {
      issuedStatusKey = JSON.stringify([
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
    accept(text) { issuedStatus = text; },
  };

  function retireHBarStatus(
    _node: HTMLElement,
    placement: Readonly<{ form: CwContinuousForm }>,
  ) {
    const retire = (next: typeof placement) => {
      if (next.form === 'knob') issuedStatus = null;
    };
    retire(placement);
    return { update: retire };
  }

  function canonical(): number | null {
    if (keySpeedFeedback !== undefined) {
      return keySpeedFeedback.availability === 'available' ? keySpeedFeedback.confirmed : null;
    }
    return keyerSpeedField?.reading.status === 'known' ? keyerSpeedField.reading.value : null;
  }

  function status(): string {
    if (keySpeedFeedback === undefined || keySpeedFeedback.phase === 'idle') return '';
    const target = keySpeedFeedback.target ?? keySpeedFeedback.requestedTarget;
    const requested = target === null ? '' : `; requested ${formatValue(target)}`;
    const confirmed = `; confirmed ${formatValue(keySpeedFeedback.confirmed)}`;
    const error = keySpeedFeedback.outcome?.error === undefined
      ? '' : `; ${keySpeedFeedback.outcome.error}`;
    return `${keySpeedFeedback.phase.replaceAll('-', ' ')}${requested}${confirmed}${error}`;
  }

  onDestroy(() => binding.destroy());
</script>

{#snippet keyerSpeedHandle(presentation?: Readonly<CwContinuousPresentation>)}
  {#if keyerSpeedField?.availability.structural}
    {@const explicitPresentation = presentation !== undefined}
    {@const form = presentation?.form ?? 'hbar'}
    {@const currentStatus = status()}
    {@const disabledReason = usable(keyerSpeedField) ? undefined : 'Not yet observed'}
    {@const accessibility = {
      description: disabledReason ?? null,
      valueText: `${label}: ${formatValue(canonical())}${currentStatus === '' ? '' : `; ${currentStatus}`}`,
    }}
    <div
      class="cw-keyer-level"
      class:cw-keyer-level--presented={explicitPresentation}
      data-testid="cw-keyer-keyerSpeed"
      data-field={field}
      data-scalar-form={form}
      data-observed={usable(keyerSpeedField) && canonical() !== null}
      data-command-phase={keySpeedFeedback?.phase}
      data-feedback-control={keySpeedFeedback?.scope.control}
      data-min={min} data-max={max} data-step={step}
      aria-busy={keySpeedFeedback?.busy}
      title={disabledReason}
      use:retireHBarStatus={{ form }}
    >
      <span class="cw-keyer-name" class:sr-only={explicitPresentation}
        aria-hidden={explicitPresentation ? 'true' : undefined}>{label}</span>
      <ValueControl
        {...feedbackIntegratedControl}
        {binding} {label} renderer={form}
        displayFn={formatValue}
        showLabel={explicitPresentation ? presentation?.showLabel ?? true : false}
        showValue={explicitPresentation ? presentation?.showValue ?? true : false}
        compact={explicitPresentation ? presentation?.compact ?? false : true}
        title={disabledReason} {accessibility}
        issuedStatusPresentation={form === 'hbar' ? issuedStatusPresentation : undefined}
      />
      <output data-testid="cw-keyer-keyerSpeed-value" data-canonical-value
        class:sr-only={explicitPresentation} aria-hidden={explicitPresentation ? 'true' : undefined}
      >{formatValue(canonical())}</output>
      {#if currentStatus !== ''}
        <span data-command-status class:command-pending={keySpeedFeedback?.busy}
          class:sr-only={explicitPresentation || keySpeedFeedback?.phase === 'unavailable'}
        >{currentStatus}</span>
      {/if}
    </div>
  {/if}
{/snippet}

{#snippet keyerSpeed(presentation?: Readonly<CwContinuousPresentation>)}
  {@render keyerSpeedHandle(presentation)}
{/snippet}
{@render children({ keyerSpeed })}

{#if issuedStatus !== null}
  {#key issuedStatusKey}
    <span class="sr-only" role="status" aria-live="polite" aria-atomic="true"
      data-control-feedback-status data-cw-feedback-status data-feedback-lane="keyerSpeed"
    >{issuedStatus}</span>
  {/key}
{/if}

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
