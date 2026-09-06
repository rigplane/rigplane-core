<script lang="ts">
  import { onDestroy, untrack } from 'svelte';
  import type {
    ContinuousScalarBinding,
    ContinuousScalarRendererLease,
    ContinuousScalarView,
  } from '../../../primitives/scalar/continuous-scalar.svelte';
  import './value-control.css';
  import {
    valueToPosition,
    calculateArcPath,
    calculateIndicatorPosition,
    generateTickPositions,
    clamp,
    snapToStep,
  } from '../../../primitives/scalar/value-control-core';
  import {
    projectScalarRenderPresentation,
    type LegacyReadingPresentation,
    type ScalarAccessibilityPresentation,
  } from './scalar-render-presentation';

  interface Props {
    binding: ContinuousScalarBinding;
    label: string;
    displayFn?: (v: number) => string;
    unknownDisplay?: string;
    fillColor?: string;
    fillGradient?: string[];
    trackColor?: string;
    accentColor?: string;
    showValue?: boolean;
    showLabel?: boolean;
    compact?: boolean;
    variant?: 'modern' | 'hardware' | 'hardware-illuminated';
    arcAngle?: number;
    tickCount?: number;
    tickLabels?: string[];
    unit?: string;
    shortcutHint?: string | null;
    title?: string | null;
    accessibility?: ScalarAccessibilityPresentation;
    legacy?: LegacyReadingPresentation;
  }

  let {
    binding,
    label,
    displayFn,
    unknownDisplay,
    fillColor,
    fillGradient,
    trackColor = 'var(--v2-bg-panel)',
    accentColor = 'var(--v2-accent-cyan)',
    showValue = true,
    showLabel = true,
    compact = false,
    variant = 'modern',
    arcAngle = 270,
    tickCount = 0,
    tickLabels = [],
    unit = '',
    shortcutHint = null,
    title = null,
    accessibility,
    legacy,
  }: Props = $props();

  const componentId = $props.id();
  const feedbackDescriptionId = componentId + '-feedback';
  const gradientId = componentId;
  let activePointer: {
    id: number;
    token: number;
    target: HTMLElement;
    startY: number;
    startValue: number;
  } | null = null;
  const initialBinding = untrack(() => binding);
  let attachedBinding = initialBinding;
  let lease: ContinuousScalarRendererLease = $state(initialBinding.attachRenderer());

  $effect(() => {
    if (binding === attachedBinding) return;
    if (activePointer?.target.hasPointerCapture?.(activePointer.id)) {
      activePointer.target.releasePointerCapture(activePointer.id);
    }
    activePointer = null;
    lease.dispose();
    attachedBinding = binding;
    lease = binding.attachRenderer();
  });
  onDestroy(() => {
    if (activePointer?.target.hasPointerCapture?.(activePointer.id)) {
      activePointer.target.releasePointerCapture(activePointer.id);
    }
    activePointer = null;
    lease.dispose();
  });

  let view = $state<ContinuousScalarView>(untrack(() => lease.view));
  let skipViewAssignment = true;
  $effect(() => {
    const next = lease.view;
    if (skipViewAssignment) skipViewAssignment = false;
    else view = next;
  });
  let renderedValue = $derived(view.displayed);
  let geometryMin = $derived(view.domainValid ? view.domain.min : 0);
  let geometryMax = $derived(view.domainValid ? view.domain.max : 1);
  let geometryValue = $derived(
    view.domainValid && renderedValue !== null ? renderedValue : geometryMin,
  );

  let size = $derived(compact ? 48 : 64);
  let cx = $derived(size / 2);
  let cy = $derived(size / 2);
  let radius = $derived((size - 12) / 2);
  let trackWidth = $derived(compact ? 4 : 5);
  let position = $derived(view.domainValid && renderedValue !== null
    ? valueToPosition(renderedValue, view.domain.min, view.domain.max) : 0);
  let startAngle = $derived(-arcAngle / 2);
  let endAngle = $derived(arcAngle / 2);
  let currentAngle = $derived(startAngle + position * arcAngle);

  let trackPath = $derived(calculateArcPath(cx, cy, radius, startAngle, endAngle));
  let fillPath = $derived(calculateArcPath(cx, cy, radius, startAngle, currentAngle));
  let indicatorEnd = $derived(calculateIndicatorPosition(
    cx, cy, radius - trackWidth - 2, geometryValue, geometryMin, geometryMax, arcAngle,
  ));
  let ticks = $derived(tickCount > 0
    ? generateTickPositions(cx, cy, radius + 2, radius + 6, tickCount, arcAngle)
    : []);

  let hasGradient = $derived(Boolean(fillGradient && fillGradient.length > 1));
  let safeFillGradient = $derived(fillGradient ?? []);
  let displayValue = $derived(renderedValue === null
    ? unknownDisplay ?? (displayFn ? displayFn(Number.NaN) : '—')
    : displayFn ? displayFn(renderedValue) : String(renderedValue) + (unit ? unit : ''));
  let renderPresentation = $derived(projectScalarRenderPresentation(view, legacy, accessibility));

  function handlePointerDown(e: PointerEvent) {
    const token = lease.beginPointer();
    if (token === null || view.canonical === null) return;

    e.preventDefault();
    const target = e.currentTarget as HTMLElement;
    target.setPointerCapture(e.pointerId);

    activePointer = {
      id: e.pointerId,
      token,
      target,
      startY: e.clientY,
      startValue: view.canonical,
    };
  }

  function handlePointerMove(e: PointerEvent) {
    if (!activePointer || activePointer.id !== e.pointerId) return;
    const deltaY = activePointer.startY - e.clientY;
    const sensitivity = e.shiftKey ? 12 : 0.5;
    const stepDelta = Math.round(deltaY / sensitivity);
    const domain = view.domain;
    const effectiveStep = e.shiftKey ? domain.step / domain.fineStepDivisor : domain.step;
    const newValue = clamp(
      snapToStep(activePointer.startValue + stepDelta * effectiveStep, effectiveStep, domain.min),
      domain.min,
      domain.max,
    );
    lease.pointer(activePointer.token, newValue);
  }

  function handlePointerUp(e: PointerEvent) {
    if (!activePointer || activePointer.id !== e.pointerId) return;
    if (activePointer.target.hasPointerCapture?.(e.pointerId)) {
      activePointer.target.releasePointerCapture(e.pointerId);
    }
    lease.endPointer(activePointer.token);
    activePointer = null;
  }

  function handlePointerCancel(e: PointerEvent) {
    if (!activePointer || activePointer.id !== e.pointerId) return;
    if (activePointer.target.hasPointerCapture?.(e.pointerId)) {
      activePointer.target.releasePointerCapture(e.pointerId);
    }
    lease.cancelPointer(activePointer.token);
    activePointer = null;
  }

  function handleWheel(e: WheelEvent) {
    if (!view.editable) return;
    e.preventDefault();
    lease.wheel({ direction: e.deltaY > 0 ? -1 : 1, fine: e.shiftKey });
  }

  function handleKeyDown(e: KeyboardEvent) {
    if (lease.key({ key: e.key, fine: e.shiftKey })) e.preventDefault();
  }

  function handleDoubleClick() { lease.reset(); }
</script>

<div
  class="vc-knob"
  class:compact
  class:disabled={!view.editable}
  class:hardware={variant === 'hardware'}
  data-shortcut-hint={shortcutHint ?? undefined}
  title={title ?? shortcutHint ?? undefined}
  style="--vc-accent: {accentColor}; --vc-knob-size: {size}px;"
>
  {#if showLabel}
    <span class="vc-label">{label}</span>
  {/if}

  <div
    class="vc-knob-container"
    role="slider"
    tabindex={view.editable ? 0 : -1}
    aria-label={label}
    aria-valuemin={view.domainValid ? view.domain.min : undefined}
    aria-valuemax={view.domainValid ? view.domain.max : undefined}
    aria-valuenow={view.domainValid ? view.canonical ?? undefined : undefined}
    aria-valuetext={accessibility?.valueText?.trim() ? accessibility.valueText : undefined}
    aria-disabled={!view.editable}
    aria-busy={renderPresentation.attributes['aria-busy']}
    aria-describedby={renderPresentation.description !== null ? feedbackDescriptionId : undefined}
    data-command-phase={renderPresentation.attributes['data-command-phase'] ?? undefined}
    onpointerdown={handlePointerDown}
    onpointermove={handlePointerMove}
    onpointerup={handlePointerUp}
    onpointercancel={handlePointerCancel}
    onwheel={handleWheel}
    onkeydown={handleKeyDown}
    ondblclick={handleDoubleClick}
  >
    <svg
      width={size}
      height={size}
      viewBox="0 0 {size} {size}"
      class="vc-knob-svg"
    >
      {#if hasGradient || variant === 'hardware'}
        <defs>
          {#if hasGradient}
            <linearGradient id={gradientId} x1="0%" y1="0%" x2="100%" y2="0%">
              {#each safeFillGradient as color, i}
                <stop offset="{(i / (safeFillGradient.length - 1)) * 100}%" stop-color={color} />
              {/each}
            </linearGradient>
          {/if}
          {#if variant === 'hardware'}
            <!-- Radial gradient for knob cap — convex dark material feel -->
            <radialGradient id="hw-body-{gradientId}" cx="40%" cy="35%" r="65%">
              <stop offset="0%" stop-color="#2c3840" />
              <stop offset="60%" stop-color="#161e24" />
              <stop offset="100%" stop-color="#0a0e12" />
            </radialGradient>
          {/if}
        </defs>
      {/if}

      <!-- Track arc -->
      <path
        d={trackPath}
        fill="none"
        stroke={trackColor}
        stroke-width={trackWidth}
        stroke-linecap="round"
        class="vc-knob-track"
      />

      <!-- Fill arc -->
      {#if position > 0}
        <path
          d={fillPath}
          fill="none"
          stroke={hasGradient ? `url(#${gradientId})` : (fillColor ?? accentColor)}
          stroke-width={trackWidth}
          stroke-linecap="round"
          class="vc-knob-fill"
        />
      {/if}

      <!-- Tick marks -->
      {#each ticks as tick}
        <line
          x1={tick.x1}
          y1={tick.y1}
          x2={tick.x2}
          y2={tick.y2}
          stroke="var(--v2-text-disabled)"
          stroke-width="1"
          class="vc-knob-tick"
        />
      {/each}

      <!-- Hardware: knob body cap — sits inside the track ring, above ticks -->
      {#if variant === 'hardware'}
        <circle
          cx={cx}
          cy={cy}
          r={radius - trackWidth - 1}
          fill="url(#hw-body-{gradientId})"
          stroke="#1e2830"
          stroke-width="1"
          class="vc-knob-body"
        />
      {/if}

      <!-- Indicator line -->
      <line
        x1={cx}
        y1={cy}
        x2={indicatorEnd.x}
        y2={indicatorEnd.y}
        stroke="var(--v2-text-white)"
        stroke-width="2"
        stroke-linecap="round"
        class="vc-knob-indicator"
      />

      <!-- Center dot -->
      <circle
        cx={cx}
        cy={cy}
        r="3"
        fill="var(--v2-bg-gradient-panel)"
        stroke="var(--v2-text-disabled)"
        stroke-width="1"
        class="vc-knob-center"
      />
    </svg>

    {#if showValue}
      <div class="vc-knob-value">{displayValue}</div>
    {/if}
  </div>

  {#if renderPresentation.description !== null}
    <span id={feedbackDescriptionId} class="sr-only">{renderPresentation.description}</span>
  {/if}
  {#if renderPresentation.status !== null}
    <span
      class="sr-only"
      role="status"
      aria-live="polite"
      aria-atomic="true"
      data-control-feedback-status
    >{renderPresentation.status}{renderPresentation.error === null ? '' : `: ${renderPresentation.error}`}</span>
  {/if}

  {#if tickLabels.length > 0}
    <div class="vc-tick-labels">
      {#each tickLabels as tickLabel}
        <span class="vc-tick-label">{tickLabel}</span>
      {/each}
    </div>
  {/if}
</div>

<style>
  .vc-knob {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 4px;
    font-family: 'Roboto Mono', monospace;
  }

  .vc-label {
    color: var(--vc-text-label, var(--v2-text-dim));
    font-size: 10px;
    text-align: center;
  }

  .compact .vc-label {
    font-size: 9px;
  }

  .disabled {
    opacity: 0.4;
    pointer-events: none;
  }

  .sr-only {
    position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px;
    overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0;
  }

  .vc-knob-container {
    position: relative;
    width: var(--vc-knob-size);
    height: var(--vc-knob-size);
    cursor: grab;
    outline: none;
    touch-action: none;
  }

  .vc-knob-container:active {
    cursor: grabbing;
  }

  .vc-knob-container:focus-visible {
    outline: var(--vc-focus-ring-width, 2px) solid var(--vc-focus-ring);
    outline-offset: 4px;
    border-radius: 50%;
  }

  .vc-knob-svg {
    display: block;
  }

  .vc-knob-track {
    opacity: 0.6;
  }

  .vc-knob-fill {
    filter: drop-shadow(0 0 2px color-mix(in srgb, var(--vc-accent) 28%, transparent));
  }

  .vc-knob-indicator {
    filter: drop-shadow(0 0 2px var(--v2-knob-shadow));
  }

  .vc-knob-value {
    position: absolute;
    bottom: -2px;
    left: 50%;
    transform: translateX(-50%);
    color: var(--vc-text-value, var(--v2-text-bright));
    font-size: 10px;
    font-weight: 500;
    white-space: nowrap;
  }

  .compact .vc-knob-value {
    font-size: 9px;
    bottom: 0;
  }

  .vc-tick-labels {
    display: flex;
    justify-content: space-between;
    width: 100%;
    padding: 0 2px;
  }

  .vc-tick-label {
    color: var(--v2-text-dimmer);
    font-size: 8px;
  }

  /* ── Hardware variant ─────────────────────────────────────────────────── */

  /* Recessed track arc — thin engraved groove */
  .hardware .vc-knob-track {
    opacity: 1;
    stroke-width: 3;
  }

  /* Flat muted fill — no glow, slightly dim */
  .hardware .vc-knob-fill {
    filter: none;
    opacity: 0.85;
    stroke-width: 3;
  }

  /* Tick marks more visible, outside body cap */
  .hardware .vc-knob-tick {
    opacity: 0.65;
  }

  /* Ivory pointer — stronger, warmer than modern */
  .hardware .vc-knob-indicator {
    filter: none;
    stroke: #e8dcc8;
    stroke-width: 3;
  }

  /* Center pivot — darker well to anchor the pointer */
  .hardware .vc-knob-center {
    fill: #0a0c10;
    stroke: #2a3440;
  }

  /* Warmer/brighter label text */
  .hardware .vc-label {
    color: #8a9e78;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    font-size: 9px;
  }

  .hardware .vc-knob-value {
    color: #c8d8a8;
  }

  .hardware .vc-tick-label {
    color: #728062;
  }
</style>
