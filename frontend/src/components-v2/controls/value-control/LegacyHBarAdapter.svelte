<script lang="ts">
  import { onDestroy } from 'svelte';
  import HBarRenderer from './HBarRenderer.svelte';
  import {
    createContinuousScalar,
    createHBarContinuousScalarPolicy,
    type ContinuousScalarPolicy,
  } from '../../../primitives/scalar/continuous-scalar.svelte';

  interface Props {
    value: number;
    min: number;
    max: number;
    step: number;
    defaultValue?: number;
    fineStepDivisor?: number;
    label: string;
    displayFn?: (v: number) => string;
    fillColor?: string;
    fillGradient?: string[];
    trackColor?: string;
    accentColor?: string;
    showValue?: boolean;
    showLabel?: boolean;
    compact?: boolean;
    variant?: 'modern' | 'hardware' | 'hardware-illuminated';
    onChange: (value: number) => void;
    debounceMs?: number;
    disabled?: boolean;
    unit?: string;
    shortcutHint?: string | null;
    title?: string | null;
    optimistic?: boolean;
    feedbackPhase?: string | null;
    feedbackBusy?: boolean;
    feedbackDescription?: string | null;
    feedbackStatus?: string | null;
  }

  let {
    value,
    min,
    max,
    step,
    defaultValue,
    fineStepDivisor = 10,
    label,
    displayFn,
    fillColor,
    fillGradient,
    trackColor,
    accentColor,
    showValue = true,
    showLabel = true,
    compact = false,
    variant = 'modern',
    onChange,
    debounceMs = 0,
    disabled = false,
    unit = '',
    shortcutHint = null,
    title = null,
    optimistic = true,
    feedbackPhase = null,
    feedbackBusy,
    feedbackDescription = null,
    feedbackStatus = null,
  }: Props = $props();

  const mountId = $props.id();
  let hbarPolicy = $derived(createHBarContinuousScalarPolicy({
    preview: optimistic ? 'optimistic' : 'confirmed',
    debounceMs,
  }));
  const policy: ContinuousScalarPolicy = {
    get name() { return hbarPolicy.name; },
    get preview() { return hbarPolicy.preview; },
    normalize(value, domain) { return hbarPolicy.normalize(value, domain); },
    wheel(current, event, domain) { return hbarPolicy.wheel(current, event, domain); },
    key(current, event, domain) { return hbarPolicy.key(current, event, domain); },
    reset(domain) { return hbarPolicy.reset(domain); },
    dispatch(source) { return hbarPolicy.dispatch(source); },
    dispatchesCanonical(source) { return hbarPolicy.dispatchesCanonical(source); },
    get wheelIdleMs() { return hbarPolicy.wheelIdleMs; },
    describeTarget(value) { return hbarPolicy.describeTarget(value); },
  };
  const binding = createContinuousScalar(
    () => ({
      evidence: 'reading' as const,
      domain: { min, max, step, defaultValue: defaultValue ?? null, fineStepDivisor },
      enabled: !disabled,
      request: onChange,
      reading: Number.isFinite(value)
        ? { status: 'known' as const, value }
        : { status: 'unknown' as const },
      // Settings affect policy and therefore invalidate any pending interaction.
      ownerKey: `${mountId}:${optimistic}:${debounceMs}`,
    }),
    policy,
  );

  onDestroy(() => binding.destroy());
</script>

<HBarRenderer
  {binding} {min} {max} {step} {label} {displayFn} {fillColor} {fillGradient} {trackColor}
  {accentColor} {showValue} {showLabel} {compact} {variant} {unit} {shortcutHint} {title}
  legacyPresentation={{ feedbackPhase, feedbackBusy, feedbackDescription, feedbackStatus }}
/>
