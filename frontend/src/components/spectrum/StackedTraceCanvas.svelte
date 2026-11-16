<script lang="ts">
  // The stacked-trace ("3D") view of the spectrum pane (MOR-4463). It is fed
  // the same frames and the same options as the flat canvas it replaces.
  import { onMount, untrack } from 'svelte';
  import { StackedTraceRenderer, StackedTraceRenderError } from '../../lib/renderers/stacked-trace-renderer';
  import { renderSpectrum, type SpectrumOptions } from '../../lib/renderers/spectrum-renderer';
  import { defaultWaterfallOptions, type ColorSchemeName, type WaterfallOptions } from '../../lib/renderers/waterfall-renderer';
  import type { CanvasSurface } from '../../lib/canvas/canvas-surface.svelte';
  import { ScopeRenderScheduler } from '../../lib/canvas/scope-render-scheduler';
  import type { ScopeRenderTrace, TraceToken } from '../../lib/runtime/scope-render-trace';

  interface Props {
    options: SpectrumOptions;
    colorScheme: ColorSchemeName;
    surface: CanvasSurface;
    onRegisterPush: (fn: (data: Uint8Array, token?: TraceToken | null) => void) => void;
    /** Diagnostic render trace of the host pane; records this view's geometry, paints and coalesced frames. */
    trace?: ScopeRenderTrace | null;
    /** The browser gave no usable WebGL2 context; the host falls back to the flat view. */
    onUnavailable: () => void;
  }

  let { options, colorScheme, surface, onRegisterPush, onUnavailable, trace = null }: Props = $props();

  let scene: HTMLCanvasElement;
  let chrome: HTMLCanvasElement;
  let renderer: StackedTraceRenderer | null = null;
  let historyRows = $state(0);

  /** The sample placement and appearance the renderer needs, from the pane's own options. */
  function rowOptions(source: SpectrumOptions, target: WaterfallOptions): WaterfallOptions {
    target.coverage = source.coverage;
    target.colorScheme = colorScheme;
    target.refLevel = source.refLevel;
    target.centerHz = source.centerHz;
    target.spanHz = source.spanHz;
    target.sampleSpanHz = source.sampleSpanHz;
    target.sampleOffset = source.sampleOffset;
    target.sampleWindow = source.sampleWindow;
    target.sampleAmplitudeMax = source.sampleAmplitudeMax;
    target.panoramaShiftHz = source.panoramaShiftHz;
    return target;
  }

  const viewOptions = { ...defaultWaterfallOptions };
  const frame = { pixels: null as Uint8Array | null, options: { ...defaultWaterfallOptions }, pending: false,
    token: null as TraceToken | null };
  const scheduler = new ScopeRenderScheduler(paint, {
    active: () => surface.visible && surface.sized && renderer !== null,
  });

  /** A typed renderer failure ends the view: the host falls back to the flat trace. */
  function failed(error: unknown): void {
    if (!(error instanceof StackedTraceRenderError)) throw error;
    renderer?.destroy(); renderer = null;
    onUnavailable();
  }

  function paint(now: number): void {
    if (!renderer) return;
    const measuring = trace?.status === 'recording';
    if (measuring) trace?.recordGeometry('spectrum', surface.cssW, surface.cssH, scene.width, scene.height, window.devicePixelRatio || 1);
    const paintAt = measuring ? performance.now() : 0;
    try {
      renderer.setPixelScale(surface.pixelScale);
      renderer.updateOptions(rowOptions(options, viewOptions));
      if (frame.pending && frame.pixels) renderer.pushRow(frame.pixels, frame.options, now);
      frame.pending = false;
      // Rows glide back between frames: keep painting display frames meanwhile.
      if (renderer.draw(now)) scheduler.wake('view');
      if (renderer.historyRows !== historyRows) historyRows = renderer.historyRows;
      if (measuring) trace?.record('spectrum_render', frame.token, performance.now() - paintAt);
    } catch (error) { failed(error); }
  }

  function push(pixels: Uint8Array, token?: TraceToken | null): void {
    if (trace?.status === 'recording' && frame.pending && frame.token && token && frame.token !== token) {
      trace.record('coalesced', frame.token, 0, true);
    }
    frame.token = token ?? null;
    frame.pixels = pixels;
    rowOptions(options, frame.options);
    frame.pending = true;
    scheduler.wake('frame');
  }

  // The frequency labels, the tuning marker, the passband and the coverage
  // hatch are painted by the flat renderer itself over the scene, with its
  // trace and grid made transparent.
  const CHROME_SAMPLES = new Uint8Array(2);
  let chromeOptions = $derived<SpectrumOptions>({
    ...options, lineColor: 'transparent', fillColor: 'transparent', fillColorBottom: 'transparent',
    gridColor: 'transparent',
  });
  function paintChrome(): void {
    const ctx = chrome?.getContext('2d');
    if (!ctx || !surface.sized) return;
    renderSpectrum(ctx, CHROME_SAMPLES, Math.max(1, Math.floor(surface.cssW)), Math.max(1, Math.floor(surface.cssH)), chromeOptions);
  }

  $effect(() => {
    options; colorScheme;
    untrack(() => scheduler.wake('view'));
  });
  $effect(() => {
    chromeOptions;
    untrack(paintChrome);
  });
  $effect(() => {
    if (surface.visible) untrack(() => scheduler.wake('visibility'));
  });

  function createRenderer(): void {
    renderer?.destroy(); renderer = null;
    try {
      renderer = new StackedTraceRenderer(scene, rowOptions(options, viewOptions));
    } catch (error) { failed(error); return; }
    frame.pending = frame.pixels !== null;
    scheduler.wake('size');
  }

  /** A lost context ends the view like any other renderer failure. */
  function contextLost(): void {
    failed(new StackedTraceRenderError());
  }

  onMount(() => {
    scene.addEventListener('webglcontextlost', contextLost);
    // The WebGL2 context must exist before the surface sizes the canvas:
    // the surface's sizing asks a canvas for a 2D context.
    createRenderer();
    const unbindScene = surface.bindBacking(scene);
    const unbindChrome = surface.bindBacking(chrome);
    // Sizing a canvas clears it — on a new size and on a new pixel scale
    // alike — so the chrome is painted after the binding and after every
    // size notification, which the bindings above have already applied.
    paintChrome();
    const stopSize = surface.onSizeChanged(() => { paintChrome(); scheduler.wake('size'); });
    onRegisterPush(push);
    return () => {
      scene.removeEventListener('webglcontextlost', contextLost);
      unbindScene(); unbindChrome(); stopSize();
      scheduler.dispose();
      renderer?.destroy(); renderer = null;
    };
  });
</script>

<div class="stacked-trace" data-testid="stacked-trace" data-stacked-trace data-history-rows={historyRows}>
  <canvas bind:this={scene}></canvas>
  <canvas bind:this={chrome} class="chrome"></canvas>
</div>

<style>
  .stacked-trace {
    position: absolute;
    inset: 0;
    background: #001020;
  }

  canvas {
    position: absolute;
    inset: 0;
    display: block;
    width: 100%;
    height: 100%;
  }

  .chrome {
    pointer-events: none;
  }

  /* The level scale beside the pane reads a flat trace; the stack has no
     single level axis, so its ticks are hidden while this view is mounted. */
  :global(.spectrum-with-scales:has([data-stacked-trace]) .db-scale .tick) {
    visibility: hidden;
  }
</style>
