<script lang="ts">
  import { presentationResources, runtime } from '$lib/runtime/frontend-runtime';
  import { deriveAudioSpectrumProps } from '$lib/runtime/adapters/panel-adapters';
  import AudioSpectrumCanvas from './AudioSpectrumCanvas.svelte';

  // ── Radio state extraction (via runtime adapter) ──

  let p = $derived(deriveAudioSpectrumProps());

  // ── Scope WS connection ──

  let fftPixels = $state<Uint8Array | null>(null);
  let fftBandwidth = $state(48000);
  let fftPush: ((data: Uint8Array) => void) | null = null;

  // Scope frames and resource demand have separate lifetimes (ADR INV-2, INV-5).
  $effect(() => {
    runtime.scope.registerPresentationDriver(presentationResources);
    const lease = presentationResources.acquire('audio-fft', 'AudioSpectrumPanel');
    const unsubscribe = runtime.scope.subscribe((frame) => {
      fftPixels = frame.pixels;
      if (frame.endFreq > frame.startFreq) {
        fftBandwidth = frame.endFreq - frame.startFreq;
      }
      fftPush?.(frame.pixels);
    });
    let released = false;
    return () => {
      if (released) return;
      released = true;
      unsubscribe();
      presentationResources.release(lease);
    };
  });
</script>

<div class="audio-spectrum-panel">
  <AudioSpectrumCanvas
    data={fftPixels}
    onRegisterPush={(fn) => { fftPush = fn; }}
    bandwidth={fftBandwidth}
    filterWidth={p.filterWidth}
    filterWidthMax={p.filterWidthMax}
    pbtInner={p.pbtInner}
    pbtOuter={p.pbtOuter}
    manualNotch={p.manualNotch}
    notchFreq={p.notchFreq}
    contour={p.contour}
    contourFreq={p.contourFreq}
  />
</div>

<style>
  .audio-spectrum-panel {
    width: 100%;
    aspect-ratio: 2 / 1;
    height: auto;
    min-height: 80px;
    max-height: 180px;
    background: var(--panel, #121922);
    border: 1px solid var(--panel-border, #1e293b);
    border-radius: var(--radius, 8px);
    overflow: hidden;
  }
</style>
