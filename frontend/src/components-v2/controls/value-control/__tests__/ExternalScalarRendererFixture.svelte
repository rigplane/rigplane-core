<script lang="ts">
  import { onDestroy, untrack } from 'svelte';
  import type {
    ContinuousScalarRendererLease,
    ContinuousScalarView,
  } from '../../../../primitives/scalar/continuous-scalar.svelte';
  import type {
    DiscreteSkinRendererProps,
    HBarSkinRendererProps,
    KnobSkinRendererProps,
  } from '../skin';

  type FixtureProps = HBarSkinRendererProps & KnobSkinRendererProps & DiscreteSkinRendererProps;

  let {
    binding,
    label,
    displayFn,
    unknownDisplay,
    accentColor,
    fillColor,
    fillGradient,
    trackColor,
    showValue,
    showLabel,
    compact,
    variant,
    unit,
    shortcutHint,
    title,
    accessibility,
    legacy,
    valueProjection,
    issuedStatusPresentation,
    arcAngle,
    tickCount,
    tickLabels,
    showAllTicks,
    tickStyle,
    dimmed,
  }: FixtureProps = $props();

  let attachedBinding = untrack(() => binding);
  let lease: ContinuousScalarRendererLease = attachedBinding.attachRenderer();
  let view = $state<ContinuousScalarView>(untrack(() => lease.view));
  let renderer = $derived(valueProjection !== undefined
    ? 'hbar'
    : arcAngle !== undefined || tickCount !== undefined
      ? 'knob'
      : tickStyle !== undefined || showAllTicks !== undefined || dimmed !== undefined
        ? 'discrete'
        : 'bipolar');

  $effect.pre(() => {
    if (binding !== attachedBinding) {
      lease.dispose();
      attachedBinding = binding;
      lease = binding.attachRenderer();
    }
    view = lease.view;
  });

  onDestroy(() => lease.dispose());

  function exposeLease(node: HTMLElement) {
    Object.defineProperty(node, 'rendererLease', {
      configurable: true,
      get: () => lease,
    });
  }
</script>

<button
  type="button"
  role="slider"
  use:exposeLease
  data-external-scalar-renderer={renderer}
  data-evidence={view.evidence}
  data-confirmed={view.confirmed ?? ''}
  data-requested={view.requested ?? ''}
  data-phase={view.phase}
  data-error={view.error ?? ''}
  data-label={label}
  data-display={displayFn?.(view.displayed ?? Number.NaN) ?? ''}
  data-unknown-display={unknownDisplay ?? ''}
  data-accent-color={accentColor ?? ''}
  data-fill-color={fillColor ?? ''}
  data-fill-gradient={fillGradient?.join('|') ?? ''}
  data-track-color={trackColor ?? ''}
  data-show-value={String(showValue)}
  data-show-label={String(showLabel)}
  data-compact={String(compact)}
  data-variant={variant ?? ''}
  data-unit={unit ?? ''}
  data-shortcut-hint={shortcutHint ?? ''}
  data-title={title ?? ''}
  data-accessibility-description={accessibility?.description ?? ''}
  data-accessibility-value-text={accessibility?.valueText ?? ''}
  data-legacy-phase={legacy?.phase ?? ''}
  data-legacy-busy={String(legacy?.busy)}
  data-legacy-description={legacy?.description ?? ''}
  data-legacy-status={legacy?.status ?? ''}
  data-projection-context={valueProjection?.contextKey ?? ''}
  data-issued-status={issuedStatusPresentation?.text ?? ''}
  data-arc-angle={arcAngle ?? ''}
  data-tick-count={tickCount ?? ''}
  data-tick-labels={tickLabels?.join('|') ?? ''}
  data-show-all-ticks={String(showAllTicks)}
  data-tick-style={tickStyle ?? ''}
  data-dimmed={String(dimmed)}
  aria-label={label}
  aria-valuenow={view.confirmed ?? undefined}
  aria-disabled={!view.editable}
  onclick={() => lease.key({ key: 'ArrowRight', fine: false })}
>
  {view.displayed ?? unknownDisplay ?? 'unknown'}
</button>
