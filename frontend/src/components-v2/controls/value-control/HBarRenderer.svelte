<script lang="ts">
  import { untrack } from 'svelte';
  import './value-control.css';
  import {
    getFillPercent,
    calculateClickValue,
    calculateDragValue,
    handleKeyboardStep,
    handleWheelStep,
    debounce,
    clamp,
    snapToStep,
  } from './value-control-core';

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
    trackColor = 'var(--v2-bg-gradient-start)',
    accentColor = 'var(--v2-accent-cyan)',
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
  }: Props = $props();

  let containerEl: HTMLDivElement | null = $state(null);
  let isDragging = $state(false);
  let wheelLocked = $state(false);
  let wheelUnlockTimer: ReturnType<typeof setTimeout> | null = null;
  let dragStartValue = $state(0);
  let dragStartX = $state(0);
  let localValue = $state(untrack(() => value));

  function markWheelActive() {
    wheelLocked = true;
    if (wheelUnlockTimer) clearTimeout(wheelUnlockTimer);
    wheelUnlockTimer = setTimeout(() => {
      wheelLocked = false;
      wheelUnlockTimer = null;
    }, 300);
  }

  // Sync from parent value ONLY when idle (no drag, no wheel)
  let prevValue = untrack(() => value);
  $effect(() => {
    const v = value;
    if (v !== prevValue) {
      prevValue = v;
      if (!isDragging && !wheelLocked) {
        localValue = v;
      }
    }
  });

  // Derived values
  let fillPercent = $derived(getFillPercent(localValue, min, max));
  let effectiveDefault = $derived(defaultValue ?? min);
  let effectiveFill = $derived(fillGradient
    ? `linear-gradient(90deg, ${fillGradient.join(', ')})`
    : (fillColor ?? accentColor));
  let displayValue = $derived(displayFn ? displayFn(localValue) : `${localValue}${unit ? '\u00a0' + unit : ''}`);
  
  // Adaptive wheel multiplier based on range (normalize to ~255 baseline)
  let adaptiveWheelMultiplier = $derived(Math.max(1, Math.ceil((max - min) / 255)));

  // Debounced change handler
  let debouncedOnChange = $derived.by<(...args: unknown[]) => void>(() => {
    if (debounceMs > 0) {
      return debounce((v: number) => onChange(v), debounceMs) as (...args: unknown[]) => void;
    }
    return ((v: number) => onChange(v)) as (...args: unknown[]) => void;
  });

  function emitChange(newValue: number, immediate = false) {
    if (newValue !== localValue) {
      localValue = newValue;
    }
    if (newValue !== value) {
      if (immediate) {
        onChange(newValue);
      } else {
        debouncedOnChange(newValue);
      }
    }
  }

  function handlePointerDown(e: PointerEvent) {
    if (disabled || !containerEl) return;

    e.preventDefault();
    const target = e.currentTarget as HTMLElement;
    target.setPointerCapture(e.pointerId);

    isDragging = true;
    dragStartX = e.clientX;

    // Calculate value from click position
    const rect = containerEl.getBoundingClientRect();
    const newValue = calculateClickValue(e.clientX, rect.left, rect.width, min, max, step);
    dragStartValue = newValue;
    emitChange(newValue, true);
  }

  function handlePointerMove(e: PointerEvent) {
    if (!isDragging || disabled || !containerEl) return;

    const rect = containerEl.getBoundingClientRect();
    const newValue = calculateClickValue(e.clientX, rect.left, rect.width, min, max, step);
    emitChange(newValue, true);
  }

  function handlePointerUp(e: PointerEvent) {
    if (!isDragging) return;

    const target = e.currentTarget as HTMLElement;
    target.releasePointerCapture(e.pointerId);
    isDragging = false;
  }

  function handleWheel(e: WheelEvent) {
    if (disabled) return;
    e.preventDefault();

    // Use adaptive multiplier for consistent scroll speed across ranges
    const wheelMultiplier = e.shiftKey ? 1 : (4 * adaptiveWheelMultiplier);
    const effectiveStep = e.shiftKey ? step / fineStepDivisor : step * wheelMultiplier;
    const direction = e.deltaY > 0 ? -1 : 1;
    const newValue = clamp(
      snapToStep(localValue + direction * effectiveStep, effectiveStep, min),
      min,
      max,
    );
    localValue = newValue;
    markWheelActive();
    onChange(newValue);
  }

  function handleKeyDown(e: KeyboardEvent) {
    if (disabled) return;

    const newValue = handleKeyboardStep(localValue, e.key, step, fineStepDivisor, min, max, e.shiftKey);
    if (newValue !== null) {
      e.preventDefault();
      emitChange(newValue);
    }
  }

  function handleDoubleClick() {
    if (disabled) return;
    emitChange(effectiveDefault);
  }
</script>

<div
  class="vc-hbar"
  class:compact
  class:disabled
  class:hardware={variant === 'hardware'}
  class:hw-illum={variant === 'hardware-illuminated'}
  bind:this={containerEl}
  data-shortcut-hint={shortcutHint ?? undefined}
  title={title ?? shortcutHint ?? undefined}
  style="--vc-accent: {accentColor}; --vc-fill-color: {effectiveFill}; --vc-track-color: {trackColor}; --vc-fill-percent: {fillPercent}%; --vc-fill-ratio: {fillPercent / 100};"
>
  {#if showLabel || showValue}
    <div class="vc-header">
      {#if showLabel}
        <span class="vc-label">{label}</span>
      {/if}
      {#if showValue}
        <span class="vc-value">{displayValue}</span>
      {/if}
    </div>
  {/if}

  <div
    class="vc-track-container"
    role="slider"
    tabindex={disabled ? -1 : 0}
    aria-label={label}
    aria-valuemin={min}
    aria-valuemax={max}
    aria-valuenow={value}
    aria-disabled={disabled}
    onpointerdown={handlePointerDown}
    onpointermove={handlePointerMove}
    onpointerup={handlePointerUp}
    onpointercancel={handlePointerUp}
    onwheel={handleWheel}
    onkeydown={handleKeyDown}
    ondblclick={handleDoubleClick}
  >
    {#if variant === 'hardware-illuminated'}
      <!-- 5-layer illuminated slider -->
      <div class="hil-frame" aria-hidden="true">
        <div class="hil-channel">
          <div class="hil-slot"></div>
        </div>
      </div>
      <div class="hil-thumb" aria-hidden="true">
        <div class="hil-slit"></div>
      </div>
    {:else if variant === 'hardware'}
      <!-- Hardware slider with illuminated-style positioning -->
      <div class="hw-channel" aria-hidden="true">
        <div class="hw-slot"></div>
      </div>
      <div class="hw-thumb" aria-hidden="true"></div>
    {:else}
      <div class="vc-track" aria-hidden="true">
        <div class="vc-track-base"></div>
        <div class="vc-track-fill"></div>
      </div>
      <div class="vc-thumb" aria-hidden="true"></div>
    {/if}
  </div>
</div>

<style>
  .vc-hbar {
    display: flex;
    flex-direction: column;
    gap: var(--vc-gap, 3px);
    width: 100%;
    font-family: 'Roboto Mono', monospace;
  }

  .vc-header {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    font-size: 10px;
    line-height: 1.4;
  }

  .compact .vc-header {
    font-size: var(--vc-label-size);
  }

  .vc-label {
    color: var(--vc-text-label, var(--v2-text-dim));
    text-align: left;
  }

  .vc-value {
    color: var(--vc-text-value, var(--v2-text-bright));
    font-family: 'Roboto Mono', monospace;
  }

  .disabled {
    opacity: 0.4;
    pointer-events: none;
  }

  .vc-track-container {
    position: relative;
    display: flex;
    align-items: center;
    min-height: 16px;
    cursor: pointer;
    outline: none;
    touch-action: none;
    isolation: isolate;
    overflow: hidden;
  }

  .compact .vc-track-container {
    min-height: 14px;
  }

  .vc-track-container:focus-visible {
    outline: var(--vc-focus-ring-width, 2px) solid var(--vc-focus-ring);
    outline-offset: 3px;
    border-radius: 2px;
  }

  .vc-track {
    position: absolute;
    inset: 0;
    display: flex;
    align-items: center;
    pointer-events: none;
  }

  .vc-track-base,
  .vc-track-fill {
    position: absolute;
    border-radius: 999px;
  }

  .vc-track-base {
    inset-inline: 0;
    height: var(--vc-bar-height, 4px);
    background: var(--vc-track-color, var(--v2-bg-gradient-start));
  }

  .compact .vc-track-base {
    height: var(--vc-bar-height-compact, 3px);
  }

  .vc-track-fill {
    left: 0;
    width: var(--vc-fill-percent);
    height: var(--vc-bar-height, 4px);
    background: var(--vc-fill-color);
    box-shadow: 0 0 0 1px color-mix(in srgb, var(--vc-accent) 6%, transparent);
  }

  .compact .vc-track-fill {
    height: var(--vc-bar-height-compact, 3px);
  }

  .vc-thumb {
    position: absolute;
    left: var(--vc-fill-percent);
    transform: translateX(-50%);
    width: var(--vc-thumb-size, 10px);
    height: var(--vc-thumb-size, 10px);
    border-radius: 2px;
    background: var(--v2-text-white);
    border: 1px solid var(--vc-accent);
    pointer-events: none;
    box-shadow: 0 0 0 1px color-mix(in srgb, var(--vc-accent) 5%, transparent);
    transition: box-shadow var(--vc-transition-fast);
  }

  .compact .vc-thumb {
    width: var(--vc-thumb-size-compact, 7px);
    height: var(--vc-thumb-size-compact, 7px);
  }

  .vc-track-container:hover .vc-thumb {
    box-shadow: 0 0 0 2px color-mix(in srgb, var(--vc-accent) 10%, transparent);
  }

  /* ── Hardware variant (illuminated-style structure) ──────────────────── */

  .hardware .vc-track-container {
    min-height: var(--vc-control-height);
  }

  /* Recessed groove */
  .hardware .hw-channel {
    position: absolute;
    inset: var(--vc-channel-inset);
    border-radius: var(--vc-channel-radius);
    background: var(--vc-hw-channel-bg);
    box-shadow:
      inset 0 2px 4px var(--vc-hw-channel-shadow-1),
      inset 0 -1px 2px var(--vc-hw-channel-shadow-2),
      inset 0 0 0 1px var(--vc-hw-channel-border);
  }

  /* Illuminated slot */
  .hardware .hw-slot {
    position: absolute;
    top: 50%;
    left: var(--vc-slot-inset-x);
    right: var(--vc-slot-inset-x);
    height: var(--vc-illum-slot-height);
    transform: translateY(-50%);
    border-radius: var(--vc-slot-radius);
    background: linear-gradient(
      90deg,
      color-mix(in srgb, var(--vc-illum-glow-primary) calc(28% + 0.47 * var(--vc-fill-ratio, 0.5) * 100%), transparent) 0%,
      color-mix(in srgb, var(--vc-illum-glow-secondary) calc(38% + 0.95 * var(--vc-fill-ratio, 0.5) * 100%), transparent) var(--vc-fill-percent),
      var(--vc-hw-slot-edge) calc(var(--vc-fill-percent) + 2%),
      var(--vc-hw-slot-dark) 100%
    );
    box-shadow:
      0 0 7px color-mix(in srgb, var(--vc-illum-glow-primary) calc(18% + 0.55 * var(--vc-fill-ratio, 0.5) * 100%), transparent),
      0 0 14px color-mix(in srgb, var(--vc-illum-glow-primary) calc(9% + 0.28 * var(--vc-fill-ratio, 0.5) * 100%), transparent);
  }



  /* Calibration scale marks — double frequency + incandescent glow */
  .hardware .vc-track-container::after {
    content: '';
    position: absolute;
    inset-inline: 0;
    bottom: 0px;
    height: var(--vc-tick-height);
    pointer-events: none;
    /* Fine tick every 6px (was 12px), major every 30px (was 60px) */
    background:
      repeating-linear-gradient(
        90deg,
        color-mix(in srgb, var(--vc-hw-tick-color) calc(var(--vc-hw-tick-major-opacity) * 100%), transparent) 0px,
        color-mix(in srgb, var(--vc-hw-tick-color) calc(var(--vc-hw-tick-major-opacity) * 100%), transparent) var(--vc-tick-major-width),
        transparent var(--vc-tick-major-width),
        transparent var(--vc-tick-major-spacing)
      ),
      repeating-linear-gradient(
        90deg,
        color-mix(in srgb, var(--vc-hw-tick-color) calc(var(--vc-hw-tick-minor-opacity) * 100%), transparent) 0px,
        color-mix(in srgb, var(--vc-hw-tick-color) calc(var(--vc-hw-tick-minor-opacity) * 100%), transparent) var(--vc-tick-minor-width),
        transparent var(--vc-tick-minor-width),
        transparent var(--vc-tick-minor-spacing)
      );
    filter: drop-shadow(0 0 2px color-mix(in srgb, var(--vc-hw-tick-color) calc(var(--vc-hw-tick-glow-opacity) * 100%), transparent));
    border-radius: 0 0 1px 1px;
  }

  /* Volumetric thumb */
  .hardware .hw-thumb {
    position: absolute;
    left: var(--vc-fill-percent);
    top: 50%;
    transform: translate(-50%, -50%);
    width: var(--vc-illum-thumb-width);
    height: var(--vc-illum-thumb-height);
    border-radius: 3px;
    background: linear-gradient(
      to right,
      var(--vc-illum-thumb-grad-dark) 0%,
      var(--vc-illum-thumb-grad-mid-1) 8%,
      var(--vc-illum-thumb-grad-mid-2) 20%,
      var(--vc-illum-thumb-grad-bright-1) 35%,
      var(--vc-illum-thumb-grad-bright-2) 45%,
      var(--vc-illum-thumb-grad-center) 50%,
      var(--vc-illum-thumb-grad-bright-2) 55%,
      var(--vc-illum-thumb-grad-bright-1) 65%,
      var(--vc-illum-thumb-grad-mid-2) 80%,
      var(--vc-illum-thumb-grad-mid-1) 92%,
      var(--vc-illum-thumb-grad-dark) 100%
    );
    border: 1px solid var(--vc-illum-thumb-border);
    box-shadow:
      var(--vc-illum-thumb-shadow-deep),
      var(--vc-illum-thumb-shadow-mid),
      var(--vc-illum-thumb-shadow-close),
      var(--vc-illum-thumb-highlight-top),
      var(--vc-illum-thumb-shadow-bottom),
      var(--vc-illum-thumb-highlight-sides),
      calc(var(--vc-illum-thumb-highlight-sides) * -1),
      var(--vc-illum-thumb-outline);
  }

  .hardware .vc-track-container:hover .hw-thumb {
    box-shadow:
      var(--vc-illum-thumb-shadow-deep),
      var(--vc-illum-thumb-shadow-mid),
      var(--vc-illum-thumb-shadow-close),
      var(--vc-illum-thumb-hover-highlight-top),
      var(--vc-illum-thumb-shadow-bottom),
      var(--vc-illum-thumb-hover-highlight-sides),
      calc(var(--vc-illum-thumb-hover-highlight-sides) * -1),
      var(--vc-illum-thumb-outline),
      var(--vc-illum-thumb-hover-ring);
  }

  /* Warmer/brighter label text in hardware context */
  .hardware .vc-label {
    color: var(--vc-hw-label-color, var(--vc-text-label));
    letter-spacing: var(--vc-label-tracking);
    text-transform: uppercase;
    font-size: var(--vc-label-size);
  }

  .hardware .vc-value {
    color: var(--vc-hw-value-color, var(--vc-text-value));
  }

  /* ── Hardware Illuminated variant (5-layer) ──────────────────────────── */

  .hw-illum .vc-label {
    color: var(--vc-illum-label-color, var(--vc-theme-label-color));
    letter-spacing: var(--vc-label-tracking);
    text-transform: uppercase;
    font-size: var(--vc-label-size);
  }

  .hw-illum .vc-value {
    color: var(--vc-illum-value-color, var(--vc-theme-value-color));
  }

  .hw-illum .vc-track-container {
    min-height: var(--vc-control-height);
  }

  /* Frame — используем color-mix для динамической яркости */
  .hw-illum .hil-frame {
    position: absolute;
    inset: 2px 0;
    border-radius: 14px;
    border: 2px solid var(--vc-illum-frame-border);
    background: transparent;
    box-shadow:
      0 0 14px color-mix(in srgb, var(--vc-illum-glow-primary) calc(14% + 0.21 * var(--vc-fill-ratio, 0.5) * 100%), transparent),
      0 0 28px color-mix(in srgb, var(--vc-illum-glow-primary) calc(7% + 0.11 * var(--vc-fill-ratio, 0.5) * 100%), transparent),
      inset 0 0 10px color-mix(in srgb, var(--vc-illum-glow-primary) calc(6% + 0.09 * var(--vc-fill-ratio, 0.5) * 100%), transparent),
      inset 0 1px 0 var(--vc-illum-frame-inset-highlight),
      inset 0 -1px 0 var(--vc-illum-frame-inset-shadow);
    pointer-events: none;
  }

  /* Channel */
  .hw-illum .hil-channel {
    position: absolute;
    inset: var(--vc-channel-inset);
    border-radius: var(--vc-channel-radius);
    background: var(--vc-illum-channel-bg-sem);
    box-shadow:
      inset 0 2px 4px var(--vc-illum-channel-shadow-1-sem),
      inset 0 -1px 2px var(--vc-illum-channel-shadow-2-sem),
      inset 0 0 0 1px var(--vc-illum-channel-border-sem);
  }

  /* Slot — color-mix для динамической яркости */
  .hw-illum .hil-slot {
    position: absolute;
    top: 50%;
    left: var(--vc-slot-inset-x);
    right: var(--vc-slot-inset-x);
    height: var(--vc-illum-slot-height);
    transform: translateY(-50%);
    border-radius: var(--vc-slot-radius);
    background: linear-gradient(
      90deg,
      color-mix(in srgb, var(--vc-illum-glow-primary) calc(28% + 0.47 * var(--vc-fill-ratio, 0.5) * 100%), transparent) 0%,
      color-mix(in srgb, var(--vc-illum-glow-secondary) calc(38% + 0.95 * var(--vc-fill-ratio, 0.5) * 100%), transparent) var(--vc-fill-percent),
      var(--vc-illum-slot-edge-sem) calc(var(--vc-fill-percent) + 2%),
      var(--vc-illum-slot-dark-sem) 100%
    );
    box-shadow:
      0 0 7px color-mix(in srgb, var(--vc-illum-glow-primary) calc(18% + 0.55 * var(--vc-fill-ratio, 0.5) * 100%), transparent),
      0 0 14px color-mix(in srgb, var(--vc-illum-glow-primary) calc(9% + 0.28 * var(--vc-fill-ratio, 0.5) * 100%), transparent);
  }

  /* Thumb — volumetric gradient + shadows */
  .hw-illum .hil-thumb {
    position: absolute;
    left: var(--vc-fill-percent);
    top: 50%;
    transform: translate(-50%, -50%);
    width: var(--vc-illum-thumb-width);
    height: var(--vc-illum-thumb-height);
    border-radius: 3px;
    pointer-events: none;
    background: linear-gradient(
      to right,
      var(--vc-illum-thumb-grad-dark) 0%,
      var(--vc-illum-thumb-grad-mid-1) 8%,
      var(--vc-illum-thumb-grad-mid-2) 20%,
      var(--vc-illum-thumb-grad-bright-1) 35%,
      var(--vc-illum-thumb-grad-bright-2) 45%,
      var(--vc-illum-thumb-grad-center) 50%,
      var(--vc-illum-thumb-grad-bright-2) 55%,
      var(--vc-illum-thumb-grad-bright-1) 65%,
      var(--vc-illum-thumb-grad-mid-2) 80%,
      var(--vc-illum-thumb-grad-mid-1) 92%,
      var(--vc-illum-thumb-grad-dark) 100%
    );
    border: 1px solid var(--vc-illum-thumb-border);
    box-shadow:
      var(--vc-illum-thumb-shadow-deep),
      var(--vc-illum-thumb-shadow-mid),
      var(--vc-illum-thumb-shadow-close),
      var(--vc-illum-thumb-highlight-top),
      var(--vc-illum-thumb-shadow-bottom),
      var(--vc-illum-thumb-highlight-sides),
      calc(var(--vc-illum-thumb-highlight-sides) * -1),
      var(--vc-illum-thumb-outline);
  }

  /* Slit — color-mix для динамической яркости */
  .hw-illum .hil-slit {
    position: absolute;
    top: 3px;
    bottom: 3px;
    left: 50%;
    width: var(--vc-illum-slit-width);
    transform: translateX(-50%);
    border-radius: 1px;
    background: color-mix(in srgb, var(--vc-illum-glow-secondary) calc(55% + 0.95 * var(--vc-fill-ratio, 0.5) * 100%), transparent);
    box-shadow:
      0 0 7px color-mix(in srgb, var(--vc-illum-glow-primary) calc(35% + 0.75 * var(--vc-fill-ratio, 0.5) * 100%), transparent),
      0 0 14px color-mix(in srgb, var(--vc-illum-glow-primary) calc(18% + 0.38 * var(--vc-fill-ratio, 0.5) * 100%), transparent);
  }

  .hw-illum .vc-track-container:hover .hil-thumb {
    box-shadow:
      var(--vc-illum-thumb-shadow-deep),
      var(--vc-illum-thumb-shadow-mid),
      var(--vc-illum-thumb-shadow-close),
      var(--vc-illum-thumb-hover-highlight-top),
      var(--vc-illum-thumb-shadow-bottom),
      var(--vc-illum-thumb-hover-highlight-sides),
      calc(var(--vc-illum-thumb-hover-highlight-sides) * -1),
      var(--vc-illum-thumb-outline),
      var(--vc-illum-thumb-hover-ring);
  }
</style>
