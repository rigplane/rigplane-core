<script lang="ts">
  import HBarRenderer from './HBarRenderer.svelte';
  import BipolarRenderer from './BipolarRenderer.svelte';
  import KnobRenderer from './KnobRenderer.svelte';
  import DiscreteRenderer from './DiscreteRenderer.svelte';
  import type { Skin } from './skin';

  interface Props {
    value: number;
    min: number;
    max: number;
    step: number;
    /** Larger increment for keyboard gestures; step remains the radio value lattice. */
    keyboardStep?: number;
    defaultValue?: number;
    fineStepDivisor?: number;
    label: string;
    displayFn?: (v: number) => string;
    renderer: 'hbar' | 'bipolar' | 'knob' | 'discrete';
    accentColor?: string;
    fillColor?: string;
    fillGradient?: string[];
    trackColor?: string;
    showValue?: boolean;
    showLabel?: boolean;
    compact?: boolean;
    variant?: 'modern' | 'hardware' | 'hardware-illuminated';
    // Knob-specific
    arcAngle?: number;
    tickCount?: number;
    tickLabels?: string[];
    /** Discrete renderer: when false, only draw ticks for the first tickLabels.length steps from min. */
    showAllTicks?: boolean;
    /** Discrete renderer: how step marks are drawn (default ruler). */
    tickStyle?: 'ruler' | 'led' | 'notch';
    /** HBar-only: keep the rendered value parent-controlled until it changes. */
    optimistic?: boolean;
    /** HBar-only presentation data; lifecycle ownership remains with the caller. */
    feedbackPhase?: string | null;
    feedbackBusy?: boolean;
    feedbackDescription?: string | null;
    feedbackStatus?: string | null;
    // Behavior
    onChange: (value: number) => void;
    debounceMs?: number;
    disabled?: boolean;
    // Compat with existing sliders
    unit?: string;
    shortcutHint?: string | null;
    title?: string | null;
    // Legacy alias for onChange
    onchange?: (value: number) => void;
    // Optional skin override
    skin?: Skin;
  }

  let {
    value,
    min,
    max,
    step,
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

  // Support both onChange and onchange (legacy)
  let effectiveOnChange = $derived(onChange ?? onchange ?? (() => {}));

  // Common props for all renderers
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

  // Knob-specific props
  let knobProps = $derived({
    ...commonProps,
    arcAngle,
    tickCount,
    tickLabels,
  });

  let discreteProps = $derived({
    ...commonProps,
    tickLabels,
    showAllTicks,
    tickStyle,
  });

  // This is intentionally not part of commonProps: skin and non-HBar renderer
  // contracts remain unchanged.
  let hbarProps = $derived({
    ...commonProps,
    optimistic,
    feedbackPhase,
    feedbackBusy,
    feedbackDescription,
    feedbackStatus,
  });

  // Resolve skin component for the current renderer type (undefined = use built-in)
  let skinComponent = $derived(
    skin
      ? renderer === 'knob' ? skin.knob
        : renderer === 'hbar' ? skin.hbar
        : renderer === 'bipolar' ? skin.bipolar
        : undefined
      : undefined
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
  <HBarRenderer {...hbarProps} />
{:else if renderer === 'bipolar'}
  <BipolarRenderer {...commonProps} />
{:else if renderer === 'knob'}
  <KnobRenderer {...knobProps} />
{:else if renderer === 'discrete'}
  <DiscreteRenderer {...discreteProps} />
{/if}
