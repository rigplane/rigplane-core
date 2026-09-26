<script lang="ts">
  import { onMount } from 'svelte';
  import {
    WaterfallRenderer,
    defaultWaterfallOptions,
    type WaterfallOptions,
  } from '../../lib/renderers/waterfall-renderer';
  import { gesture } from '../../lib/gestures/use-gesture';
  import { vibrate } from '../../lib/utils/haptics';
  import { canvasBackingSize, readAncestorScale, watchDevicePixelRatio } from '../../lib/canvas/backing-store';

  interface Props {
    options?: WaterfallOptions;
    onFreqClick?: (hz: number) => void;
    onRegisterPush?: (fn: (data: Uint8Array, options?: WaterfallOptions) => void) => void;
  }

  let { options = defaultWaterfallOptions, onFreqClick, onRegisterPush }: Props = $props();

  let canvas: HTMLCanvasElement;
  let renderer = $state<WaterfallRenderer | null>(null);

  function directPush(pixels: Uint8Array, frameOptions?: WaterfallOptions): void {
    if (document.hidden) return;
    // The push fires synchronously from the frame handler while the options
    // $effect runs later — prefer the caller's fresh snapshot.
    const effective = frameOptions ?? options;
    if (renderer && effective) renderer.updateOptions(effective);
    renderer?.pushRow(pixels);
  }

  $effect(() => {
    if (renderer && options) {
      renderer.updateOptions(options);
    }
  });

  // Tap-to-tune only — drag-to-pan handled by SpectrumPanel (parent).
  const waterfallGestures = {
    onTap(x: number, _y: number): void {
      if (!renderer || !onFreqClick) return;
      const rect = canvas.getBoundingClientRect();
      const fraction = rect.width > 0 ? (x - rect.left) / rect.width : 0;
      const freq = renderer.pixelToFreq(fraction * canvas.width);
      if (freq > 0) {
        vibrate('tap');
        onFreqClick(freq);
      }
    },
  };

  onMount(() => {
    renderer = new WaterfallRenderer(canvas, options);
    onRegisterPush?.(directPush);

    let cssWidth = 0;
    let cssHeight = 0;
    function applyBackingStore(): void {
      const backing = canvasBackingSize(cssWidth, cssHeight, window.devicePixelRatio || 1, readAncestorScale(canvas));
      renderer?.resize(backing.width, backing.height);
    }

    const ro = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect;
      if (!rect) return;
      cssWidth = rect.width;
      cssHeight = rect.height;
      applyBackingStore();
    });
    ro.observe(canvas);
    const stopPixelWatch = watchDevicePixelRatio(applyBackingStore);

    return () => {
      stopPixelWatch();
      ro.disconnect();
      renderer?.destroy();
      renderer = null;
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
