<script lang="ts">
  import { onMount } from 'svelte';
  import {
    SpectrumRenderer,
    defaultSpectrumOptions,
    type SpectrumOptions,
  } from '../../lib/renderers/spectrum-renderer';
  interface Props {
    data: Uint8Array | null;
    options?: SpectrumOptions;
    spanHz?: number;
    enableAvg?: boolean;
    enablePeakHold?: boolean;
    onRegisterPush?: (fn: (data: Uint8Array) => void) => void;
  }

  let { data, options = defaultSpectrumOptions, spanHz = 0, enableAvg = true, enablePeakHold = true, onRegisterPush }: Props = $props();

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
  let cssWidth = 1;
  let cssHeight = 1;
  let rafId = 0;
  let visible = true;

  // Latest scope pixels — updated directly from WS binary, not via Svelte props
  let latestPixels: Uint8Array | null = null;

  function draw(): void {
    if (!visible) { rafId = 0; return; } // will be restarted by visibilitychange
    const pixels = latestPixels ?? data;
    if (canvas && pixels && cssWidth > 0 && cssHeight > 0) {
      const ctx = canvas.getContext('2d');
      if (ctx) renderer.render(ctx, pixels, cssWidth, cssHeight, options);
    }
    // Always schedule next frame — data may arrive at any time
    rafId = requestAnimationFrame(draw);
  }

  function onVisibilityChange() {
    visible = !document.hidden;
    if (visible && rafId === 0) {
      rafId = requestAnimationFrame(draw);
    }
  }

  onMount(() => {
    // Register push callback with parent — parent handles WS subscription
    onRegisterPush?.((pixels: Uint8Array) => {
      latestPixels = pixels;
    });

    document.addEventListener('visibilitychange', onVisibilityChange);

    // Continuous RAF loop for smooth rendering
    rafId = requestAnimationFrame(draw);

    const ro = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect;
      if (!rect) return;
      cssWidth = Math.max(1, Math.floor(rect.width));
      cssHeight = Math.max(1, Math.floor(rect.height));
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.round(cssWidth * dpr);
      canvas.height = Math.round(cssHeight * dpr);
      const ctx = canvas.getContext('2d');
      ctx?.setTransform(dpr, 0, 0, dpr, 0, 0);
    });
    ro.observe(canvas);

    return () => {
      document.removeEventListener('visibilitychange', onVisibilityChange);
      ro.disconnect();
      cancelAnimationFrame(rafId);
    };
  });
</script>

<div class="spectrum-container">
  <canvas bind:this={canvas}></canvas>
  {#if options.showRfOverlays !== false && spanHz > 0}
    <div class="span-indicators">
      <span class="span-left">{formatOffset(spanHz / -2)}</span>
      <span class="span-right">{formatOffset(spanHz / 2)}</span>
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
