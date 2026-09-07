<script lang="ts">
  import { onDestroy, untrack } from 'svelte';
  import HBarRenderer from './HBarRenderer.svelte';
  import BipolarRenderer from './BipolarRenderer.svelte';
  import KnobRenderer from './KnobRenderer.svelte';
  import DiscreteRenderer from './DiscreteRenderer.svelte';
  import { getSelectedScalarAppearance } from '../../../component-kits/activation';
  import type {
    LegacyReadingPresentation,
    ScalarAccessibilityPresentation,
  } from './scalar-render-presentation';
  import type {
    HBarIssuedStatusPresentation,
    HBarValueProjection,
    Skin,
  } from './skin';
  import {
    createBipolarContinuousScalarPolicy,
    createContinuousScalar,
    createDiscreteContinuousScalarPolicy,
    createHBarContinuousScalarPolicy,
    createKnobContinuousScalarPolicy,
    createContinuousScalarRendererSeat,
    type ContinuousScalarBinding,
    type ContinuousScalarPolicy,
    type ContinuousScalarRendererSeat,
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
    accessibility?: ScalarAccessibilityPresentation;
    skin?: Skin;
    valueProjection?: Readonly<HBarValueProjection>;
    issuedStatusPresentation?: Readonly<HBarIssuedStatusPresentation>;
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

  interface BoundContinuousProps {
    binding: ContinuousScalarBinding;
    renderer: 'hbar' | 'bipolar' | 'knob' | 'discrete';
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
  }

  type Props = PresentationProps & (RawProps | BoundContinuousProps);

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
    accessibility,
    onchange,
    skin,
    valueProjection,
    issuedStatusPresentation,
  }: Props = $props();

  let effectiveOnChange = $derived(onChange ?? onchange ?? (() => {}));
  const configuredAppearance = getSelectedScalarAppearance();
  let effectiveAppearance = $derived(skin ?? configuredAppearance);
  const mountId = $props.id();
  let hbarPolicy = $derived(createHBarContinuousScalarPolicy({
    preview: optimistic ? 'optimistic' : 'confirmed',
    debounceMs,
  }));
  let bipolarPolicy = $derived(createBipolarContinuousScalarPolicy({ debounceMs }));
  let discretePolicy = $derived(createDiscreteContinuousScalarPolicy({ debounceMs }));
  let knobPolicy = $derived(createKnobContinuousScalarPolicy({ debounceMs }));
  let scalarPolicy = $derived(renderer === 'bipolar'
    ? bipolarPolicy
    : renderer === 'discrete' ? discretePolicy
      : renderer === 'knob' ? knobPolicy : hbarPolicy);
  const adapterPolicy: ContinuousScalarPolicy = {
    get name() { return scalarPolicy.name; },
    get preview() { return scalarPolicy.preview; },
    resolveKeyboardStep(domain) {
      return scalarPolicy.resolveKeyboardStep === undefined
        ? domain.keyboardStep
        : scalarPolicy.resolveKeyboardStep(domain);
    },
    normalize(candidate, domain) { return scalarPolicy.normalize(candidate, domain); },
    wheel(current, event, domain) { return scalarPolicy.wheel(current, event, domain); },
    key(current, event, domain) { return scalarPolicy.key(current, event, domain); },
    reset(domain) { return scalarPolicy.reset(domain); },
    dispatch(source) { return scalarPolicy.dispatch(source); },
    dispatchesCanonical(source, context, domain) {
      return scalarPolicy.dispatchesCanonical(source, context, domain);
    },
    get wheelIdleMs() { return scalarPolicy.wheelIdleMs; },
    describeTarget(candidate) { return scalarPolicy.describeTarget(candidate); },
  };

  function createRawBinding(): ContinuousScalarBinding {
    return createContinuousScalar(
      () => ({
        evidence: 'reading' as const,
        domain: {
          min,
          max,
          step,
          defaultValue: defaultValue ?? (renderer === 'bipolar' ? 0 : null),
          fineStepDivisor,
          keyboardStep,
        },
        enabled: !disabled,
        request: effectiveOnChange,
        reading: Number.isFinite(value)
          ? { status: 'known' as const, value }
          : { status: 'unknown' as const },
        ownerKey: `${mountId}:${scalarPolicy.name}:${debounceMs}`,
      }),
      adapterPolicy,
    );
  }

  const initialExternalBinding = untrack(() => externalBinding);
  let attachedExternalBinding = initialExternalBinding;
  let ownedBinding: ContinuousScalarBinding | null = initialExternalBinding === undefined
    ? createRawBinding()
    : null;
  let scalarBinding = $state(initialExternalBinding ?? ownedBinding!);

  $effect(() => {
    if (externalBinding === attachedExternalBinding) return;
    const displacedOwnedBinding = ownedBinding;
    attachedExternalBinding = externalBinding;
    if (externalBinding === undefined) {
      ownedBinding = createRawBinding();
      scalarBinding = ownedBinding;
    } else {
      ownedBinding = null;
      scalarBinding = externalBinding;
    }
    if (displacedOwnedBinding !== null) {
      queueMicrotask(() => displacedOwnedBinding.destroy());
    }
  });

  onDestroy(() => ownedBinding?.destroy());

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
    effectiveAppearance
      ? renderer === 'knob' ? effectiveAppearance.knob
      : renderer === 'hbar' ? effectiveAppearance.hbar
        : renderer === 'bipolar' ? effectiveAppearance.bipolar
          : effectiveAppearance.discrete
      : undefined,
  );
  const initialAppearance = untrack(() => effectiveAppearance);
  let attachedSeatBinding = untrack(() => scalarBinding);
  let attachedSeatRenderer = untrack(() => renderer);
  let attachedSeatAppearance = initialAppearance;
  let attachedSeatComponent = untrack(() => skinComponent);
  let currentRendererOccurrence: object;

  function makeRendererSeat(binding: ContinuousScalarBinding): ContinuousScalarRendererSeat {
    const occurrence = Object.freeze({});
    currentRendererOccurrence = occurrence;
    return createContinuousScalarRendererSeat(
      binding,
      () => currentRendererOccurrence === occurrence,
    );
  }

  let rendererSeat = $state(makeRendererSeat(untrack(() => scalarBinding)));
  $effect.pre(() => {
    if (scalarBinding === attachedSeatBinding
      && renderer === attachedSeatRenderer
      && effectiveAppearance === attachedSeatAppearance
      && skinComponent === attachedSeatComponent) return;
    attachedSeatBinding = scalarBinding;
    attachedSeatRenderer = renderer;
    attachedSeatAppearance = effectiveAppearance;
    attachedSeatComponent = skinComponent;
    rendererSeat = makeRendererSeat(scalarBinding);
  });

  let rendererProps = $derived({
    binding: rendererSeat,
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
  });
  let knobProps = $derived({ ...rendererProps, arcAngle, tickCount, tickLabels });
  let hbarProps = $derived({ ...rendererProps, valueProjection, issuedStatusPresentation });
  let discreteProps = $derived({
    ...rendererProps,
    tickLabels,
    showAllTicks,
    tickStyle,
    dimmed: externalBinding === undefined ? disabled : undefined,
  });
</script>

{#key rendererSeat}
  {#if skinComponent}
    {@const SkinRenderer = skinComponent}
    {#if renderer === 'knob'}
      <SkinRenderer {...knobProps} />
    {:else if renderer === 'hbar'}
      <SkinRenderer {...hbarProps} />
    {:else if renderer === 'discrete'}
      <SkinRenderer {...discreteProps} />
    {:else}
      <SkinRenderer {...rendererProps} />
    {/if}
  {:else if renderer === 'hbar'}
    <HBarRenderer
      binding={rendererSeat} {label} {displayFn} {unknownDisplay}
      {fillColor} {fillGradient} {trackColor}
      {accentColor} {showValue} {showLabel} {compact} {variant} {unit} {shortcutHint} {title}
      {accessibility} {legacy} {valueProjection} {issuedStatusPresentation}
    />
  {:else if renderer === 'bipolar'}
    <BipolarRenderer
      binding={rendererSeat} {label} {displayFn} {unknownDisplay}
      {fillColor} {fillGradient} {trackColor}
      {accentColor} {showValue} {showLabel} {compact} {variant} {unit} {shortcutHint} {title}
      {accessibility} {legacy}
    />
  {:else if renderer === 'knob'}
    <KnobRenderer {...knobProps} />
  {:else if renderer === 'discrete'}
    <DiscreteRenderer {...discreteProps} />
  {/if}
{/key}
