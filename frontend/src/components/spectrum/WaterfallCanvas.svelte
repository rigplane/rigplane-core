<script lang="ts">
  import { onMount, untrack } from 'svelte';
  import {
    WaterfallRenderer,
    defaultWaterfallOptions,
    type WaterfallOptions,
  } from '../../lib/renderers/waterfall-renderer';
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
  // `renderer` is a plain variable, so the options effect below would not
  // re-run after mount without reading this flag.
  let rendererReady = $state(false);

  // MOR-4271: one surface + one scheduler. Frames pushed while the surface
  // is inactive (hidden, off-screen, unsized) are CACHED, never dropped —
  // the visibility wake repaints the latest cached frame. The renderer owns
  // the canvas dimensions through the surface's `applyBacking` override.
  const surface = new CanvasSurface({
    stageScale: getStageScale(),
    applyBacking: (target, backing) => renderer?.resize(backing.width, backing.height),
  });
  const scheduler = new ScopeRenderScheduler(paint, { active: () => surface.visible && surface.sized });

  // MOR-4206: scope frames can arrive faster than the display paints. The
  // scheduler keeps only the LATEST pending frame and paints once per
  // animation frame; at or below the display rate every frame paints.
  interface WaterfallFrame {
    pixels: Uint8Array;
    options: WaterfallOptions | undefined;
    token: TraceToken | null;
  }
  let pendingFrame: WaterfallFrame | null = null;
  let latestFrame: WaterfallFrame | null = null;

  // Review round 2 (MOR-4206): the queued pixels were sampled against the
  // delivery-time sample window, but the panorama may have advanced while
  // the frame awaited its animation frame — the options effect below has
  // already applied the newer viewport. Keep the frame's sample-window
  // center and paint it under the CURRENT viewport and appearance options,
  // recomputing the sampling shift. Applying the delivery-time snapshot
  // instead would scroll history backward against the spectrum/overlays
  // and revert the center pixelToFreq() tunes against.
  function reconciledFrameOptions(frameOptions: WaterfallOptions): WaterfallOptions {
    const sampleWindowCenterHz = frameOptions.centerHz - frameOptions.panoramaShiftHz;
    return { ...options, panoramaShiftHz: options.centerHz - sampleWindowCenterHz };
  }

  function paint(): void {
    const frame = pendingFrame;
    pendingFrame = null;
    if (!renderer || !frame) return;
    latestFrame = frame;
    const measuring = trace?.status === 'recording';
    if (measuring) trace?.recordGeometry('waterfall', surface.cssW, surface.cssH, canvas.width, canvas.height, window.devicePixelRatio || 1);
    const optionsStart = measuring ? performance.now() : 0;
    if (frame.options) renderer.updateOptions(reconciledFrameOptions(frame.options));
    if (measuring) trace?.record('waterfall_options', frame.token, performance.now() - optionsStart);
    const pushStart = measuring ? performance.now() : 0;
    renderer.pushRow(frame.pixels);
    if (measuring) trace?.record('waterfall_push', frame.token, performance.now() - pushStart);
  }

  function directPush(pixels: Uint8Array, frameOptions?: WaterfallOptions, token?: TraceToken | null): void {
    if (!renderer) return;
    // The push fires synchronously from the frame handler while the options
    // $effect runs later — prefer the caller's fresh snapshot. A push that
    // arrives while a paint is pending replaces (and traces) the pending
    // frame, exactly like the old latest-wins pump.
    if (trace?.status === 'recording' && scheduler.pending && pendingFrame) {
      trace?.record('coalesced', pendingFrame.token, 0, true);
    }
    pendingFrame = { pixels, options: frameOptions ?? options, token: token ?? null };
    scheduler.wake('frame');
  }

  $effect(() => {
    if (rendererReady && renderer && options) {
      renderer.updateOptions(options);
    }
  });

  $effect(() => untrack(() => surface.attach(canvas)));
  const stopSizeListener = surface.onSizeChanged(() => scheduler.wake('size'));
  $effect(() => {
    if (surface.visible) untrack(() => {
      pendingFrame ??= latestFrame;
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

  onMount(() => {
    renderer = new WaterfallRenderer(canvas, options);
    rendererReady = true;
    // The surface may have attached (and published facts) before the
    // renderer existed — apply the current backing once.
    const facts = surface.facts;
    if (facts.backingW > 0 && facts.backingH > 0) {
      renderer.resize(facts.backingW, facts.backingH);
    }
    onRegisterPush?.(directPush);
    return () => {
      stopSizeListener();
      scheduler.dispose();
      renderer?.destroy();
      renderer = null;
      rendererReady = false;
    };
  });
</script>

<canvas bind:this={canvas} use:gesture={waterfallGestures}></canvas>

<style>
  canvas {
    display: block;
    width: 100%;
    height: 100%;
    cursor: crosshair;
    background: #001020;
  }
</style>
