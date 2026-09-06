<script lang="ts">
  import '../value-control.css';
  import { valueToPosition, calculateArcPath, calculateIndicatorPosition, generateTickPositions, handleKeyboardStep, debounce, clamp, snapToStep } from '../../../../primitives/scalar/value-control-core';
  import type { KnobSkinRendererProps } from '../skin';

  let { value, min, max, step, defaultValue, fineStepDivisor = 10, label, displayFn,
    accentColor = '#00e5ff', showValue = true, showLabel = true, compact = false,
    arcAngle = 270, tickCount = 0, tickLabels = [], onChange, debounceMs = 0,
    disabled = false, unit = '', shortcutHint = null, title = null,
  }: KnobSkinRendererProps = $props();

  let isDragging = $state(false), dragStartY = $state(0), dragStartValue = $state(0);
  let size = $derived(compact ? 52 : 68), cx = $derived(size / 2), cy = $derived(size / 2);
  let radius = $derived((size - 14) / 2), tw = $derived(compact ? 4 : 5);
  let position = $derived(valueToPosition(value, min, max));
  let sa = $derived(-arcAngle / 2), ea = $derived(arcAngle / 2);
  let trackPath = $derived(calculateArcPath(cx, cy, radius, sa, ea));
  let fillPath = $derived(calculateArcPath(cx, cy, radius, sa, sa + position * arcAngle));
  let indEnd = $derived(calculateIndicatorPosition(cx, cy, radius - tw - 2, value, min, max, arcAngle));
  let ticks = $derived(tickCount > 0 ? generateTickPositions(cx, cy, radius + 2, radius + 6, tickCount, arcAngle) : []);
  let uid = `pro-${Math.random().toString(36).slice(2)}`;
  let displayVal = $derived(displayFn ? displayFn(value) : `${value}${unit || ''}`);
  let dbc = $derived.by<(v: number) => void>(() => debounceMs > 0 ? debounce(onChange, debounceMs) : onChange);

  function emit(v: number, now = false) { if (v !== value) { now ? onChange(v) : dbc(v); } }

  function onDown(e: PointerEvent) {
    if (disabled) return; e.preventDefault();
    (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId);
    isDragging = true; dragStartY = e.clientY; dragStartValue = value;
  }
  function onMove(e: PointerEvent) {
    if (!isDragging || disabled) return;
    const dy = dragStartY - e.clientY, s = e.shiftKey ? 12 : 0.5, ef = e.shiftKey ? step / fineStepDivisor : step;
    emit(clamp(snapToStep(dragStartValue + Math.round(dy / s) * ef, ef, min), min, max), true);
  }
  function onUp(e: PointerEvent) { if (!isDragging) return; (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId); isDragging = false; }
  function onWheel(e: WheelEvent) {
    if (disabled) return; e.preventDefault();
    const ef = e.shiftKey ? step / fineStepDivisor : step * 4;
    emit(clamp(snapToStep(value + (e.deltaY > 0 ? -1 : 1) * ef, ef, min), min, max), true);
  }
  function onKey(e: KeyboardEvent) { if (disabled) return; const nv = handleKeyboardStep(value, e.key, step, fineStepDivisor, min, max, e.shiftKey); if (nv !== null) { e.preventDefault(); emit(nv); } }
  function onDbl() { if (!disabled) emit(defaultValue ?? min); }
</script>

<div class="pro-knob" class:compact class:disabled
  data-shortcut-hint={shortcutHint ?? undefined} title={title ?? shortcutHint ?? undefined}
  style="--pro-accent:{accentColor};--pro-size:{size}px;">
  {#if showLabel}<span class="pro-label">{label}</span>{/if}
  <div class="pro-ctr" role="slider" tabindex={disabled ? -1 : 0}
    aria-label={label} aria-valuemin={min} aria-valuemax={max} aria-valuenow={value} aria-disabled={disabled}
    onpointerdown={onDown} onpointermove={onMove} onpointerup={onUp} onpointercancel={onUp}
    onwheel={onWheel} onkeydown={onKey} ondblclick={onDbl}>
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
  {#if tickLabels.length > 0}<div class="pro-ticks">{#each tickLabels as tl}<span class="pro-tick">{tl}</span>{/each}</div>{/if}
</div>

<style>
  .pro-knob { display: flex; flex-direction: column; align-items: center; gap: 4px; font-family: 'Roboto Mono', monospace; }
  .pro-label { color: var(--pro-accent, #00e5ff); font-size: 10px; text-align: center; text-transform: uppercase; letter-spacing: 0.06em; opacity: 0.85; }
  .compact .pro-label { font-size: 9px; }
  .disabled { opacity: 0.4; pointer-events: none; }
  .pro-ctr { position: relative; width: var(--pro-size); height: var(--pro-size); cursor: grab; outline: none; touch-action: none; }
  .pro-ctr:active { cursor: grabbing; }
  .pro-ctr:focus-visible { outline: 2px solid var(--pro-accent, #00e5ff); outline-offset: 4px; border-radius: 50%; }
  .pro-svg { display: block; }
  .pro-val { position: absolute; bottom: -2px; left: 50%; transform: translateX(-50%); color: #e0f0ff; font-size: 10px; font-weight: 500; white-space: nowrap; }
  .compact .pro-val { font-size: 9px; bottom: 0; }
  .pro-ticks { display: flex; justify-content: space-between; width: 100%; padding: 0 2px; }
  .pro-tick { color: #4a5a68; font-size: 8px; }
</style>
