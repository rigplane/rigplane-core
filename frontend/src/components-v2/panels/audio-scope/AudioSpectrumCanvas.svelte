<script lang="ts">
  import type { ScopeAudioProjection } from '$lib/runtime/scope-frame';
  import { onMount, untrack } from 'svelte';
  import { renderAudioSpectrum, AudioSpectrumRendererState, type SpectrumState } from './audio-spectrum-renderer';
  import type { ControlDisplayDomain, PbtRange } from '$lib/radio/filter-controls';
  import { CanvasSurface, type CanvasSurfaceFacts } from '../../../lib/canvas/canvas-surface.svelte';
  import { ScopeRenderScheduler } from '../../../lib/canvas/scope-render-scheduler';
  import { getStageScale } from '../../../primitives/stage/stage-scale';

  interface Props {
    projection?: ScopeAudioProjection | null;
    /** FFT pixel data from AudioFftScope (0-160 range) */
    data: Uint8Array | null;
    /** Register a push callback for streaming updates */
    onRegisterPush?: (fn: (data: Uint8Array | null) => void) => void;
    onSurfaceChange?: (facts: CanvasSurfaceFacts) => void;
    /** Effective bandwidth of the FFT data in Hz */
    bandwidth: number;
    /** Filter passband width in Hz */
    filterWidth: number;
    /** Max filter width in Hz */
    filterWidthMax: number;
    /** Native IF-shift in Hz, or non-finite while unread */
    ifShift: number;
    /** PBT inner raw value (0-255, center=128) */
    pbtInner?: number;
    /** PBT outer raw value (0-255, center=128) */
    pbtOuter?: number;
    /** Published PBT raw↔Hz range (see `SpectrumState.pbtRange`) */
    pbtRange?: PbtRange;
    /** Current mode's twin-PBT lattice step (see `SpectrumState.pbtStepHz`) */
    pbtStepHz?: number;
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
    projection = null,
    onRegisterPush,
    onSurfaceChange,
    bandwidth = 48000,
    filterWidth = 2400,
    filterWidthMax = 4000,
    ifShift,
    pbtInner = 128,
    pbtOuter = 128,
    pbtRange,
    pbtStepHz,
    manualNotch = false,
    notchFreq = 128,
    notchFreqDomain,
    contour = 0,
    contourFreq = 128,
  }: Props = $props();

  let canvas: HTMLCanvasElement;

  // MOR-4271: one surface + one scheduler. A size change NEVER resets the
  // renderer state (MOR-4243 class: a renderer-owned resize-coupled reset
  // was the blank-audio-scope bug) — the renderer invalidates its own
  // caches, and the smoothed trace survives a resize.
  const surface = new CanvasSurface({ stageScale: getStageScale() });
  const scheduler = new ScopeRenderScheduler(draw, { active: () => surface.visible && surface.sized });

  let latestPixels: Uint8Array | null = null;
  let pushed = false;
  const rendererState = new AudioSpectrumRendererState();

  function draw(): void {
    if (!surface.visible) return;
    const pixels = pushed ? latestPixels : data;

    if (canvas && surface.sized) {
      const ctx = canvas.getContext('2d');
      if (ctx) {
        if (pixels === null) rendererState.reset();
        const state: SpectrumState = {
          pixels, projection,
          bandwidth,
          filterWidth,
          filterWidthMax,
          ifShift,
          pbtInner,
          pbtOuter,
          pbtRange,
          pbtStepHz,
          manualNotch,
          notchFreq,
          notchFreqDomain,
          contour,
          contourFreq,
        };
        renderAudioSpectrum(ctx, surface.cssW, surface.cssH, state, rendererState);
      }
    }
  }

  // A control/readout change must repaint even between FFT frames.
  $effect(() => {
    data; bandwidth; filterWidth; filterWidthMax; ifShift; pbtInner; pbtOuter;
    pbtRange; pbtStepHz; manualNotch; notchFreq; notchFreqDomain; contour; contourFreq;
    scheduler.wake('view');
  });

  $effect(() => untrack(() => surface.attach(canvas)));
  const stopSizeListener = surface.onSizeChanged(() => scheduler.wake('size'));
  $effect(() => {
    if (surface.visible) scheduler.wake('visibility');
  });
  $effect(() => {
    onSurfaceChange?.(surface.facts);
  });

  let projectionKey: string | undefined;
  $effect(() => {
    const key = JSON.stringify(projection);
    if (key !== projectionKey) { projectionKey = key; rendererState.reset(); scheduler.wake('view'); }
  });

  onMount(() => {
    onRegisterPush?.((pixels: Uint8Array | null) => {
      pushed = true; latestPixels = pixels;
      scheduler.wake('frame');
    });
    return () => {
      stopSizeListener();
      scheduler.dispose();
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
