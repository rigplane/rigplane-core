<script lang="ts">
  /**
   * jsdom gives a canvas no layout box of its own, and a `ResizeObserver`
   * reports the observed element's own box — so the canvas is given an
   * explicit one here, through its own style. The stage's box still comes
   * from the test's host stub; nothing here fires an observer callback.
   */
  import ScaledStage from '../../../primitives/stage/ScaledStage.svelte';
  import SpectrumCanvas from '../SpectrumCanvas.svelte';
  import WaterfallCanvas from '../WaterfallCanvas.svelte';

  interface Props {
    canvas: 'spectrum' | 'waterfall';
    nativeW: number;
    nativeH: number;
  }

  let { canvas, nativeW, nativeH }: Props = $props();
</script>

<ScaledStage {nativeW} {nativeH}>
  <div class="canvas-slot">
    {#if canvas === 'spectrum'}
      <SpectrumCanvas data={null} />
    {:else}
      <WaterfallCanvas />
    {/if}
  </div>
</ScaledStage>

<style>
  .canvas-slot {
    width: 200px;
    height: 100px;
  }

  .canvas-slot :global(.spectrum-container),
  .canvas-slot :global(canvas) {
    display: block;
    width: 200px;
    height: 100px;
  }
</style>
