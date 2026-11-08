<script lang="ts">
  import { audioScopeFactsRevision, audioScopeProjection } from '$lib/runtime/adapters/scope-adapter';
  import { scopeSubscriptionSurface } from '$lib/runtime/scope-view';
  import { onMount } from 'svelte';
  import { presentationResources, runtime } from '$lib/runtime/frontend-runtime';
  import { deriveAudioSpectrumProps } from '$lib/runtime/adapters/panel-adapters';
  import AudioSpectrumCanvas from './AudioSpectrumCanvas.svelte';

  // ── Radio state extraction (via runtime adapter) ──

  let p = $derived(deriveAudioSpectrumProps());

  // ── Scope WS connection ──

  let fftGeometry = $state.raw<import('$lib/runtime/scope-frame').ScopeGeometry | null>(null);
  let fftProjection = $derived(audioScopeProjection(fftGeometry, runtime.state));
  let fftBandwidth = $state(48000);
  let fftPush: ((data: Uint8Array | null) => void) | null = null;
  let panel: HTMLDivElement;
  let visible = $state(typeof IntersectionObserver === 'undefined');

  onMount(() => {
    if (typeof IntersectionObserver === 'undefined') {
      visible = true;
      return;
    }
    const observer = new IntersectionObserver(([entry]) => {
      visible = entry?.isIntersecting === true;
    }, { rootMargin: '64px' });
    observer.observe(panel);
    return () => observer.disconnect();
  });

  // Scope frames and resource demand have separate lifetimes (ADR INV-2, INV-5).
  $effect(() => {
    runtime.scope.registerPresentationDriver(presentationResources);
    if (!visible) return;
    const lease = presentationResources.acquire('audio-fft', 'AudioSpectrumPanel');
    // MOR-4270: one stream sink — pixels go straight to the canvas push;
    // the rarely-changing bandwidth is a geometry fact (F15 double
    // scheduling is gone: no per-frame `$state` pixels).
    const stream = runtime.scope.stream('audio_fft');
    let hasPixels = false;
    const clearPixels = () => { if (hasPixels) fftPush?.(null); hasPixels = false; };
    const stopGeometry = stream.geometry.subscribe((geometry) => {
      fftGeometry = geometry;
      if (geometry === null) clearPixels();
      if (geometry && geometry.view.endHz > geometry.view.startHz) {
        fftBandwidth = geometry.view.endHz - geometry.view.startHz;
      }
    });
    const unsubscribe = stream.subscribe((frame) => {
      if (!visible || document.hidden) return;
      hasPixels = frame.pixels !== null; fftPush?.(frame.pixels);
    });
    let released = false;
    return () => {
      if (released) return;
      released = true;
      clearPixels();
      unsubscribe();
      stopGeometry();
      presentationResources.release(lease);
    };
  });
</script>

<div class="audio-spectrum-panel" bind:this={panel} data-streaming={visible} use:scopeSubscriptionSurface={{
  stream: runtime.scope.stream('audio_fft'), receiver: null,
  view: { follow: 'vfo', spanHz: 48000 }, revision: audioScopeFactsRevision(runtime.state, runtime.caps),
}}>
  <AudioSpectrumCanvas
    projection={fftProjection}
    data={null}
    onRegisterPush={(fn) => { fftPush = fn; }}
    bandwidth={fftBandwidth}
    filterWidth={p.filterWidth}
    filterWidthMax={p.filterWidthMax}
    ifShift={p.ifShift}
    pbtInner={p.pbtInner}
    pbtOuter={p.pbtOuter}
    pbtRange={p.pbtRange}
    pbtStepHz={p.pbtStepHz}
    manualNotch={p.manualNotch}
    notchFreq={p.notchFreq}
    notchFreqDomain={p.notchFreqDomain}
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
