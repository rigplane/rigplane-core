<script lang="ts">
  import { scopeSubscriptionSurface } from '$lib/runtime/scope-view';
  import type { ScopeStream } from '$lib/runtime/scope-stream';
  import type { ScopeGeometry } from '$lib/runtime/scope-frame';
  export type LcdAfFftInputState = 'live' | 'missing' | 'stale' | 'unknown' | 'unsupported';

  interface Props {
    /** Typed payload straight from the shared scope stream (MOR-4270). */
    scopeStream?: ScopeStream | null;
    receiver?: 'MAIN' | 'SUB' | null;
    inputState: LcdAfFftInputState;
    receiverActivity: 'active' | 'inactive' | 'unknown';
  }

  let { scopeStream = null, receiver = null, inputState, receiverActivity }: Props = $props();
  let geometry = $state.raw<ScopeGeometry | null>(null);
  let svg = $state<SVGSVGElement>();
  let trace = $state<SVGPathElement>();
  let pixels: Uint8Array | null = null;

  const EMPTY_BINS = new Uint8Array(64);
  function paint() {
    if (!svg || !trace) return;
    const live = inputState === 'live' && receiverActivity === 'active'
      && geometry?.source === 'audio_fft' && geometry.freshness === 'fresh'
      && !geometry.outOfRange && geometry.hasPixels
      && (geometry.receiver === null || geometry.receiver === (receiver === 'MAIN' ? 0 : receiver === 'SUB' ? 1 : -1))
      && pixels !== null && pixels.length > 1;
    const bins = live ? pixels! : EMPTY_BINS;
    svg.dataset.fftMode = live ? 'live' : 'safe-empty';
    svg.classList.toggle('ghosted', !live);
    trace.setAttribute('d', Array.from(bins, (sample, index) => {
    const x = (index / (bins.length - 1)) * 500;
    const y = 92 - Math.max(0, Math.min(255, sample)) / 255 * 82;
    return `${index === 0 ? 'M' : 'L'}${x.toFixed(2)},${y.toFixed(2)}`;
    }).join(' '));
  }
  $effect(() => {
    const stream = scopeStream;
    pixels = null;
    geometry = null;
    if (!stream) { paint(); return; }
    const stopGeometry = stream.geometry.subscribe(value => { geometry = value; });
    const stopFrames = stream.subscribe((frame, slot) => {
      if (slot !== stream.selectedSlot) return;
      pixels = frame.pixels;
      paint();
    });
    return () => { stopFrames(); stopGeometry(); pixels = null; };
  });
  $effect(() => {
    void geometry; void receiver; void inputState; void receiverActivity;
    const slot = scopeStream?.selectedSlot;
    pixels = slot ? scopeStream?.latest(slot)?.pixels ?? null : null;
    paint();
  });
</script>

{#if inputState !== 'unsupported'}
  <svg
    use:scopeSubscriptionSurface={{ stream: scopeStream, receiver: null, view: { follow: 'vfo', spanHz: 48000 } }}
    bind:this={svg}
    data-testid="lcd-af-fft"
    data-fft-mode="safe-empty"
    viewBox="0 0 500 100"
    preserveAspectRatio="none"
    aria-hidden="true"
  >
    {#each [25, 50, 75] as y}<line class="grid" x1="0" x2="500" y1={y} y2={y} />{/each}
    {#each [50, 100, 150, 200, 250, 300, 350, 400, 450] as x}
      <line class="grid" x1={x} x2={x} y1="0" y2="100" />
    {/each}
    <path bind:this={trace} class="fft-trace" fill="none" />
  </svg>
{/if}

<style>
  svg { width: 100%; height: 100%; min-height: 0; overflow: visible; }
  .grid { stroke: var(--ink-ghost); stroke-width: 0.5; }
  .fft-trace { stroke: var(--ink-strong); stroke-width: 1; vector-effect: non-scaling-stroke; }
  svg:global(.ghosted) .fft-trace { stroke: var(--ink-ghost); }
</style>
