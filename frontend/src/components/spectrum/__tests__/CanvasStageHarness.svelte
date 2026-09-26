<script lang="ts">
  /**
   * jsdom gives a canvas no layout box of its own, and a `ResizeObserver`
   * reports the observed element's own box — so the canvas is given an
   * explicit inline size here, which is the only size jsdom exposes. The
   * stage's box still comes from the test's host stub; nothing here fires
   * an observer callback.
   */
  import { onMount } from 'svelte';
  import ScaledStage from '../../../primitives/stage/ScaledStage.svelte';
  import SpectrumCanvas from '../SpectrumCanvas.svelte';
  import WaterfallCanvas from '../WaterfallCanvas.svelte';

  interface Props {
    canvas: 'spectrum' | 'waterfall';
    nativeW: number;
    nativeH: number;
  }

  let { canvas, nativeW, nativeH }: Props = $props();

  let slot: HTMLDivElement | undefined = $state();

  onMount(() => {
    slot?.querySelectorAll<HTMLElement>('canvas, .spectrum-container').forEach((element) => {
      element.style.width = '200px';
      element.style.height = '100px';
    });
  });
</script>

<ScaledStage {nativeW} {nativeH}>
  <div class="canvas-slot" bind:this={slot}>
    {#if canvas === 'spectrum'}
      <SpectrumCanvas data={null} />
    {:else}
      <WaterfallCanvas />
    {/if}
  </div>
</ScaledStage>
