<script lang="ts">
  // The stacked-trace ("3D") view of the spectrum pane (MOR-4463). It is fed
  // the same frames and the same options as the flat canvas it replaces.
  import { onMount, untrack } from 'svelte';
  import { StackedTraceRenderer, StackedTraceRenderError } from '../../lib/renderers/stacked-trace-renderer';
  import { renderSpectrum, type SpectrumOptions } from '../../lib/renderers/spectrum-renderer';
  import { defaultWaterfallOptions, type ColorSchemeName, type WaterfallOptions } from '../../lib/renderers/waterfall-renderer';
  import type { CanvasSurface } from '../../lib/canvas/canvas-surface.svelte';
  import { ScopeRenderScheduler } from '../../lib/canvas/scope-render-scheduler';

  interface Props {
    options: SpectrumOptions;
    colorScheme: ColorSchemeName;
    surface: CanvasSurface;
    onRegisterPush: (fn: (data: Uint8Array) => void) => void;
    /** The browser gave no usable WebGL2 context; the host falls back to the flat view. */
    onUnavailable: () => void;
  }

  let { options, colorScheme, surface, onRegisterPush, onUnavailable }: Props = $props();

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
  const frame = { pixels: null as Uint8Array | null, options: { ...defaultWaterfallOptions }, pending: false };
  const scheduler = new ScopeRenderScheduler(paint, {
    active: () => surface.visible && surface.sized && renderer !== null,
  });

  function paint(now: number): void {
    if (!renderer) return;
    renderer.updateOptions(rowOptions(options, viewOptions));
    if (frame.pending && frame.pixels) renderer.pushRow(frame.pixels, frame.options, now);
    frame.pending = false;
    // Rows glide back between commits: keep painting display frames meanwhile.
    if (renderer.draw(now)) scheduler.wake('view');
    if (renderer.historyRows !== historyRows) historyRows = renderer.historyRows;
  }

  function push(pixels: Uint8Array): void {
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
    chromeOptions; surface.backingW; surface.backingH;
    untrack(paintChrome);
  });
  $effect(() => {
    if (surface.visible) untrack(() => scheduler.wake('visibility'));
  });

  function createRenderer(): void {
    renderer?.destroy(); renderer = null;
    try {
      renderer = new StackedTraceRenderer(scene, rowOptions(options, viewOptions));
    } catch (error) {
      if (!(error instanceof StackedTraceRenderError)) throw error;
      onUnavailable();
      return;
    }
    frame.pending = frame.pixels !== null;
    scheduler.wake('size');
  }

  function contextLost(event: Event): void {
    event.preventDefault();
    renderer?.destroy(); renderer = null;
  }

  onMount(() => {
    scene.addEventListener('webglcontextlost', contextLost);
    scene.addEventListener('webglcontextrestored', createRenderer);
    // The WebGL2 context must exist before the surface sizes the canvas:
    // the surface's sizing asks a canvas for a 2D context.
    createRenderer();
    const unbindScene = surface.bindBacking(scene);
    const unbindChrome = surface.bindBacking(chrome);
    // Sizing a canvas clears it, so the first chrome paint follows the binding.
    paintChrome();
    const stopSize = surface.onSizeChanged(() => scheduler.wake('size'));
    onRegisterPush(push);
    return () => {
      scene.removeEventListener('webglcontextlost', contextLost);
      scene.removeEventListener('webglcontextrestored', createRenderer);
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
