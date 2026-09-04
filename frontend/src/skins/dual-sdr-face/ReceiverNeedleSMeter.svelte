<script lang="ts">
  import { onMount } from 'svelte';
  import { calibratedToSegments, isSmeterCalibrated } from '../../components-v2/meters/smeter-scale';
  import { createSmoother, prefersReducedMotion } from '$lib/utils/smoothing.svelte';

  interface Props { value: number | null; }
  let { value }: Props = $props();
  let available = $derived(value !== null && isSmeterCalibrated());
  const smoother = createSmoother(0.06, 0.1);
  $effect(() => { if (available) smoother.update(calibratedToSegments(value!)); });
  onMount(() => { smoother.start(); return () => smoother.stop(); });
  let angle = $derived(available ? -62 + (smoother.value / 20) * 124 : null);
</script>

<svg class="needle-meter" viewBox="0 0 240 104" role="img" aria-label={available ? 'S meter' : 'S meter unavailable'}>
  {#if available}
    <path d="M20 88 A104 104 0 0 1 220 88" class="meter-arc" />
    <path d="M27 88 A97 97 0 0 1 213 88" class="meter-arc faint" />
    <text x="22" y="100">S</text><text x="109" y="21">9</text><text x="188" y="45">+40</text>
  {/if}
  {#if angle !== null}
    <line data-needle data-reduced-motion={prefersReducedMotion()} x1="120" y1="88" x2="120" y2="27" transform={`rotate(${angle} 120 88)`} class="needle" />
  {:else}
    <text data-meter-unknown x="120" y="70" text-anchor="middle">—</text>
  {/if}
</svg>

<style>
  .needle-meter { width: 100%; height: auto; color: #e8eeee; background: #030708; }
  .meter-arc { fill: none; stroke: currentColor; stroke-width: 3; }
  .faint { stroke-width: 1; opacity: .55; }
  text { fill: currentColor; font: 14px ui-monospace, monospace; }
  .needle { stroke: #f1f7f5; stroke-width: 2; transform-origin: center; }
</style>
