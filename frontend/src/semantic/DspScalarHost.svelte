<script lang="ts">
  import { onDestroy, type Snippet } from 'svelte';
  import { ValueControl } from '../components-v2/controls/value-control';
  import type { HBarIssuedStatusPresentation, HBarIssuedStatusSnapshot }
    from '../components-v2/controls/value-control/skin';
  import {
    createContinuousScalar,
    createRenderedNativeRangeContinuousScalarPolicy,
    type ContinuousScalarInput,
    type ScalarDomain,
  } from '../primitives/scalar/continuous-scalar.svelte';
  import { rawToPercentDisplay } from '../primitives/scalar/value-control-core';
  import type { DspField, RadioViewModel } from './radio-view-model';
  import { DSP_SCALAR_FIELDS, type DspScalarFeedback, type DspScalarField,
    type DspScalarHandles, type DspScalarPresentation } from './dsp-scalars';

  interface Props {
    view: RadioViewModel | null;
    feedback: DspScalarFeedback;
    nbLevelMax?: number;
    nbLevelPercent?: boolean;
    onLevelChange?: (field: DspScalarField, value: number) => void;
    children: Snippet<[DspScalarHandles]>;
  }

  let { view, feedback, nbLevelMax = 255, nbLevelPercent = false,
    onLevelChange, children }: Props = $props();
  let dsp = $derived(view?.dsp);

  const LABELS = { nbLevel: 'NB level', nbWidth: 'NB width' } as const;
  const COMMANDS = { nbLevel: 'set_nb_level', nbWidth: 'set_nb_width' } as const;
  const feedbackIntegratedControl = { 'feedback-policy': 'feedback-integrated' } as const;
  const safeInteger = (value: unknown): value is number =>
    typeof value === 'number' && Number.isSafeInteger(value);
  const usable = (field: DspField<unknown> | undefined): boolean => field !== undefined
    && field.availability.structural && field.availability.operational
    && field.reading.status === 'known';

  function domain(field: DspScalarField): Readonly<{ domain: ScalarDomain; valid: boolean }> {
    const max = field === 'nbLevel' ? nbLevelMax : 255;
    const valid = safeInteger(max) && max > 0;
    return {
      valid,
      domain: { min: 0, max, step: 1, defaultValue: null, fineStepDivisor: 1 },
    };
  }
  function enabled(field: DspScalarField): boolean {
    const current = domain(field);
    return current.valid && usable(dsp?.[field]) && feedback[field].availability === 'available';
  }
  function input(field: DspScalarField): Readonly<ContinuousScalarInput> {
    const current = domain(field);
    return {
      evidence: 'command-feedback', domain: current.domain,
      enabled: current.valid && usable(dsp?.[field]) && feedback[field].availability === 'available',
      request: (value) => { if (enabled(field)) onLevelChange?.(field, value); },
      feedback: feedback[field], command: COMMANDS[field],
    };
  }

  const nbLevelPolicy = createRenderedNativeRangeContinuousScalarPolicy();
  const nbWidthPolicy = createRenderedNativeRangeContinuousScalarPolicy();
  const nbLevelBinding = createContinuousScalar(() => input('nbLevel'), nbLevelPolicy);
  const nbWidthBinding = createContinuousScalar(() => input('nbWidth'), nbWidthPolicy);
  const bindings = { nbLevel: nbLevelBinding, nbWidth: nbWidthBinding } as const;

  let issuedStatus = $state<Record<DspScalarField, string | null>>({ nbLevel: null, nbWidth: null });
  let issuedStatusKey = $state<Record<DspScalarField, string>>({ nbLevel: '', nbWidth: '' });
  function statusPresentation(field: DspScalarField): Readonly<HBarIssuedStatusPresentation> {
    return {
      get text() { return null; },
      format({ view: scalarView, announcement }: Readonly<HBarIssuedStatusSnapshot>) {
        issuedStatusKey[field] = JSON.stringify([
          scalarView.feedback.providerGeneration ?? null, scalarView.feedback.sessionEpoch,
          scalarView.feedback.scope.control, scalarView.feedback.scope.receiver,
          scalarView.feedback.scope.slot ?? null, announcement.transitionId,
        ]);
        return scalarView.error === null
          ? announcement.message
          : `${announcement.message.replace(/[.!?]$/, '')}: ${scalarView.error}`;
      },
      accept(text) { issuedStatus[field] = text; },
    };
  }
  const statusPresentations = {
    nbLevel: statusPresentation('nbLevel'), nbWidth: statusPresentation('nbWidth'),
  } as const;

  function retireHBarStatus(
    _node: HTMLElement,
    placement: Readonly<{ field: DspScalarField; form: 'hbar' | 'knob' }>,
  ) {
    const retire = (next: typeof placement) => {
      if (next.form === 'knob') issuedStatus[next.field] = null;
    };
    retire(placement);
    return { update: retire };
  }
  function formatValue(field: DspScalarField, value: number | null): string {
    if (value === null || !Number.isFinite(value)) return '?';
    return field === 'nbLevel' && nbLevelPercent
      ? rawToPercentDisplay(value, 0, nbLevelMax) : String(value);
  }
  function canonical(field: DspScalarField): number | null {
    const current = feedback[field];
    return current.availability === 'available' ? current.confirmed : null;
  }
  function status(field: DspScalarField): string {
    const current = feedback[field];
    if (current.phase === 'idle') return '';
    const target = current.target ?? current.requestedTarget;
    const requested = target === null ? '' : `; requested ${formatValue(field, target)}`;
    const confirmed = `; confirmed ${formatValue(field, current.confirmed)}`;
    const error = current.outcome?.error === undefined ? '' : `; ${current.outcome.error}`;
    return `${LABELS[field]}: ${current.phase.replaceAll('-', ' ')}${requested}${confirmed}${error}`;
  }

  onDestroy(() => { nbLevelBinding.destroy(); nbWidthBinding.destroy(); });
</script>

{#snippet scalar(field: DspScalarField, presentation?: Readonly<DspScalarPresentation>)}
  {#if dsp?.[field].availability.structural}
    {@const explicit = presentation !== undefined}
    {@const form = presentation?.form ?? 'hbar'}
    {@const label = LABELS[field]}
    {@const current = feedback[field]}
    {@const currentStatus = status(field)}
    {@const reason = enabled(field) ? undefined : 'Not yet observed'}
    {@const accessibility = {
      description: reason ?? null,
      valueText: `${label}: ${formatValue(field, canonical(field))}${currentStatus === '' ? '' : `; ${currentStatus}`}`,
    }}
    <div class="dsp-scalar" class:dsp-scalar--presented={explicit}
      data-testid={`dsp-${field}`} data-scalar-field={field} data-scalar-form={form}
      data-feedback-control={current.scope.control} data-feedback-receiver={current.scope.receiver}
      data-disabled-reason={reason === undefined ? undefined : 'field-not-observed'}
      aria-busy={current.busy} title={reason} use:retireHBarStatus={{ field, form }}>
      <span class="dsp-scalar-name" class:sr-only={explicit}
        aria-hidden={explicit ? 'true' : undefined}>{label}</span>
      <ValueControl {...feedbackIntegratedControl} binding={bindings[field]} {label} renderer={form}
        displayFn={(value) => formatValue(field, value)}
        showLabel={explicit ? presentation?.showLabel ?? true : false}
        showValue={explicit ? presentation?.showValue ?? true : false}
        compact={explicit ? presentation?.compact ?? false : true}
        title={reason} {accessibility}
        issuedStatusPresentation={form === 'hbar' ? statusPresentations[field] : undefined} />
      <output data-canonical-value class:sr-only={explicit}
        aria-hidden={explicit ? 'true' : undefined}>{formatValue(field, canonical(field))}</output>
      {#if currentStatus !== ''}
        <span data-command-status class:command-pending={current.busy}
          class:sr-only={explicit || current.phase === 'unavailable'}>{currentStatus}</span>
      {/if}
    </div>
  {/if}
{/snippet}

{#snippet nbLevel(presentation?: Readonly<DspScalarPresentation>)}
  {@render scalar('nbLevel', presentation)}
{/snippet}
{#snippet nbWidth(presentation?: Readonly<DspScalarPresentation>)}
  {@render scalar('nbWidth', presentation)}
{/snippet}

{@render children({ nbLevel, nbWidth })}

{#each DSP_SCALAR_FIELDS as field (field)}
  {#if issuedStatus[field] !== null}
    {#key issuedStatusKey[field]}
      <span class="sr-only" role="status" aria-live="polite" aria-atomic="true"
        data-control-feedback-status data-feedback-lane={field}>{issuedStatus[field]}</span>
    {/key}
  {/if}
{/each}

<style>
  .dsp-scalar { display: grid; grid-template-columns: 9ch 8rem auto; align-items: center; gap: 0.5rem; }
  .dsp-scalar--presented { display: inline-flex; min-width: 0; max-width: 100%; }
  .dsp-scalar-name { white-space: nowrap; }
  .dsp-scalar :global(.vc-hbar) { width: 100%; min-width: 0; }
  .sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
</style>
