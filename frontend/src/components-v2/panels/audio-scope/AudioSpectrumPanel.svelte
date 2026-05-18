<script lang="ts">
  import { runtime } from '$lib/runtime';
  import { deriveAudioSpectrumProps } from '$lib/runtime/adapters/panel-adapters';
  import AudioSpectrumCanvas from './AudioSpectrumCanvas.svelte';

  // ── Radio state extraction (via runtime adapter) ──

  let p = $derived(deriveAudioSpectrumProps());

  // ── Scope WS connection ──

  let fftPixels = $state<Uint8Array | null>(null);
  let fftBandwidth = $state(48000);
  let fftPush: ((data: Uint8Array) => void) | null = null;

  // Scope subscription — delegates lifecycle to ScopeController (ADR INV-2, INV-5)
  $effect(() => {
    return runtime.scope.subscribe((frame) => {
      fftPixels = frame.pixels;
      if (frame.endFreq > frame.startFreq) {
        fftBandwidth = frame.endFreq - frame.startFreq;
      }
      fftPush?.(frame.pixels);
    });
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
