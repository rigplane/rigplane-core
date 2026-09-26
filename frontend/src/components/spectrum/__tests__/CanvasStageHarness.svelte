<script lang="ts">
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
  {#if canvas === 'spectrum'}
    <SpectrumCanvas data={null} />
  {:else}
    <WaterfallCanvas />
  {/if}
</ScaledStage>
