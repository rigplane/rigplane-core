<script lang="ts">
  import { onDestroy, untrack } from 'svelte';
  import HBarRenderer from './HBarRenderer.svelte';
  import BipolarRenderer from './BipolarRenderer.svelte';
  import KnobRenderer from './KnobRenderer.svelte';
  import DiscreteRenderer from './DiscreteRenderer.svelte';
  import type { LegacyReadingPresentation } from './scalar-render-presentation';
  import type { Skin } from './skin';
  import {
    createContinuousScalar,
    createHBarContinuousScalarPolicy,
    type ContinuousScalarBinding,
    type ContinuousScalarPolicy,
  } from '../../../primitives/scalar/continuous-scalar.svelte';

  interface PresentationProps {
    label: string;
    displayFn?: (v: number) => string;
    accentColor?: string;
    fillColor?: string;
    fillGradient?: string[];
    trackColor?: string;
    showValue?: boolean;
    showLabel?: boolean;
    compact?: boolean;
    variant?: 'modern' | 'hardware' | 'hardware-illuminated';
    arcAngle?: number;
    tickCount?: number;
    tickLabels?: string[];
    showAllTicks?: boolean;
    tickStyle?: 'ruler' | 'led' | 'notch';
    feedbackPhase?: string | null;
    feedbackBusy?: boolean;
    feedbackDescription?: string | null;
    feedbackStatus?: string | null;
    unit?: string;
    shortcutHint?: string | null;
    title?: string | null;
    skin?: Skin;
  }

  interface RawProps {
    binding?: undefined;
    value: number;
    min: number;
    max: number;
    step: number;
    keyboardStep?: number;
    defaultValue?: number;
    fineStepDivisor?: number;
    renderer: 'hbar' | 'bipolar' | 'knob' | 'discrete';
    optimistic?: boolean;
    onChange: (value: number) => void;
    onchange?: (value: number) => void;
    debounceMs?: number;
    disabled?: boolean;
  }

  interface BoundHBarProps {
    binding: ContinuousScalarBinding;
    renderer: 'hbar';
    value?: undefined;
    min?: undefined;
    max?: undefined;
    step?: undefined;
    keyboardStep?: undefined;
    defaultValue?: undefined;
    fineStepDivisor?: undefined;
    optimistic?: undefined;
    onChange?: undefined;
    onchange?: undefined;
    debounceMs?: undefined;
    disabled?: undefined;
    skin?: undefined;
  }

  type Props = PresentationProps & (RawProps | BoundHBarProps);

  let {
    binding: externalBinding,
    value = Number.NaN,
    min = Number.NaN,
    max = Number.NaN,
    step = Number.NaN,
    keyboardStep,
    defaultValue,
    fineStepDivisor = 10,
    label,
    displayFn,
    renderer,
    accentColor = 'var(--v2-accent-cyan)',
    fillColor,
    fillGradient,
    trackColor,
    showValue = true,
    showLabel = true,
    compact = false,
    variant = 'modern',
    arcAngle = 270,
    tickCount = 0,
    tickLabels = [],
    showAllTicks = true,
    tickStyle = 'ruler',
    optimistic = true,
    feedbackPhase = null,
    feedbackBusy,
    feedbackDescription = null,
    feedbackStatus = null,
    onChange,
    debounceMs = 50,
    disabled = false,
    unit = '',
    shortcutHint = null,
    title = null,
    onchange,
    skin,
  }: Props = $props();

  let effectiveOnChange = $derived(onChange ?? onchange ?? (() => {}));
  const mountId = $props.id();
  let hbarPolicy = $derived(createHBarContinuousScalarPolicy({
    preview: optimistic ? 'optimistic' : 'confirmed',
    debounceMs,
  }));
  const adapterPolicy: ContinuousScalarPolicy = {
    get name() { return hbarPolicy.name; },
    get preview() { return hbarPolicy.preview; },
    normalize(candidate, domain) { return hbarPolicy.normalize(candidate, domain); },
    wheel(current, event, domain) { return hbarPolicy.wheel(current, event, domain); },
    key(current, event, domain) { return hbarPolicy.key(current, event, domain); },
    reset(domain) { return hbarPolicy.reset(domain); },
    dispatch(source) { return hbarPolicy.dispatch(source); },
    dispatchesCanonical(source) { return hbarPolicy.dispatchesCanonical(source); },
    get wheelIdleMs() { return hbarPolicy.wheelIdleMs; },
    describeTarget(candidate) { return hbarPolicy.describeTarget(candidate); },
  };

  function createRawBinding(): ContinuousScalarBinding {
    return createContinuousScalar(
      () => ({
        evidence: 'reading' as const,
        domain: {
          min,
          max,
          step,
          defaultValue: defaultValue ?? null,
          fineStepDivisor,
          keyboardStep,
        },
        enabled: !disabled,
        request: effectiveOnChange,
        reading: Number.isFinite(value)
          ? { status: 'known' as const, value }
          : { status: 'unknown' as const },
        ownerKey: `${mountId}:${optimistic}:${debounceMs}`,
      }),
      adapterPolicy,
    );
  }

  const initialExternalBinding = untrack(() => externalBinding);
  let attachedExternalBinding = initialExternalBinding;
  let ownedBinding: ContinuousScalarBinding | null = initialExternalBinding === undefined
    ? createRawBinding()
    : null;
  let hbarBinding = $state(initialExternalBinding ?? ownedBinding!);

  $effect(() => {
    if (externalBinding === attachedExternalBinding) return;
    const displacedOwnedBinding = ownedBinding;
    attachedExternalBinding = externalBinding;
    if (externalBinding === undefined) {
      ownedBinding = createRawBinding();
      hbarBinding = ownedBinding;
    } else {
      ownedBinding = null;
      hbarBinding = externalBinding;
    }
    if (displacedOwnedBinding !== null) {
      queueMicrotask(() => displacedOwnedBinding.destroy());
    }
  });

  onDestroy(() => ownedBinding?.destroy());

  let commonProps = $derived({
    value,
    min,
    max,
    step,
    keyboardStep,
    defaultValue,
    fineStepDivisor,
    label,
    displayFn,
    accentColor,
    fillColor,
    fillGradient,
    trackColor,
    showValue,
    showLabel,
    compact,
    variant,
    onChange: effectiveOnChange,
    debounceMs,
    disabled,
    unit,
    shortcutHint,
    title,
  });
  let knobProps = $derived({ ...commonProps, arcAngle, tickCount, tickLabels });
  let discreteProps = $derived({ ...commonProps, tickLabels, showAllTicks, tickStyle });
  let legacy = $derived<LegacyReadingPresentation>({
    phase: feedbackPhase,
    busy: feedbackBusy,
    description: feedbackDescription,
    status: feedbackStatus,
  });
  let unknownDisplay = $derived(
    externalBinding === undefined && !Number.isFinite(value) && displayFn
      ? displayFn(value)
      : undefined,
  );
  let skinComponent = $derived(
    skin
      ? renderer === 'knob' ? skin.knob
        : renderer === 'hbar' ? skin.hbar
        : renderer === 'bipolar' ? skin.bipolar
        : undefined
      : undefined,
  );
</script>

{#if skinComponent}
  {@const SkinRenderer = skinComponent}
  {#if renderer === 'knob'}
    <SkinRenderer {...knobProps} />
  {:else}
    <SkinRenderer {...commonProps} />
  {/if}
{:else if renderer === 'hbar'}
  <HBarRenderer
    binding={hbarBinding} {label} {displayFn} {unknownDisplay}
    {fillColor} {fillGradient} {trackColor}
    {accentColor} {showValue} {showLabel} {compact} {variant} {unit} {shortcutHint} {title}
    {legacy}
  />
{:else if renderer === 'bipolar'}
  <BipolarRenderer {...commonProps} />
{:else if renderer === 'knob'}
  <KnobRenderer {...knobProps} />
{:else if renderer === 'discrete'}
  <DiscreteRenderer {...discreteProps} />
{/if}
