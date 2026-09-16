<script lang="ts">
  import { onMount } from 'svelte';
  import { renderAudioSpectrum, AudioSpectrumRendererState, type SpectrumState } from './audio-spectrum-renderer';
  import type { ControlDisplayDomain } from '$lib/radio/filter-controls';

  interface Props {
    /** FFT pixel data from AudioFftScope (0-160 range) */
    data: Uint8Array | null;
    /** Register a push callback for streaming updates */
    onRegisterPush?: (fn: (data: Uint8Array) => void) => void;
    /** Effective bandwidth of the FFT data in Hz */
    bandwidth: number;
    /** Filter passband width in Hz */
    filterWidth: number;
    /** Max filter width in Hz */
    filterWidthMax: number;
    /** PBT inner raw value (0-255, center=128) */
    pbtInner?: number;
    /** PBT outer raw value (0-255, center=128) */
    pbtOuter?: number;
    /** Manual notch active */
    manualNotch?: boolean;
    /** Manual notch frequency (0-255 raw, or display units when `notchFreqDomain` is present) */
    notchFreq?: number;
    /** Published manual-notch display domain (see `SpectrumState.notchFreqDomain`) */
    notchFreqDomain?: ControlDisplayDomain;
    /** Contour level (0=off, >0=active) */
    contour?: number;
    /** Contour center frequency offset (0-255 raw) */
    contourFreq?: number;
  }

  let {
    data,
    onRegisterPush,
    bandwidth = 48000,
    filterWidth = 2400,
    filterWidthMax = 4000,
    pbtInner = 128,
    pbtOuter = 128,
    manualNotch = false,
    notchFreq = 128,
    notchFreqDomain,
    contour = 0,
    contourFreq = 128,
  }: Props = $props();

  let canvas: HTMLCanvasElement;
  let cssWidth = $state(1);
  let cssHeight = $state(1);
  let rafId = 0;
  let visible = true;
  let mounted = false;
  let latestPixels: Uint8Array | null = null;
  const rendererState = new AudioSpectrumRendererState();

  function scheduleDraw(): void {
    if (!mounted || !visible || rafId !== 0) return;
    rafId = requestAnimationFrame(draw);
  }

  function draw(): void {
    rafId = 0;
    if (!visible) return;
    const pixels = latestPixels ?? data;

    if (canvas && pixels && cssWidth > 1 && cssHeight > 1) {
      const ctx = canvas.getContext('2d');
      if (ctx) {
        const state: SpectrumState = {
          pixels,
          bandwidth,
          filterWidth,
          filterWidthMax,
          pbtInner,
          pbtOuter,
          manualNotch,
          notchFreq,
          notchFreqDomain,
          contour,
          contourFreq,
        };
        renderAudioSpectrum(ctx, cssWidth, cssHeight, state, rendererState);
      }
    }

  }

  function onVisibilityChange() {
    visible = !document.hidden;
    scheduleDraw();
  }

  // A control/readout change must repaint even between FFT frames.
  $effect(() => {
    data; bandwidth; filterWidth; filterWidthMax; pbtInner; pbtOuter;
    manualNotch; notchFreq; notchFreqDomain; contour; contourFreq;
    scheduleDraw();
  });

  onMount(() => {
    onRegisterPush?.((pixels: Uint8Array) => {
      latestPixels = pixels;
      scheduleDraw();
    });

    document.addEventListener('visibilitychange', onVisibilityChange);
    mounted = true;
    scheduleDraw();

    const ro = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect;
      if (!rect) return;
      cssWidth = Math.max(1, Math.floor(rect.width));
      cssHeight = Math.max(1, Math.floor(rect.height));
      const dpr = window.devicePixelRatio || 1;
      canvas.width = Math.round(cssWidth * dpr);
      canvas.height = Math.round(cssHeight * dpr);
      canvas.getContext('2d')?.setTransform(dpr, 0, 0, dpr, 0, 0);
      rendererState.reset();
      scheduleDraw();
    });
    ro.observe(canvas);

    return () => {
      document.removeEventListener('visibilitychange', onVisibilityChange);
      ro.disconnect();
      cancelAnimationFrame(rafId);
      rafId = 0;
      mounted = false;
    };
  });
</script>

<canvas bind:this={canvas} class="audio-spectrum-canvas"></canvas>

<style>
  canvas {
    display: block;
    width: 100%;
    height: 100%;
  }
</style>
