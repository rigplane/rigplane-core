<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import {
    WaterfallRenderer, WaterfallRenderError,
    defaultWaterfallOptions,
    type WaterfallOptions,
  } from '../../lib/renderers/waterfall-renderer';
  import { t } from '$lib/i18n';
  import { gesture } from '../../lib/gestures/use-gesture';
  import { vibrate } from '../../lib/utils/haptics';
  import { CanvasSurface } from '../../lib/canvas/canvas-surface.svelte';
  import { ScopeRenderScheduler } from '../../lib/canvas/scope-render-scheduler';
  import { getStageScale } from '../../primitives/stage/stage-scale';
  import type { ScopeRenderTrace, TraceToken } from '../../lib/runtime/scope-render-trace';

  interface Props {
    options?: WaterfallOptions;
    onFreqClick?: (hz: number) => void;
    trace?: ScopeRenderTrace | null;
    onRegisterPush?: (fn: (data: Uint8Array, options?: WaterfallOptions, token?: TraceToken | null) => void) => void;
  }

  let { options = defaultWaterfallOptions, onFreqClick, onRegisterPush, trace = null }: Props = $props();

  let canvas: HTMLCanvasElement;
  let renderer: WaterfallRenderer | null = null;
  let failure = $state<WaterfallRenderError | null>(null);
  function failed(error: unknown): void {
    if (!(error instanceof WaterfallRenderError)) throw error;
    failure = error; renderer?.destroy(); renderer = null;
  }
  // `renderer` is a plain variable, so the options effect below would not
  // re-run after mount without reading this flag.
  let rendererReady = $state(false);

  // MOR-4271: one surface + one scheduler. Frames pushed while the surface
  // is inactive (hidden, off-screen, unsized) are CACHED, never dropped —
  // the visibility wake repaints the latest cached frame. The renderer owns
  // the canvas dimensions through the surface's `applyBacking` override.
  const surface = new CanvasSurface({
    stageScale: getStageScale(),
    applyBacking: (_target, backing) => {
      try { renderer?.resize(backing.width, backing.height); } catch (error) { failed(error); }
    },
  });
  const scheduler = new ScopeRenderScheduler(paint, { active: () => surface.visible && surface.sized });

  const frame = { pixels: null as Uint8Array | null, options: undefined as WaterfallOptions | undefined,
    token: null as TraceToken | null };
  const paintOptions = { ...defaultWaterfallOptions };
  let pending = false;

  function paint(): void {
    if (!renderer) return;
    const measuring = trace?.status === 'recording';
    if (measuring) trace?.recordGeometry('waterfall', surface.cssW, surface.cssH, canvas.width, canvas.height, window.devicePixelRatio || 1);
    const optionsStart = measuring ? performance.now() : 0;
    try {
      if (pending && frame.options) {
        Object.assign(paintOptions, options);
        paintOptions.sampleSpanHz = frame.options.sampleSpanHz || frame.options.spanHz;
        paintOptions.sampleWindow = frame.options.sampleWindow;
        paintOptions.sampleOffset = frame.options.sampleOffset;
        paintOptions.sampleAmplitudeMax = frame.options.sampleAmplitudeMax;
        paintOptions.coverage = frame.options.coverage;
        paintOptions.panoramaShiftHz = options.centerHz - (frame.options.centerHz - frame.options.panoramaShiftHz);
        renderer.updateOptions(paintOptions);
      } else renderer.updateOptions(options);
      if (measuring) trace?.record('waterfall_options', frame.token, performance.now() - optionsStart);
      const pushStart = measuring ? performance.now() : 0;
      if (pending && frame.pixels) renderer.pushRow(frame.pixels);
      else renderer.draw();
      pending = false;
      if (measuring) trace?.record('waterfall_push', frame.token, performance.now() - pushStart);
    } catch (error) { failed(error); }
  }

  function directPush(pixels: Uint8Array, frameOptions?: WaterfallOptions, token?: TraceToken | null): void {
    if (!renderer) return;
    if (trace?.status === 'recording' && scheduler.pending && pending) trace.record('coalesced', frame.token, 0, true);
    frame.pixels = pixels; frame.options = frameOptions ?? options; frame.token = token ?? null;
    pending = true;
    scheduler.wake('frame');
  }

  $effect(() => {
    if (rendererReady && renderer && options) {
      renderer.updateOptions(options);
      scheduler.wake('view');
    }
  });

  $effect(() => untrack(() => surface.attach(canvas)));
  const stopSizeListener = surface.onSizeChanged(() => {
    if (frame.pixels) pending = true;
    scheduler.wake('size');
  });
  $effect(() => {
    if (surface.visible) untrack(() => {
      scheduler.wake('visibility');
    });
  });

  // Tap-to-tune only — drag-to-pan handled by SpectrumPanel (parent).
  const waterfallGestures = {
    onTap(x: number, _y: number): void {
      if (!renderer || !onFreqClick) return;
      // Map the tap in CSS pixels, then into the backing store by the pixel
      // ratio only. The stage scale enlarges the store and the painted rect by
      // the same factor, so it cancels; folding the store width in here would
      // move the tuned frequency (MOR-1161).
      const rect = canvas.getBoundingClientRect();
      const dpr = window.devicePixelRatio || 1;
      const freq = renderer.pixelToFreq((x - rect.left) * dpr);
      if (freq > 0) {
        vibrate('tap');
        onFreqClick(freq);
      }
    },
  };

  function contextLost(event: Event): void { event.preventDefault(); failed(new WaterfallRenderError()); }

  onMount(() => {
    canvas.addEventListener('webglcontextlost', contextLost);
    try { renderer = new WaterfallRenderer(canvas, options); } catch (error) { failed(error); }
    rendererReady = true;
    // The surface may have attached (and published facts) before the
    // renderer existed — apply the current backing once.
    const facts = surface.facts;
    if (facts.backingW > 0 && facts.backingH > 0) {
      try { renderer?.resize(facts.backingW, facts.backingH); } catch (error) { failed(error); }
    }
    onRegisterPush?.(directPush);
    return () => {
      canvas.removeEventListener('webglcontextlost', contextLost);
      stopSizeListener();
      scheduler.dispose();
      renderer?.destroy();
      renderer = null;
      rendererReady = false;
    };
  });
</script>

<div class="waterfall-surface" data-error={failure?.code}>
  <canvas bind:this={canvas} use:gesture={waterfallGestures}></canvas>
  {#if failure}<div class="waterfall-error" role="alert">{t('core.scope.webglRequired')}</div>{/if}
</div>

<style>
  .waterfall-surface { position: relative; width: 100%; height: 100%; }
  .waterfall-error { position: absolute; inset: 0; display: grid; place-items: center; color: var(--text-secondary); background: #001020; padding: 8px; }

  canvas {
    display: block;
    width: 100%;
    height: 100%;
    cursor: crosshair;
    background: #001020;
  }
</style>
