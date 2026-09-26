<script lang="ts">
  import { onMount } from 'svelte';
  import {
    WaterfallRenderer,
    defaultWaterfallOptions,
    type WaterfallOptions,
  } from '../../lib/renderers/waterfall-renderer';
  import { gesture } from '../../lib/gestures/use-gesture';
  import { vibrate } from '../../lib/utils/haptics';
  import { canvasBackingSize, watchDevicePixelRatio, watchStageScale } from '../../lib/canvas/backing-store.svelte';
  import { getStageScale } from '../../primitives/stage/stage-scale';

  interface Props {
    options?: WaterfallOptions;
    onFreqClick?: (hz: number) => void;
    onRegisterPush?: (fn: (data: Uint8Array, options?: WaterfallOptions) => void) => void;
  }

  let { options = defaultWaterfallOptions, onFreqClick, onRegisterPush }: Props = $props();

  let canvas: HTMLCanvasElement;
  let renderer: WaterfallRenderer | null = null;
  // `renderer` is a plain variable, so the options effect below would not
  // re-run after mount without reading this flag.
  let rendererReady = $state(false);
  let cssWidth = 0;
  let cssHeight = 0;

  function directPush(pixels: Uint8Array, frameOptions?: WaterfallOptions): void {
    if (document.hidden) return;
    // The push fires synchronously from the frame handler while the options
    // $effect runs later — prefer the caller's fresh snapshot.
    const effective = frameOptions ?? options;
    if (renderer && effective) renderer.updateOptions(effective);
    renderer?.pushRow(pixels);
  }

  $effect(() => {
    if (rendererReady && renderer && options) {
      renderer.updateOptions(options);
    }
  });

  // Bumped when the canvas box or the pixel ratio changes. The stage-scale
  // effect reads it, so that effect re-runs after either and stays the only
  // reader of the stage scale (MOR-1161).
  let backingEpoch = $state(0);

  function applyBackingStore(stageScale: number): void {
    if (!renderer) return;
    const backing = canvasBackingSize(cssWidth, cssHeight, window.devicePixelRatio || 1, stageScale);
    renderer.resize(backing.width, backing.height);
  }

  // The stage's transform does not resize this canvas, so nothing else here
  // notices a scale change (MOR-1161).
  watchStageScale(() => { void backingEpoch; applyBackingStore(getStageScale()()); });

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
    onRegisterPush?.(directPush);

    const ro = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect;
      if (!rect) return;
      cssWidth = rect.width;
      cssHeight = rect.height;
      backingEpoch += 1;
    });
    ro.observe(canvas);
    const stopPixelWatch = watchDevicePixelRatio(() => { backingEpoch += 1; });

    return () => {
      stopPixelWatch();
      ro.disconnect();
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
