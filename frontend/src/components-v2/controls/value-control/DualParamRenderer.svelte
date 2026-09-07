<script lang="ts">
  import { onDestroy, untrack } from 'svelte';
  import type {
    ContinuousPairBinding,
    ContinuousPairLaneView,
    ContinuousPairRendererLease,
    ContinuousPairView,
  } from '../../../primitives/scalar/continuous-pair.svelte';
  import { clamp } from '../../../primitives/scalar/value-control-core';
  import type {
    DualParamIssuedStatusPresentation,
    DualParamLane,
  } from './dual-param-issued-status';
  import './value-control.css';

  interface Props {
    binding: ContinuousPairBinding;
    rfLabel?: string;
    sqlLabel?: string;
    rfAccentColor?: string;
    sqlAccentColor?: string;
    trackColor?: string;
    showValues?: boolean;
    variant?: 'modern' | 'hardware' | 'hardware-illuminated';
    shortcutHint?: string | null;
    title?: string | null;
    issuedStatusPresentation?: Readonly<DualParamIssuedStatusPresentation>;
  }

  let {
    binding,
    rfLabel = 'RF',
    sqlLabel = 'SQL',
    rfAccentColor = '#22C55E',
    sqlAccentColor = '#F59E0B',
    trackColor = 'var(--v2-bg-gradient-start)',
    showValues = true,
    variant = 'hardware-illuminated',
    shortcutHint = null,
    title = null,
    issuedStatusPresentation,
  }: Props = $props();

  let containerEl: HTMLDivElement | null = $state(null);
  let activePointer: { id: number; token: number; target: HTMLElement } | null = null;
  const initialBinding = untrack(() => binding);
  let attachedBinding = initialBinding;
  let lease: ContinuousPairRendererLease = initialBinding.attachRenderer();
  let view = $state<ContinuousPairView | null>(null);

  function releaseActivePointer(): void {
    if (activePointer?.target.hasPointerCapture?.(activePointer.id)) {
      activePointer.target.releasePointerCapture(activePointer.id);
    }
    activePointer = null;
  }

  onDestroy(() => {
    releaseActivePointer();
    lease.dispose();
  });

  $effect.pre(() => {
    if (binding !== attachedBinding) {
      releaseActivePointer();
      lease.dispose();
      attachedBinding = binding;
      lease = binding.attachRenderer();
    }
    view = lease.view;
  });

  function laneValue(lane: ContinuousPairLaneView): number | null {
    if (lane.evidence === 'command-feedback' && lane.feedback.target !== null) {
      return lane.feedback.target;
    }
    return lane.localRequested ?? lane.canonical;
  }

  function percent(value: number | null, snapshot: ContinuousPairView | null): number | null {
    if (value === null || snapshot === null || !snapshot.domainValid) return null;
    const span = snapshot.domain.max - snapshot.domain.min;
    return span > 0 ? Math.round((value - snapshot.domain.min) / span * 100) : null;
  }

  let thumbPct = $derived(view?.displayedPosition === null || view?.displayedPosition === undefined
    ? 50 : clamp(view.displayedPosition, 0, 1) * 100);
  let displayRf = $derived(view === null ? null : percent(laneValue(view.lanes.rf), view));
  let displaySql = $derived(view === null ? null : percent(laneValue(view.lanes.sql), view));
  let absDeviation = $derived(Math.abs(thumbPct - 50) / 50);
  let fillRatio = $derived(absDeviation);
  let rfFillWidth = $derived(Math.max(0, 50 - thumbPct));
  let sqlFillWidth = $derived(Math.max(0, thumbPct - 50));
  let slitAccent = $derived(thumbPct <= 50 ? rfAccentColor : sqlAccentColor);
  let ariaNow = $derived(view?.position === null || view?.position === undefined
    ? undefined : Math.round(view.position * 100));
  let rfLane = $derived(view?.lanes.rf ?? null);
  let sqlLane = $derived(view?.lanes.sql ?? null);
  let rfStatusText = $derived(issuedStatusPresentation === undefined
    ? rfLane?.announcement ?? null : issuedStatusPresentation.rf.text);
  let sqlStatusText = $derived(issuedStatusPresentation === undefined
    ? sqlLane?.announcement ?? null : issuedStatusPresentation.sql.text);

  $effect(() => {
    const snapshot = view;
    if (issuedStatusPresentation === undefined || snapshot === null) return;
    for (const lane of ['rf', 'sql'] as const satisfies readonly DualParamLane[]) {
      const laneView = snapshot.lanes[lane];
      if (laneView.evidence !== 'command-feedback') continue;
      const output = issuedStatusPresentation[lane];
      const announcement = laneView.presentation.politeAnnouncement;
      if (announcement !== null) {
        const text = untrack(() => output.format({ lane, view: snapshot, laneView, announcement }));
        untrack(() => output.accept(text));
      } else if (laneView.feedback.transitionId === null) {
        untrack(() => output.accept(null));
      }
    }
  });

  function normX(clientX: number): number | null {
    if (!containerEl) return null;
    const rect = containerEl.getBoundingClientRect();
    if (!Number.isFinite(rect.width) || rect.width <= 0) return null;
    return clamp((clientX - rect.left) / rect.width, 0, 1);
  }

  function handlePointerDown(e: PointerEvent) {
    if (!containerEl || view === null) return;
    const token = lease.beginPointer();
    if (token === null) return;
    e.preventDefault();
    const target = e.currentTarget as HTMLElement;
    target.setPointerCapture(e.pointerId);
    activePointer = { id: e.pointerId, token, target };
    const candidate = normX(e.clientX);
    if (candidate !== null) lease.pointer(token, candidate);
  }

  function handlePointerMove(e: PointerEvent) {
    if (!activePointer || activePointer.id !== e.pointerId) return;
    const candidate = normX(e.clientX);
    if (candidate !== null) lease.pointer(activePointer.token, candidate);
  }

  function handlePointerUp(e: PointerEvent) {
    if (!activePointer || activePointer.id !== e.pointerId) return;
    const token = activePointer.token;
    releaseActivePointer();
    lease.endPointer(token);
  }

  function handlePointerCancel(e: PointerEvent) {
    if (!activePointer || activePointer.id !== e.pointerId) return;
    const token = activePointer.token;
    releaseActivePointer();
    lease.cancelPointer(token);
  }

  function handleWheel(e: WheelEvent) {
    if (view === null || !view.editable) return;
    e.preventDefault();
    lease.wheel({ direction: e.deltaY > 0 ? -1 : 1, fine: e.shiftKey });
  }

  function handleKeyDown(e: KeyboardEvent) {
    if (lease.key({ key: e.key, fine: e.shiftKey })) e.preventDefault();
  }

  function handleDoubleClick() {
    lease.reset();
  }
</script>

{#if view !== null}
<div
  class="vc-dual"
  class:disabled={!view.editable}
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
      <span class="vc-label-rf">{rfLabel}<span class="vc-num">{displayRf === null ? '—' : `${displayRf}%`}</span></span>
      <span class="vc-label-sql"><span class="vc-num">{displaySql === null ? '—' : `${displaySql}%`}</span>{sqlLabel}</span>
    </div>
  {/if}

  <div
    class="vc-track-container"
    role="slider"
    tabindex={view.editable ? 0 : -1}
    data-control="rf-sql-dual"
    aria-label="RF gain and squelch (single control). Center is default; left reduces RF; right adds squelch."
    aria-valuemin={0}
    aria-valuemax={100}
    aria-valuenow={ariaNow}
    aria-valuetext="RF {displayRf === null ? '—' : `${displayRf}%`}, squelch {displaySql === null ? '—' : `${displaySql}%`}"
    aria-disabled={!view.editable}
    aria-busy={view.busy}
    data-rf-command-phase={rfLane?.phase ?? undefined}
    data-sql-command-phase={sqlLane?.phase ?? undefined}
    data-rf-confirmed={rfLane?.canonical ?? undefined}
    data-rf-target={rfLane?.evidence === 'command-feedback' ? rfLane.feedback.target ?? '' : undefined}
    data-rf-requested={rfLane?.evidence === 'command-feedback' ? rfLane.feedback.requestedTarget ?? '' : undefined}
    data-rf-error={rfLane?.error ?? undefined}
    data-sql-confirmed={sqlLane?.canonical ?? undefined}
    data-sql-target={sqlLane?.evidence === 'command-feedback' ? sqlLane.feedback.target ?? '' : undefined}
    data-sql-requested={sqlLane?.evidence === 'command-feedback' ? sqlLane.feedback.requestedTarget ?? '' : undefined}
    data-sql-error={sqlLane?.error ?? undefined}
    onpointerdown={handlePointerDown}
    onpointermove={handlePointerMove}
    onpointerup={handlePointerUp}
    onpointercancel={handlePointerCancel}
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
  {#if rfStatusText}
    <span class="sr-only" role="status" aria-live="polite" aria-atomic="true" data-control-feedback-status>{rfStatusText}</span>
  {/if}
  {#if sqlStatusText}
    <span class="sr-only" role="status" aria-live="polite" aria-atomic="true" data-control-feedback-status>{sqlStatusText}</span>
  {/if}
</div>
{/if}

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
