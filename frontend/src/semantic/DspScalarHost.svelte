<script lang="ts">
  import { onDestroy, type Snippet } from 'svelte';
  import type { ScalarAppearance } from '../../component-kit-api/src/index';
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
  import { NOTCH_WIDTH_LABELS, formatAgcTime } from '../components-v2/panels/dsp-panel-logic';
  import { disabledReasonText } from './disabled-reason';
  import type { DspField, RadioViewModel } from './radio-view-model';
  import { DSP_SCALAR_FIELDS, type DspScalarFeedback, type DspScalarField,
    type DspScalarHandles, type DspScalarPresentation } from './dsp-scalars';

  interface Props {
    view: RadioViewModel | null;
    feedback: DspScalarFeedback;
    nbLevelMax?: number;
    nbLevelPercent?: boolean;
    onLevelChange?: (field: DspScalarField, value: number) => void;
    scalarAppearance?: ScalarAppearance;
    presentationIsCurrent?: () => boolean;
    children: Snippet<[DspScalarHandles]>;
  }

  let { view, feedback, nbLevelMax = 255, nbLevelPercent = false,
    onLevelChange, scalarAppearance, presentationIsCurrent, children }: Props = $props();
  let dsp = $derived(view?.dsp);

  const LABELS = {
    nbLevel: 'NB level', nbDepth: 'NB depth', nbWidth: 'NB width', nrLevel: 'NR level',
    notchFreq: 'Notch position', manualNotchWidth: 'Notch width', agcTimeConstant: 'AGC time',
  } as const;
  const COMMANDS = {
    nbLevel: 'set_nb_level', nbDepth: 'set_nb_depth', nbWidth: 'set_nb_width',
    nrLevel: 'set_nr_level', notchFreq: 'set_notch_filter',
    manualNotchWidth: 'set_manual_notch_width', agcTimeConstant: 'set_agc_time_constant',
  } as const;
  const feedbackIntegratedControl = { 'feedback-policy': 'feedback-integrated' } as const;
  const safeInteger = (value: unknown): value is number =>
    typeof value === 'number' && Number.isSafeInteger(value);
  const usable = (field: DspField<unknown> | undefined): boolean => field !== undefined
    && field.availability.structural && field.availability.operational
    && field.reading.status === 'known';

  function domain(field: DspScalarField): Readonly<{ domain: ScalarDomain; valid: boolean }> {
    if (field === 'nrLevel') {
      const nr = dsp?.nrLevelProjection?.domain;
      if (nr === undefined || nr === null || !safeInteger(nr.min) || !safeInteger(nr.max)
        || !safeInteger(nr.step) || !safeInteger(nr.origin) || nr.step <= 0 || nr.max <= nr.min) {
        return {
          valid: false,
          domain: { min: 0, max: 15, step: 1, defaultValue: null, fineStepDivisor: 1 },
        };
      }
      return {
        valid: true,
        domain: {
          min: nr.min, max: nr.max, step: nr.step,
          defaultValue: null, fineStepDivisor: 1,
        },
      };
    }
    const [min, max] = field === 'nbLevel' ? [0, nbLevelMax]
      : field === 'nbDepth' ? [1, 10]
        : field === 'manualNotchWidth' ? [0, 2]
          : field === 'agcTimeConstant' ? [0, 9] : [0, 255];
    const valid = safeInteger(min) && safeInteger(max) && max > min;
    return {
      valid,
      domain: { min, max, step: 1, defaultValue: null, fineStepDivisor: 1 },
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

  const bindings: Partial<Record<DspScalarField, ReturnType<typeof createContinuousScalar>>> = {};
  function bindingFor(field: DspScalarField): ReturnType<typeof createContinuousScalar> {
    return bindings[field] ??= createContinuousScalar(
      () => input(field), createRenderedNativeRangeContinuousScalarPolicy(),
    );
  }
  // Preserve the original two-lane host's eager identity/order. Additional
  // disclosure bindings are created only when Standard opens their panel.
  bindingFor('nbLevel');
  bindingFor('nbWidth');

  const record = <T,>(value: T) => Object.fromEntries(
    DSP_SCALAR_FIELDS.map((field) => [field, value]),
  ) as Record<DspScalarField, T>;
  let issuedStatus = $state(record<string | null>(null));
  let issuedStatusKey = $state(record(''));
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
  const statusPresentations = Object.fromEntries(DSP_SCALAR_FIELDS.map((field) => [
    field, statusPresentation(field),
  ])) as Record<DspScalarField, Readonly<HBarIssuedStatusPresentation>>;

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
    if (field === 'manualNotchWidth') return NOTCH_WIDTH_LABELS[value] ?? String(value);
    if (field === 'agcTimeConstant') return `${formatAgcTime(value)}s`;
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
  function disabledReason(field: DspScalarField): string | undefined {
    return enabled(field) ? undefined : disabledReasonText({ structural: true, operational: false });
  }

  onDestroy(() => {
    for (const binding of Object.values(bindings)) binding.destroy();
  });
</script>

{#snippet scalar(field: DspScalarField, presentation?: Readonly<DspScalarPresentation>)}
  {#if dsp?.[field].availability.structural}
    {@const explicit = presentation !== undefined}
    {@const form = presentation?.form ?? 'hbar'}
    {@const label = LABELS[field]}
    {@const current = feedback[field]}
    {@const currentStatus = status(field)}
    {@const reason = disabledReason(field)}
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
      <ValueControl {...feedbackIntegratedControl} binding={bindingFor(field)} {label} renderer={form}
        displayFn={(value) => formatValue(field, value)}
        showLabel={explicit ? presentation?.showLabel ?? true : false}
        showValue={explicit ? presentation?.showValue ?? true : false}
        compact={explicit ? presentation?.compact ?? false : true}
        variant={presentation?.variant ?? 'modern'}
        title={reason} {accessibility}
        skin={scalarAppearance} {presentationIsCurrent}
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
{#snippet nbDepth(presentation?: Readonly<DspScalarPresentation>)}
  {@render scalar('nbDepth', presentation)}
{/snippet}
{#snippet nrLevel(presentation?: Readonly<DspScalarPresentation>)}
  {@render scalar('nrLevel', presentation)}
{/snippet}
{#snippet notchFreq(presentation?: Readonly<DspScalarPresentation>)}
  {@render scalar('notchFreq', presentation)}
{/snippet}
{#snippet manualNotchWidth(presentation?: Readonly<DspScalarPresentation>)}
  {@render scalar('manualNotchWidth', presentation)}
{/snippet}
{#snippet agcTimeConstant(presentation?: Readonly<DspScalarPresentation>)}
  {@render scalar('agcTimeConstant', presentation)}
{/snippet}

{@render children({
  nbLevel, nbDepth, nbWidth, nrLevel, notchFreq, manualNotchWidth, agcTimeConstant,
})}

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
