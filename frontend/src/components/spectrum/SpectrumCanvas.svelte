<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import {
    SpectrumRenderer,
    defaultSpectrumOptions,
    type SpectrumOptions,
  } from '../../lib/renderers/spectrum-renderer';
  import { CanvasSurface, type CanvasSurfaceFacts } from '../../lib/canvas/canvas-surface.svelte';
  import { ScopeRenderScheduler } from '../../lib/canvas/scope-render-scheduler';
  import { getStageScale } from '../../primitives/stage/stage-scale';
  import type { ScopeRenderTrace, TraceToken } from '../../lib/runtime/scope-render-trace';
  import type { ColorSchemeName } from '../../lib/renderers/waterfall-renderer';
  import type { StackedTraceLocate } from '../../lib/renderers/stacked-trace-geometry';
  import StackedTraceCanvas from './StackedTraceCanvas.svelte';
  import {
    isStackedTraceEnabled, isStackedTraceUnavailable, markStackedTraceUnavailable,
  } from '../../lib/stores/stacked-trace.svelte';
  interface Props {
    data: Uint8Array | null;
    options?: SpectrumOptions;
    spanHz?: number;
    absoluteLabels?: boolean;
    enableAvg?: boolean;
    enablePeakHold?: boolean;
    trace?: ScopeRenderTrace | null;
    onRegisterPush?: (fn: (data: Uint8Array, token?: TraceToken | null) => void) => void;
    /** Surface facts on every size/visibility change (MOR-4271). */
    onSurfaceChange?: (facts: CanvasSurfaceFacts) => void;
    surface?: CanvasSurface;
    /**
     * Waterfall palette of the host panorama. A host that passes it (with
     * its `surface`) lets the user's stacked-trace preference replace the
     * flat trace (MOR-4463); without it the pane is always flat.
     */
    colorScheme?: ColorSchemeName;
    /**
     * While the stacked-trace view is shown a pointer position is not a
     * frequency by its x alone: the host gets the view's own map, and null
     * again when the flat trace returns.
     */
    onRegisterLocate?: (locate: StackedTraceLocate | null) => void;
  }

  let { data, options = defaultSpectrumOptions, spanHz = 0, absoluteLabels = false, enableAvg = true, enablePeakHold = true, onRegisterPush, onSurfaceChange, surface: sharedSurface, colorScheme, onRegisterLocate, trace = null }: Props = $props();

  const renderer = new SpectrumRenderer();

  $effect(() => { renderer.setAvgEnabled(enableAvg); });
  $effect(() => { renderer.setPeakHoldEnabled(enablePeakHold); });

  function formatOffset(hz: number): string {
    const absHz = Math.abs(hz);
    const sign = hz < 0 ? '-' : '+';
    if (absHz >= 1e6) return `${sign}${(absHz / 1e6).toFixed(1)} MHz`;
    if (absHz >= 1e3) return `${sign}${(absHz / 1e3).toFixed(0)} kHz`;
    return `${sign}${absHz} Hz`;
  }

  let canvas = $state<HTMLCanvasElement>();

  // MOR-4271: the one sizing/visibility mechanism and the one redraw
  // scheduler. The scheduler requests an animation frame only while the
  // surface is active (sized ∧ visible); the visibility wake repaints from
  // the cached latest frame.
  const suppliedSurface = untrack(() => sharedSurface);
  const surface = suppliedSurface ?? new CanvasSurface({ stageScale: getStageScale() });
  const scheduler = new ScopeRenderScheduler(draw, { active: () => surface.visible && surface.sized });

  // MOR-4463: the stacked-trace view takes over the pane only by the user's
  // own toggle, for an RF panorama whose host supplied the surface and the
  // palette; every other case keeps the flat trace.
  let stacked = $derived(isStackedTraceEnabled() && !isStackedTraceUnavailable()
    && colorScheme !== undefined && suppliedSurface !== undefined && options.showRfOverlays !== false);
  let stackedPush: ((pixels: Uint8Array, token?: TraceToken | null) => void) | null = null;

  // Latest scope pixels — updated directly from WS binary, not via Svelte props
  let latestPixels: Uint8Array | null = null;
  let latestToken: TraceToken | null = null;
  let scheduledAt = 0;

  function wake(reason: 'frame' | 'view' | 'size' | 'visibility'): void {
    if (trace?.status === 'recording') scheduledAt = performance.now();
    scheduler.wake(reason);
  }

  function draw(now: number): void {
    void now;
    if (!surface.visible) return;
    // The pre-surface component floored the observer rect for drawing; the
    // surface now carries the fractional box, so the historical floor lives
    // here.
    const cssWidth = Math.max(1, Math.floor(surface.cssW));
    const cssHeight = Math.max(1, Math.floor(surface.cssH));
    const pixels = latestPixels ?? data;
    const token = latestToken;
    const measuring = trace?.status === 'recording';
    if (measuring && canvas) trace?.recordGeometry('spectrum', cssWidth, cssHeight, canvas.width, canvas.height, window.devicePixelRatio || 1);
    const drawAt = measuring ? performance.now() : 0;
    if (measuring && scheduledAt) trace?.record('raf', token, drawAt - scheduledAt);
    if (canvas && pixels && surface.sized) {
      const ctx = canvas.getContext('2d');
      if (ctx) {
        renderer.render(ctx, pixels, cssWidth, cssHeight, options);
        if (measuring) trace?.record('spectrum_render', token, performance.now() - drawAt);
      }
    }
    scheduledAt = 0;
  }

  // Renderer options and fallback prop data can change without a stream push.
  $effect(() => {
    data; options; spanHz; enableAvg; enablePeakHold;
    wake('view');
  });

  // The surface publishes sizing (RO ∧ DPR ∧ stage scale) and visibility
  // (IO ∧ !document.hidden); size and visibility wakes repaint from cache.
  $effect(() => {
    // The flat canvas is absent while the stacked view is shown, and is
    // sized and repainted again when it returns.
    const target = canvas;
    if (!target) return;
    return untrack(() => {
      const stop = suppliedSurface ? surface.bindBacking(target) : surface.attach(target);
      wake('size');
      return stop;
    });
  });
  const stopSizeListener = surface.onSizeChanged(() => wake('size'));
  $effect(() => {
    if (surface.visible) wake('visibility');
  });
  $effect(() => {
    onSurfaceChange?.(surface.facts);
  });

  onMount(() => {
    // Register push callback with parent — parent handles WS subscription
    onRegisterPush?.((pixels: Uint8Array, token?: TraceToken | null) => {
      const measuring = trace?.status === 'recording';
      if (measuring && scheduler.pending && latestToken && token && latestToken !== token) {
        trace?.record('coalesced', latestToken, 0, true);
      }
      latestPixels = pixels;
      latestToken = token ?? null;
      if (measuring) trace?.record('spectrum_push', latestToken);
      if (stacked && stackedPush) stackedPush(pixels, latestToken);
      else wake('frame');
    });
    return () => {
      stopSizeListener();
      scheduler.dispose();
    };
  });
</script>

<div class="spectrum-container">
  {#if stacked && colorScheme !== undefined}
    <StackedTraceCanvas {options} {colorScheme} {surface} {trace} {onRegisterLocate} onUnavailable={markStackedTraceUnavailable}
      onRegisterPush={(fn) => {
        stackedPush = fn;
        if (latestPixels) fn(latestPixels, latestToken);
      }} />
  {:else}
    <canvas bind:this={canvas}></canvas>
  {/if}
  {#if options.showRfOverlays !== false && spanHz > 0}
    <div class="span-indicators">
      <span class="span-left">{absoluteLabels ? `${((options.centerHz - spanHz / 2) / 1e6).toFixed(3)} MHz` : formatOffset(spanHz / -2)}</span>
      <span class="span-right">{absoluteLabels ? `${((options.centerHz + spanHz / 2) / 1e6).toFixed(3)} MHz` : formatOffset(spanHz / 2)}</span>
    </div>
  {/if}
</div>

<style>
  .spectrum-container {
    position: relative;
    width: 100%;
    height: 100%;
  }

  canvas {
    display: block;
    width: 100%;
    height: 100%;
  }

  .span-indicators {
    position: absolute;
    top: 4px;
    left: 0;
    right: 0;
    display: flex;
    justify-content: space-between;
    pointer-events: none;
    padding: 0 8px;
  }

  .span-left,
  .span-right {
    font-size: 11px;
    color: var(--text-secondary);
    background: rgba(0, 0, 0, 0.5);
    padding: 2px 6px;
    border-radius: 3px;
  }
</style>
