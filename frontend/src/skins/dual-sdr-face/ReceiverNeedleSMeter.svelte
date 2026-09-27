<script lang="ts">
  import { onMount } from 'svelte';
  import { calibratedToSegments, isSmeterCalibrated } from '../../components-v2/meters/smeter-scale';
  import { createSmoother, prefersReducedMotion } from '$lib/utils/smoothing.svelte';

  interface Props { value: number | null; }
  let { value }: Props = $props();
  // MOR-2720 (MOR-2705 part 4a decision): calibration is a profile fact, so
  // the layout is fixed per radio. `calibratedToSegments` is
  // raw-proportional without a curve, so the needle's geometry MOVES with
  // the raw fraction like the bars do — it needs no calibration curve. What
  // a calibration is needed for is the S scale: on an uncalibrated profile
  // no scale mark ('S', '9', '+40') is drawn, and the accessible name is the
  // bare meter name — never a status word in place of a value.
  let calibrated = $derived(isSmeterCalibrated());
  let available = $derived(value !== null);
  const smoother = createSmoother(0.06, 0.1);
  $effect(() => { if (available) smoother.update(calibratedToSegments(value!)); });
  onMount(() => { smoother.start(); return () => smoother.stop(); });
  let angle = $derived(available ? -62 + (smoother.value / 20) * 124 : null);
</script>

<svg class="needle-meter" viewBox="0 0 240 104" role="img" aria-label="S meter">
  <path d="M20 88 A104 104 0 0 1 220 88" class="meter-arc" />
  <path d="M27 88 A97 97 0 0 1 213 88" class="meter-arc faint" />
  {#if calibrated}
    <text x="22" y="100">S</text><text x="109" y="21">9</text><text x="188" y="45">+40</text>
  {/if}
  <!-- MOR-2692: an unread meter draws nothing — no '—' glyph,
       the LCD segment is unlit. -->
  {#if angle !== null}
    <line data-needle data-reduced-motion={prefersReducedMotion()} x1="120" y1="88" x2="120" y2="27" transform={`rotate(${angle} 120 88)`} class="needle" />
  {/if}
</svg>

<style>
  .needle-meter { width: 100%; height: auto; color: #e8eeee; background: #030708; }
  .meter-arc { fill: none; stroke: currentColor; stroke-width: 3; }
  .faint { stroke-width: 1; opacity: .55; }
  text { fill: currentColor; font: 14px ui-monospace, monospace; }
  .needle { stroke: #f1f7f5; stroke-width: 2; transform-origin: center; }
</style>
