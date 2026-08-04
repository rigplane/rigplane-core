<script lang="ts">
  import { untrack } from 'svelte';
  import './value-control.css';
  import {
    clamp,
    snapToStep,
    debounce,
    dualParamValuesFromNormX,
    dualParamThumbPercent,
    dualParamDeviationFromValues,
    dualParamStepAlongAxis,
  } from './value-control-core';

  interface Props {
    rfValue: number;
    sqlValue: number;
    min?: number;
    max?: number;
    step?: number;
    fineStepDivisor?: number;
    rfLabel?: string;
    sqlLabel?: string;
    rfAccentColor?: string;
    sqlAccentColor?: string;
    trackColor?: string;
    showValues?: boolean;
    variant?: 'modern' | 'hardware' | 'hardware-illuminated';
    onRfChange: (v: number) => void;
    onSqlChange: (v: number) => void;
    debounceMs?: number;
    disabled?: boolean;
    shortcutHint?: string | null;
    title?: string | null;
  }

  let {
    rfValue,
    sqlValue,
    min = 0,
    max = 255,
    step = 1,
    fineStepDivisor = 10,
    rfLabel = 'RF',
    sqlLabel = 'SQL',
    rfAccentColor = '#22C55E',
    sqlAccentColor = '#F59E0B',
    trackColor = 'var(--v2-bg-gradient-start)',
    showValues = true,
    variant = 'hardware-illuminated',
    onRfChange,
    onSqlChange,
    debounceMs = 0,
    disabled = false,
    shortcutHint = null,
    title = null,
  }: Props = $props();

  let containerEl: HTMLDivElement | null = $state(null);
  let isDragging = $state(false);
  let wheelLocked = $state(false);
  let wheelUnlockTimer: ReturnType<typeof setTimeout> | null = null;
  let localRf = $state(untrack(() => rfValue));
  let localSql = $state(untrack(() => sqlValue));

  function markWheelActive() {
    wheelLocked = true;
    if (wheelUnlockTimer) clearTimeout(wheelUnlockTimer);
    wheelUnlockTimer = setTimeout(() => {
      wheelLocked = false;
      wheelUnlockTimer = null;
    }, 300);
  }

  let prevRf = untrack(() => rfValue);
  let prevSql = untrack(() => sqlValue);
  $effect(() => {
    const r = rfValue;
    const s = sqlValue;
    if (isDragging || wheelLocked) {
      if (r !== prevRf) prevRf = r;
      if (s !== prevSql) prevSql = s;
      return;
    }
    if (r !== prevRf) {
      prevRf = r;
      localRf = r;
    }
    if (s !== prevSql) {
      prevSql = s;
      localSql = s;
    }
  });

  let thumbPct = $derived(dualParamThumbPercent(localRf, localSql, min, max));
  let absDeviation = $derived(dualParamDeviationFromValues(localRf, localSql, min, max));

  /** Display values as 0–100% instead of raw 0–255. */
  let displayRf = $derived(max > min ? Math.round((localRf - min) / (max - min) * 100) : 0);
  let displaySql = $derived(max > min ? Math.round((localSql - min) / (max - min) * 100) : 0);
  let fillRatio = $derived(absDeviation);
  let rfFillWidth = $derived(Math.max(0, 50 - thumbPct));
  let sqlFillWidth = $derived(Math.max(0, thumbPct - 50));
  let slitAccent = $derived(thumbPct <= 50 ? rfAccentColor : sqlAccentColor);

  let adaptiveWheelMultiplier = $derived(Math.max(1, Math.ceil((max - min) / 255)));

  let debouncedRf = $derived.by<(...args: unknown[]) => void>(() => {
    if (debounceMs > 0) {
      return debounce((v: number) => onRfChange(v), debounceMs) as (...args: unknown[]) => void;
    }
    return ((v: number) => onRfChange(v)) as (...args: unknown[]) => void;
  });

  let debouncedSql = $derived.by<(...args: unknown[]) => void>(() => {
    if (debounceMs > 0) {
      return debounce((v: number) => onSqlChange(v), debounceMs) as (...args: unknown[]) => void;
    }
    return ((v: number) => onSqlChange(v)) as (...args: unknown[]) => void;
  });

  function emitPair(nextRf: number, nextSql: number, immediate: boolean) {
    if (nextRf !== localRf) localRf = nextRf;
    if (nextSql !== localSql) localSql = nextSql;
    if (nextRf !== rfValue) {
      if (immediate) onRfChange(nextRf);
      else debouncedRf(nextRf);
    }
    if (nextSql !== sqlValue) {
      if (immediate) onSqlChange(nextSql);
      else debouncedSql(nextSql);
    }
  }

  function normX(clientX: number): number {
    if (!containerEl) return 0;
    const rect = containerEl.getBoundingClientRect();
    return clamp((clientX - rect.left) / rect.width, 0, 1);
  }

  function applyNormX(nx: number, immediate: boolean) {
    const { rf, sql } = dualParamValuesFromNormX(nx, min, max, step);
    emitPair(rf, sql, immediate);
  }

  function handlePointerDown(e: PointerEvent) {
    if (disabled || !containerEl) return;
    e.preventDefault();
    const target = e.currentTarget as HTMLElement;
    target.setPointerCapture(e.pointerId);
    isDragging = true;
    applyNormX(normX(e.clientX), true);
  }

  function handlePointerMove(e: PointerEvent) {
    if (!isDragging || disabled || !containerEl) return;
    applyNormX(normX(e.clientX), true);
  }

  function handlePointerUp(e: PointerEvent) {
    if (!isDragging) return;
    const target = e.currentTarget as HTMLElement;
    target.releasePointerCapture(e.pointerId);
    isDragging = false;
  }

  function handleWheel(e: WheelEvent) {
    if (disabled || !containerEl) return;
    e.preventDefault();
    const wheelMultiplier = e.shiftKey ? 1 : 4 * adaptiveWheelMultiplier;
    const wheelStep = e.shiftKey ? step / fineStepDivisor : step * wheelMultiplier;
    const direction = (e.deltaY > 0 ? -1 : 1) as 1 | -1;
    const { rf, sql } = dualParamStepAlongAxis(
      localRf,
      localSql,
      direction,
      wheelStep,
      fineStepDivisor,
      min,
      max,
      false,
    );
    localRf = rf;
    localSql = sql;
    markWheelActive();
    if (rf !== rfValue) onRfChange(rf);
    if (sql !== sqlValue) onSqlChange(sql);
  }

  function handleKeyDown(e: KeyboardEvent) {
    if (disabled) return;
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    const direction = (e.key === 'ArrowRight' ? 1 : -1) as 1 | -1;
    const { rf, sql } = dualParamStepAlongAxis(
      localRf,
      localSql,
      direction,
      step,
      fineStepDivisor,
      min,
      max,
      e.shiftKey,
    );
    if (rf === localRf && sql === localSql) return;
    e.preventDefault();
    emitPair(rf, sql, false);
  }

  function handleDoubleClick() {
    if (disabled) return;
    emitPair(max, min, true);
  }

  let ariaNow = $derived(Math.round(thumbPct));
</script>

<div
  class="vc-dual"
  class:disabled
  class:hw-illum={variant === 'hardware-illuminated'}
  class:hardware={variant === 'hardware'}
  bind:this={containerEl}
  data-shortcut-hint={shortcutHint ?? undefined}
  title={title ?? shortcutHint ?? undefined}
  style="
    --vc-rf-accent: {rfAccentColor};
    --vc-sql-accent: {sqlAccentColor};
    --vc-track-color: {trackColor};
    --vc-thumb-pct: {thumbPct}%;
    --vc-fill-ratio: {fillRatio};
    --vc-abs-deviation: {absDeviation};
    --vc-slit-accent: {slitAccent};
  "
>
  {#if showValues}
    <div class="vc-header">
      <span class="vc-label-rf">{rfLabel}<span class="vc-num">{displayRf}%</span></span>
      <span class="vc-label-sql"><span class="vc-num">{displaySql}%</span>{sqlLabel}</span>
    </div>
  {/if}

  <div
    class="vc-track-container"
    role="slider"
    tabindex={disabled ? -1 : 0}
    data-control="rf-sql-dual"
    aria-label="RF gain and squelch (single control). Center is default; left reduces RF; right adds squelch."
    aria-valuemin={0}
    aria-valuemax={100}
    aria-valuenow={ariaNow}
    aria-valuetext="RF {displayRf}%, squelch {displaySql}%"
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
      <div class="hil-frame" aria-hidden="true">
        <div class="hil-channel">
          <div class="hil-slot-base"></div>
          <div
            class="hil-fill-rf"
            style="left: {thumbPct}%; width: {rfFillWidth}%; opacity: {rfFillWidth > 0 ? 1 : 0};"
          ></div>
          <div
            class="hil-fill-sql"
            style="left: 50%; width: {sqlFillWidth}%; opacity: {sqlFillWidth > 0 ? 1 : 0};"
          ></div>
        </div>
      </div>
      <div class="hil-thumb" aria-hidden="true">
        <div class="hil-slit"></div>
      </div>
    {:else}
      <div class="vc-track" aria-hidden="true">
        <div class="vc-track-base"></div>
        <div
          class="vc-fill-rf"
          style="left: {thumbPct}%; width: {rfFillWidth}%; opacity: {rfFillWidth > 0 ? 1 : 0};"
        ></div>
        <div
          class="vc-fill-sql"
          style="left: 50%; width: {sqlFillWidth}%; opacity: {sqlFillWidth > 0 ? 1 : 0};"
        ></div>
      </div>
      <div class="vc-thumb" aria-hidden="true"></div>
    {/if}
  </div>

  <div class="vc-axis" aria-hidden="true">
    <span class="axis-rf">{rfLabel}</span>
    <span class="axis-gap"></span>
    <span class="axis-sql">{sqlLabel}</span>
  </div>
</div>

<style>
  .vc-dual {
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

  .vc-label-rf,
  .vc-label-sql {
    color: var(--vc-illum-label-color, var(--vc-text-label, var(--v2-text-dim)));
    letter-spacing: var(--vc-label-tracking, 0.04em);
    text-transform: uppercase;
    font-size: var(--vc-label-size, 9px);
    font-weight: 700;
  }

  .vc-num {
    margin: 0 4px;
    color: var(--vc-illum-value-color, var(--vc-text-value, var(--v2-text-bright)));
    font-family: 'Roboto Mono', monospace;
    font-weight: 600;
  }

  .disabled {
    opacity: 0.4;
    pointer-events: none;
  }

  .vc-track-container {
    position: relative;
    display: flex;
    align-items: center;
    min-height: var(--vc-control-height, 28px);
    cursor: pointer;
    outline: none;
    touch-action: none;
    isolation: isolate;
    overflow: hidden;
  }

  .vc-track-container:focus-visible {
    outline: var(--vc-focus-ring-width, 2px) solid var(--vc-focus-ring);
    outline-offset: 3px;
    border-radius: 2px;
  }

  /* ── Illuminated (default) ───────────────────────────────────────── */

  .hw-illum .hil-frame {
    position: absolute;
    inset: 2px 0;
    border-radius: 14px;
    border: 2px solid var(--vc-illum-frame-border);
    background: transparent;
    box-shadow:
      0 0 14px color-mix(in srgb, var(--vc-illum-glow-primary) calc(14% + 0.21 * var(--vc-abs-deviation, 0) * 100%), transparent),
      0 0 28px color-mix(in srgb, var(--vc-illum-glow-primary) calc(7% + 0.11 * var(--vc-abs-deviation, 0) * 100%), transparent),
      inset 0 0 10px color-mix(in srgb, var(--vc-illum-glow-primary) calc(6% + 0.09 * var(--vc-abs-deviation, 0) * 100%), transparent),
      inset 0 1px 0 var(--vc-illum-frame-inset-highlight),
      inset 0 -1px 0 var(--vc-illum-frame-inset-shadow);
    pointer-events: none;
  }

  .hw-illum .hil-channel {
    position: absolute;
    inset: var(--vc-channel-inset, 4px 8px);
    border-radius: var(--vc-channel-radius, 3px);
    background: var(--vc-illum-channel-bg-sem);
    box-shadow:
      inset 0 2px 4px var(--vc-illum-channel-shadow-1-sem),
      inset 0 -1px 2px var(--vc-illum-channel-shadow-2-sem),
      inset 0 0 0 1px var(--vc-illum-channel-border-sem);
  }

  .hw-illum .hil-slot-base {
    position: absolute;
    top: 50%;
    left: var(--vc-slot-inset-x, 4px);
    right: var(--vc-slot-inset-x, 4px);
    height: var(--vc-illum-slot-height);
    transform: translateY(-50%);
    border-radius: var(--vc-slot-radius, 1.5px);
    background: var(--vc-illum-slot-dark-sem);
    pointer-events: none;
  }

  .hw-illum .hil-fill-rf {
    position: absolute;
    top: 50%;
    height: var(--vc-illum-slot-height);
    transform: translateY(-50%);
    border-radius: var(--vc-slot-radius, 1.5px);
    background: linear-gradient(
      270deg,
      color-mix(in srgb, var(--vc-rf-accent) calc(55% + 40% * var(--vc-abs-deviation)), transparent),
      color-mix(in srgb, var(--vc-rf-accent) calc(28% + 35% * var(--vc-abs-deviation)), transparent)
    );
    box-shadow:
      0 0 8px color-mix(in srgb, var(--vc-rf-accent) calc(22% + 50% * var(--vc-abs-deviation)), transparent),
      0 0 14px color-mix(in srgb, var(--vc-rf-accent) calc(10% + 25% * var(--vc-abs-deviation)), transparent);
    pointer-events: none;
    transition: opacity 0.08s ease-out;
  }

  .hw-illum .hil-fill-sql {
    position: absolute;
    top: 50%;
    height: var(--vc-illum-slot-height);
    transform: translateY(-50%);
    border-radius: var(--vc-slot-radius, 1.5px);
    background: linear-gradient(
      90deg,
      color-mix(in srgb, var(--vc-sql-accent) calc(28% + 35% * var(--vc-abs-deviation)), transparent),
      color-mix(in srgb, var(--vc-sql-accent) calc(55% + 40% * var(--vc-abs-deviation)), transparent)
    );
    box-shadow:
      0 0 8px color-mix(in srgb, var(--vc-sql-accent) calc(22% + 50% * var(--vc-abs-deviation)), transparent),
      0 0 14px color-mix(in srgb, var(--vc-sql-accent) calc(10% + 25% * var(--vc-abs-deviation)), transparent);
    pointer-events: none;
    transition: opacity 0.08s ease-out;
  }

  .hw-illum .hil-thumb {
    position: absolute;
    left: var(--vc-thumb-pct);
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
    border: 1px solid color-mix(
      in srgb,
      var(--vc-slit-accent) 40%,
      var(--vc-illum-thumb-border)
    );
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

  .hw-illum .hil-slit {
    position: absolute;
    top: 3px;
    bottom: 3px;
    left: 50%;
    width: var(--vc-illum-slit-width);
    transform: translateX(-50%);
    border-radius: 1px;
    pointer-events: none;
    background: color-mix(in srgb, var(--vc-slit-accent) calc(50% + 45% * var(--vc-abs-deviation)), transparent);
    box-shadow: 0 0 6px color-mix(in srgb, var(--vc-slit-accent) 35%, transparent);
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

  .hw-illum .vc-track-container::after {
    content: '';
    position: absolute;
    inset-inline: 0;
    bottom: 0;
    height: var(--vc-tick-height, 8px);
    pointer-events: none;
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

  /* ── Simple fallback (non-illuminated) ───────────────────────────── */

  .vc-track {
    position: absolute;
    inset: 0;
    pointer-events: none;
  }

  .vc-track-base {
    position: absolute;
    inset-inline: 0;
    top: 50%;
    height: var(--vc-bar-height, 4px);
    transform: translateY(-50%);
    border-radius: 999px;
    background: var(--vc-track-color);
  }

  .vc-fill-rf,
  .vc-fill-sql {
    position: absolute;
    top: 50%;
    height: var(--vc-bar-height, 4px);
    transform: translateY(-50%);
    border-radius: 999px;
    pointer-events: none;
    transition: opacity 0.08s ease-out;
  }

  .vc-fill-rf {
    background: var(--vc-rf-accent);
    box-shadow: 0 0 0 1px color-mix(in srgb, var(--vc-rf-accent) 25%, transparent);
  }

  .vc-fill-sql {
    background: var(--vc-sql-accent);
    box-shadow: 0 0 0 1px color-mix(in srgb, var(--vc-sql-accent) 25%, transparent);
  }

  .vc-thumb {
    position: absolute;
    left: var(--vc-thumb-pct);
    top: 50%;
    transform: translate(-50%, -50%);
    width: var(--vc-thumb-size, 10px);
    height: var(--vc-thumb-size, 10px);
    border-radius: 2px;
    background: var(--v2-text-white);
    pointer-events: none;
    border: 1px solid color-mix(in srgb, var(--vc-slit-accent) 55%, var(--v2-text-dim));
    box-shadow: 0 0 0 1px color-mix(in srgb, var(--vc-slit-accent) 8%, transparent);
  }

  .hardware .vc-track-container {
    min-height: var(--vc-control-height, 28px);
  }

  .vc-axis {
    display: grid;
    grid-template-columns: 1fr auto 1fr;
    align-items: center;
    color: var(--vc-illum-axis-color, var(--v2-text-dimmer));
    font-size: 8px;
    font-weight: 700;
    letter-spacing: 0.08em;
    line-height: 1;
    user-select: none;
  }

  .axis-rf {
    justify-self: start;
    color: color-mix(in srgb, var(--vc-rf-accent) 85%, var(--v2-text-dimmer));
  }

  .axis-sql {
    justify-self: end;
    color: color-mix(in srgb, var(--vc-sql-accent) 85%, var(--v2-text-dimmer));
  }

  .axis-gap {
    width: 10px;
  }
</style>
