<script lang="ts">
  import { wheelControl } from '../wheel-control';
  import { onDestroy, untrack } from 'svelte';
  import '../value-control.css';
  import type {
    ContinuousScalarRendererLease,
    ContinuousScalarView,
  } from '../../../../primitives/scalar/continuous-scalar.svelte';
  import {
    valueToPosition, calculateArcPath, calculateIndicatorPosition,
    generateTickPositions, clamp, snapToStep,
  } from '../../../../primitives/scalar/value-control-core';
  import { projectScalarRenderPresentation } from '../scalar-render-presentation';
  import type { KnobSkinRendererProps } from '../skin';

  let { binding, label, displayFn, unknownDisplay,
    accentColor = '#00e5ff', showValue = true, showLabel = true, compact = false,
    arcAngle = 270, tickCount = 0, tickLabels = [],
    unit = '', shortcutHint = null, title = null, legacy,
  }: KnobSkinRendererProps = $props();

  const componentId = $props.id();
  const feedbackDescriptionId = componentId + '-feedback';
  const uid = componentId;
  let activePointer: {
    id: number; token: number; target: HTMLElement; startY: number; startValue: number;
  } | null = null;
  const initialBinding = untrack(() => binding);
  let attachedBinding = initialBinding;
  let lease: ContinuousScalarRendererLease = $state.raw(initialBinding.attachRenderer());

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
  let size = $derived(compact ? 52 : 68), cx = $derived(size / 2), cy = $derived(size / 2);
  let radius = $derived((size - 14) / 2), tw = $derived(compact ? 4 : 5);
  let position = $derived(view.domainValid && renderedValue !== null
    ? valueToPosition(renderedValue, view.domain.min, view.domain.max) : 0);
  let sa = $derived(-arcAngle / 2), ea = $derived(arcAngle / 2);
  let trackPath = $derived(calculateArcPath(cx, cy, radius, sa, ea));
  let fillPath = $derived(calculateArcPath(cx, cy, radius, sa, sa + position * arcAngle));
  let indEnd = $derived(calculateIndicatorPosition(
    cx, cy, radius - tw - 2, geometryValue, geometryMin, geometryMax, arcAngle,
  ));
  let ticks = $derived(tickCount > 0 ? generateTickPositions(cx, cy, radius + 2, radius + 6, tickCount, arcAngle) : []);
  let displayVal = $derived(renderedValue === null
    ? unknownDisplay ?? (displayFn ? displayFn(Number.NaN) : '—')
    : displayFn ? displayFn(renderedValue) : String(renderedValue) + (unit || ''));
  let renderPresentation = $derived(projectScalarRenderPresentation(view, legacy));

  function onDown(e: PointerEvent) {
    const token = lease.beginPointer();
    if (token === null || view.canonical === null) return;
    e.preventDefault();
    const target = e.currentTarget as HTMLElement;
    target.setPointerCapture(e.pointerId);
    activePointer = {
      id: e.pointerId, token, target, startY: e.clientY, startValue: view.canonical,
    };
  }
  function onMove(e: PointerEvent) {
    if (!activePointer || activePointer.id !== e.pointerId) return;
    const domain = view.domain;
    const dy = activePointer.startY - e.clientY;
    const sensitivity = e.shiftKey ? 12 : 0.5;
    const quantum = e.shiftKey ? domain.step / domain.fineStepDivisor : domain.step;
    lease.pointer(activePointer.token, clamp(snapToStep(
      activePointer.startValue + Math.round(dy / sensitivity) * quantum,
      quantum, domain.min,
    ), domain.min, domain.max));
  }
  function onUp(e: PointerEvent) {
    if (!activePointer || activePointer.id !== e.pointerId) return;
    if (activePointer.target.hasPointerCapture?.(e.pointerId)) {
      activePointer.target.releasePointerCapture(e.pointerId);
    }
    lease.endPointer(activePointer.token);
    activePointer = null;
  }
  function onCancel(e: PointerEvent) {
    if (!activePointer || activePointer.id !== e.pointerId) return;
    if (activePointer.target.hasPointerCapture?.(e.pointerId)) {
      activePointer.target.releasePointerCapture(e.pointerId);
    }
    lease.cancelPointer(activePointer.token);
    activePointer = null;
  }
  function onKey(e: KeyboardEvent) {
    if (lease.key({ key: e.key, fine: e.shiftKey })) e.preventDefault();
  }
  function onDbl() { lease.reset(); }
</script>

<div class="pro-knob" class:compact class:disabled={!view.editable}
  data-shortcut-hint={shortcutHint ?? undefined} title={title ?? shortcutHint ?? undefined}
  style="--pro-accent:{accentColor};--pro-size:{size}px;">
  {#if showLabel}<span class="pro-label">{label}</span>{/if}
  <div class="pro-ctr" role="slider" tabindex={view.editable ? 0 : -1}
    aria-label={label}
    aria-valuemin={view.domainValid ? view.domain.min : undefined}
    aria-valuemax={view.domainValid ? view.domain.max : undefined}
    aria-valuenow={view.domainValid ? view.canonical ?? undefined : undefined}
    aria-disabled={!view.editable}
    aria-busy={renderPresentation.attributes['aria-busy']}
    aria-describedby={renderPresentation.description !== null ? feedbackDescriptionId : undefined}
    data-command-phase={renderPresentation.attributes['data-command-phase'] ?? undefined}
    onpointerdown={onDown} onpointermove={onMove} onpointerup={onUp} onpointercancel={onCancel}
    use:wheelControl={{ view, lease }} onkeydown={onKey} ondblclick={onDbl}>
    <svg width={size} height={size} viewBox="0 0 {size} {size}" class="pro-svg">
      <defs>
        <radialGradient id="{uid}-b" cx="38%" cy="32%" r="68%"><stop offset="0%" stop-color="#3a4550"/><stop offset="50%" stop-color="#1c2428"/><stop offset="100%" stop-color="#0c1014"/></radialGradient>
        <radialGradient id="{uid}-r" cx="50%" cy="30%" r="70%"><stop offset="0%" stop-color="#4a5a68"/><stop offset="100%" stop-color="#1a2228"/></radialGradient>
        <filter id="{uid}-g"><feGaussianBlur stdDeviation="2" result="blur"/><feMerge><feMergeNode in="blur"/><feMergeNode in="SourceGraphic"/></feMerge></filter>
      </defs>
      <circle cx={cx} cy={cy} r={radius + 1} fill="none" stroke="url(#{uid}-r)" stroke-width="2"/>
      <path d={trackPath} fill="none" stroke="#1a2228" stroke-width={tw} stroke-linecap="round" opacity="0.8"/>
      {#if position > 0}<path d={fillPath} fill="none" stroke={accentColor} stroke-width={tw} stroke-linecap="round" filter="url(#{uid}-g)"/>{/if}
      {#each ticks as t}<line x1={t.x1} y1={t.y1} x2={t.x2} y2={t.y2} stroke="#3a4a58" stroke-width="1"/>{/each}
      <circle cx={cx} cy={cy} r={radius - tw - 1} fill="url(#{uid}-b)" stroke="#2a3640" stroke-width="0.5"/>
      <line x1={cx} y1={cy} x2={indEnd.x} y2={indEnd.y} stroke={accentColor} stroke-width="2.5" stroke-linecap="round" filter="url(#{uid}-g)"/>
      <circle cx={cx} cy={cy} r="3" fill="#0a0e12" stroke="#2a3640" stroke-width="0.5"/>
    </svg>
    {#if showValue}<div class="pro-val">{displayVal}</div>{/if}
  </div>
  {#if renderPresentation.description !== null}
    <span id={feedbackDescriptionId} class="sr-only">{renderPresentation.description}</span>
  {/if}
  {#if renderPresentation.status !== null}
    <span class="sr-only" role="status" aria-live="polite" aria-atomic="true"
      data-control-feedback-status>{renderPresentation.status}{renderPresentation.error === null ? '' : `: ${renderPresentation.error}`}</span>
  {/if}
  {#if tickLabels.length > 0}<div class="pro-ticks">{#each tickLabels as tl}<span class="pro-tick">{tl}</span>{/each}</div>{/if}
</div>

<style>
  .pro-knob { display: flex; flex-direction: column; align-items: center; gap: 4px; font-family: 'Roboto Mono', monospace; }
  .pro-label { color: var(--pro-accent, #00e5ff); font-size: 10px; text-align: center; text-transform: uppercase; letter-spacing: 0.06em; opacity: 0.85; }
  .compact .pro-label { font-size: 9px; }
  .disabled { opacity: 0.4; pointer-events: none; }
  .sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
  .pro-ctr { position: relative; width: var(--pro-size); height: var(--pro-size); cursor: grab; outline: none; touch-action: none; }
  .pro-ctr:active { cursor: grabbing; }
  .pro-ctr:focus-visible { outline: 2px solid var(--pro-accent, #00e5ff); outline-offset: 4px; border-radius: 50%; }
  .pro-svg { display: block; }
  .pro-val { position: absolute; bottom: -2px; left: 50%; transform: translateX(-50%); color: #e0f0ff; font-size: 10px; font-weight: 500; white-space: nowrap; }
  .compact .pro-val { font-size: 9px; bottom: 0; }
  .pro-ticks { display: flex; justify-content: space-between; width: 100%; padding: 0 2px; }
  .pro-tick { color: #4a5a68; font-size: 8px; }
</style>
