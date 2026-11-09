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
  }

  let { data, options = defaultSpectrumOptions, spanHz = 0, absoluteLabels = false, enableAvg = true, enablePeakHold = true, onRegisterPush, onSurfaceChange, surface: sharedSurface, trace = null }: Props = $props();

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

  let canvas: HTMLCanvasElement;

  // MOR-4271: the one sizing/visibility mechanism and the one redraw
  // scheduler. The scheduler requests an animation frame only while the
  // surface is active (sized ∧ visible); the visibility wake repaints from
  // the cached latest frame.
  const suppliedSurface = untrack(() => sharedSurface);
  const surface = suppliedSurface ?? new CanvasSurface({ stageScale: getStageScale() });
  const scheduler = new ScopeRenderScheduler(draw, { active: () => surface.visible && surface.sized });

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
  $effect(() => untrack(() => suppliedSurface ? surface.bindBacking(canvas) : surface.attach(canvas)));
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
      wake('frame');
    });
    return () => {
      stopSizeListener();
      scheduler.dispose();
    };
  });
</script>

<div class="spectrum-container">
  <canvas bind:this={canvas}></canvas>
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
