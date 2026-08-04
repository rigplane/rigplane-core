<script lang="ts">
  import { untrack } from 'svelte';
  import './value-control.css';
  import {
    getCenterPercent,
    getBipolarFill,
    calculateClickValue,
    handleKeyboardStep,
    handleWheelStep,
    debounce,
    formatBipolarValue,
    clamp,
    snapToStep,
    valueToPosition,
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
    defaultValue = 0,
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
  let centerPercent = $derived(getCenterPercent(min, max));
  let currentPercent = $derived(valueToPosition(localValue, min, max) * 100);
  let bipolarFill = $derived(getBipolarFill(localValue, min, max));
  let effectiveFill = $derived(fillGradient
    ? `linear-gradient(90deg, ${fillGradient.join(', ')})`
    : (fillColor ?? accentColor));
  let displayValue = $derived(displayFn
    ? displayFn(localValue)
    : `${formatBipolarValue(localValue)}${unit ? '\u00a0' + unit : ''}`);
  
  // Absolute deviation from center for illuminated variant
  // 0 at center, 1.0 at either extreme
  let absDeviationRatio = $derived(Math.abs(localValue - defaultValue) / Math.max(Math.abs(max - defaultValue), Math.abs(min - defaultValue)));
  
  // Bipolar wheel: 1 step per tick by default (fine control).
  // Only scale up for very large ranges (>500 steps) to stay usable.
  let stepsInRange = $derived(Math.max(1, (max - min) / step));
  let adaptiveWheelMultiplier = $derived(stepsInRange > 500 ? Math.round(stepsInRange / 240) : 1);

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

    // Calculate value from click position
    const rect = containerEl.getBoundingClientRect();
    const newValue = calculateClickValue(e.clientX, rect.left, rect.width, min, max, step);
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
    const wheelMultiplier = e.shiftKey ? 1 : adaptiveWheelMultiplier;
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
    emitChange(defaultValue);
  }
</script>

<div
  class="vc-bipolar"
  class:compact
  class:disabled
  class:hardware={variant === 'hardware'}
  class:hw-illum={variant === 'hardware-illuminated'}
  bind:this={containerEl}
  data-shortcut-hint={shortcutHint ?? undefined}
  title={title ?? shortcutHint ?? undefined}
  style="--vc-accent: {accentColor}; --vc-fill-color: {effectiveFill}; --vc-track-color: {trackColor}; --vc-center: {centerPercent}%; --vc-fill-start: {bipolarFill.fillStart}%; --vc-fill-end: {bipolarFill.fillEnd}%; --vc-current: {currentPercent}%; --vc-abs-deviation: {absDeviationRatio};"
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
      <!-- 5-layer illuminated bipolar slider -->
      <div class="hil-frame" aria-hidden="true">
        <div class="hil-channel">
          <div class="hil-slot"></div>
          <div class="hil-center-mark"></div>
        </div>
      </div>
      <div class="hil-thumb" aria-hidden="true">
        <div class="hil-slit"></div>
      </div>
    {:else if variant === 'hardware'}
      <!-- Hardware bipolar with illuminated-style positioning -->
      <div class="hw-channel" aria-hidden="true">
        <div class="hw-slot"></div>
        <div class="hw-center-mark"></div>
      </div>
      <div class="hw-thumb" aria-hidden="true"></div>
    {:else}
      <div class="vc-track" aria-hidden="true">
        <div class="vc-track-base">
          <div class="vc-track-fill"></div>
          <div class="vc-track-center"></div>
        </div>
      </div>
      <div class="vc-thumb" aria-hidden="true"></div>
    {/if}
  </div>

  <div class="vc-axis" aria-hidden="true">
    <span class="axis-negative">-</span>
    <span class="axis-zero">0</span>
    <span class="axis-positive">+</span>
  </div>
</div>

<style>
  .vc-bipolar {
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

  .vc-track-base {
    position: absolute;
    inset-inline: 0;
    height: var(--vc-bar-height, 4px);
    border-radius: 999px;
    background: var(--vc-track-color, var(--v2-bg-gradient-start));
    overflow: hidden;
  }

  .compact .vc-track-base {
    height: var(--vc-bar-height-compact, 3px);
  }

  .vc-track-base::before {
    content: '';
    position: absolute;
    inset: 0;
    background: linear-gradient(
      90deg,
      var(--v2-bipolar-gradient-edge) 0%,
      var(--v2-bipolar-gradient-center) 50%,
      var(--v2-bipolar-gradient-edge) 100%
    );
  }

  .vc-track-fill {
    position: absolute;
    left: var(--vc-fill-start);
    width: calc(var(--vc-fill-end) - var(--vc-fill-start));
    height: 100%;
    background: var(--vc-fill-color);
    box-shadow: 0 0 0 1px color-mix(in srgb, var(--vc-accent) 6%, transparent);
  }

  .vc-track-center {
    position: absolute;
    left: calc(var(--vc-center) - 1px);
    top: 50%;
    width: 2px;
    height: var(--vc-center-height, 12px);
    transform: translateY(-50%);
    border-radius: 999px;
    background: color-mix(in srgb, var(--v2-text-light) 35%, var(--v2-bg-gradient-start));
    box-shadow: 0 0 0 1px var(--v2-bipolar-border);
  }

  .compact .vc-track-center {
    height: var(--vc-center-height-compact, 10px);
  }

  .vc-thumb {
    position: absolute;
    left: var(--vc-current);
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

  /* Illuminated slot (fills from center) */
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
      var(--vc-hw-slot-dark) 0%,
      var(--vc-hw-slot-edge) calc(var(--vc-fill-start) - 1%),
      color-mix(in srgb, var(--vc-illum-glow-primary) calc(28% + 0.47 * var(--vc-abs-deviation, 0.5) * 100%), transparent) var(--vc-fill-start),
      color-mix(in srgb, var(--vc-illum-glow-secondary) calc(38% + 0.95 * var(--vc-abs-deviation, 0.5) * 100%), transparent) calc(var(--vc-center)),
      color-mix(in srgb, var(--vc-illum-glow-primary) calc(28% + 0.47 * var(--vc-abs-deviation, 0.5) * 100%), transparent) var(--vc-fill-end),
      var(--vc-hw-slot-edge) calc(var(--vc-fill-end) + 1%),
      var(--vc-hw-slot-dark) 100%
    );
    box-shadow:
      0 0 7px color-mix(in srgb, var(--vc-illum-glow-primary) calc(18% + 0.55 * var(--vc-abs-deviation, 0.5) * 100%), transparent),
      0 0 14px color-mix(in srgb, var(--vc-illum-glow-primary) calc(9% + 0.28 * var(--vc-abs-deviation, 0.5) * 100%), transparent);
  }

  /* Center mark */
  .hardware .hw-center-mark {
    position: absolute;
    left: var(--vc-center);
    top: 0;
    bottom: 0;
    width: 2px;
    transform: translateX(-50%);
    background: var(--vc-hw-center-mark-bg);
    border-radius: 1px;
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
  }

  /* Volumetric thumb */
  .hardware .hw-thumb {
    position: absolute;
    left: var(--vc-current);
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

  /* Hardware axis labels */
  .hardware .vc-axis {
    color: var(--vc-hw-axis-color);
  }

  .hardware .axis-zero {
    color: var(--vc-hw-axis-zero-color);
  }

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

  /* Frame — color-mix с --vc-abs-deviation (как процент) */
  .hw-illum .hil-frame {
    position: absolute;
    inset: 2px 0;
    border-radius: 14px;
    border: 2px solid var(--vc-illum-frame-border);
    background: transparent;
    box-shadow:
      0 0 14px color-mix(in srgb, var(--vc-illum-glow-primary) calc(14% + 0.21 * var(--vc-abs-deviation, 0.5) * 100%), transparent),
      0 0 28px color-mix(in srgb, var(--vc-illum-glow-primary) calc(7% + 0.11 * var(--vc-abs-deviation, 0.5) * 100%), transparent),
      inset 0 0 10px color-mix(in srgb, var(--vc-illum-glow-primary) calc(6% + 0.09 * var(--vc-abs-deviation, 0.5) * 100%), transparent),
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

  /* Slot — fills from center, brightness scales with deviation */
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
      var(--vc-illum-slot-dark-sem) 0%,
      var(--vc-illum-slot-edge-sem) calc(var(--vc-fill-start) - 1%),
      color-mix(in srgb, var(--vc-illum-glow-primary) calc(28% + 0.47 * var(--vc-abs-deviation, 0.5) * 100%), transparent) var(--vc-fill-start),
      color-mix(in srgb, var(--vc-illum-glow-secondary) calc(38% + 0.95 * var(--vc-abs-deviation, 0.5) * 100%), transparent) calc(var(--vc-center)),
      color-mix(in srgb, var(--vc-illum-glow-primary) calc(28% + 0.47 * var(--vc-abs-deviation, 0.5) * 100%), transparent) var(--vc-fill-end),
      var(--vc-illum-slot-edge-sem) calc(var(--vc-fill-end) + 1%),
      var(--vc-illum-slot-dark-sem) 100%
    );
    box-shadow:
      0 0 7px color-mix(in srgb, var(--vc-illum-glow-primary) calc(18% + 0.55 * var(--vc-abs-deviation, 0.5) * 100%), transparent),
      0 0 14px color-mix(in srgb, var(--vc-illum-glow-primary) calc(9% + 0.28 * var(--vc-abs-deviation, 0.5) * 100%), transparent);
  }

  /* Center mark — always visible reference point */
  .hw-illum .hil-center-mark {
    position: absolute;
    left: calc(var(--vc-center) - 1px);
    top: 50%;
    width: 2px;
    height: 14px;
    transform: translateY(-50%);
    background: var(--vc-illum-center-mark-bg);
    box-shadow: 0 0 3px var(--vc-illum-center-mark-glow);
    border-radius: 1px;
  }

  /* Thumb — volumetric gradient + shadows */
  .hw-illum .hil-thumb {
    position: absolute;
    left: var(--vc-current);
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

  /* Slit — brightness scales with deviation */
  .hw-illum .hil-slit {
    position: absolute;
    top: 3px;
    bottom: 3px;
    left: 50%;
    width: var(--vc-illum-slit-width);
    transform: translateX(-50%);
    border-radius: 1px;
    background: color-mix(in srgb, var(--vc-illum-glow-secondary) calc(55% + 0.95 * var(--vc-abs-deviation, 0.5) * 100%), transparent);
    box-shadow:
      0 0 7px color-mix(in srgb, var(--vc-illum-glow-primary) calc(35% + 0.75 * var(--vc-abs-deviation, 0.5) * 100%), transparent),
      0 0 14px color-mix(in srgb, var(--vc-illum-glow-primary) calc(18% + 0.38 * var(--vc-abs-deviation, 0.5) * 100%), transparent);
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

  /* Hide standard axis for illuminated — use same hardware axis style */
  .hw-illum .vc-axis {
    color: var(--vc-illum-axis-color);
  }

  .hw-illum .axis-zero {
    color: var(--vc-illum-axis-zero-color);
  }

  .vc-axis {
    display: grid;
    grid-template-columns: 1fr 1fr 1fr;
    align-items: center;
    color: var(--v2-text-dimmer);
    font-size: 8px;
    letter-spacing: 0.06em;
    line-height: 1;
    user-select: none;
  }

  .compact .vc-axis {
    font-size: 7px;
  }

  .axis-negative {
    justify-self: start;
  }

  .axis-zero {
    justify-self: center;
    color: var(--v2-text-subtle);
  }

  .axis-positive {
    justify-self: end;
  }
</style>
