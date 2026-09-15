<script lang="ts">
  import { onMount } from 'svelte';
  import { createSmoother } from '$lib/utils/smoothing.svelte';
  import type { DisplayValue } from '../../semantic/radio-display-model';
  import { meterFill } from './lcd-display-helpers';

  interface Props {
    field: DisplayValue<number>;
  }

  let { field }: Props = $props();

  const smoother = createSmoother(0.06, 0.1);
  let displayFill = $derived(field.state === 'known' ? smoother.value : 0);

  $effect(() => {
    smoother.update(meterFill(field));
  });

  onMount(() => {
    smoother.start();
    return () => smoother.stop();
  });
</script>

<div class="s-meter" data-state={field.state}>
  <span class="meter-label">S</span>
  <div class="meter-track">
    <div class="meter-fill" style:width={`${displayFill * 100}%`}></div>
    <span class="meter-threshold"></span>
  </div>
  <div class="meter-scale" aria-hidden="true">
    <span>1</span><span>3</span><span>5</span><span>7</span><span>9</span><span>+20</span><span>+40</span><span>+60</span>
  </div>
</div>

<style>
  .s-meter { display: grid; grid-template-columns: 30px minmax(0, 1fr); grid-template-rows: 28px 12px; align-items: center; column-gap: 7px; }
  .meter-label { font-size: 11px; font-weight: 700; letter-spacing: 0.1em; }
  .meter-track { position: relative; height: 28px; border: 1px solid var(--ink-soft); }
  .meter-fill { position: absolute; inset-block: 2px; left: 2px; max-width: calc(100% - 4px); background: repeating-linear-gradient(90deg, var(--ink-strong) 0 8px, transparent 8px 10px); }
  .meter-threshold { position: absolute; top: -4px; bottom: -4px; left: 56%; width: 1px; background: var(--ink-strong); }
  .meter-scale { grid-column: 2; display: flex; justify-content: space-between; color: var(--ink-mid); font-size: 8px; }
  .s-meter[data-state='unknown'], .s-meter[data-state='unsupported'] { opacity: 0.3; }
</style>
