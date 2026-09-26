<script lang="ts">
  /**
   * Stands in for `ScaledStage` where jsdom cannot: it publishes a scale
   * through the same context, with the same getter shape, that `ScaledStage`
   * publishes via `provideStageScale`. The test moves `scale`; the canvas
   * must recompute its backing store from that alone.
   */
  import { provideStageScale } from '../../../primitives/stage/stage-scale';
  import SpectrumCanvas from '../SpectrumCanvas.svelte';
  import WaterfallCanvas from '../WaterfallCanvas.svelte';

  interface Props {
    canvas: 'spectrum' | 'waterfall';
    scale?: number;
  }

  let { canvas, scale = 1 }: Props = $props();

  // A prop is not reactive state. Copying it into `$state` is what lets the
  // published getter change when the test assigns `scale`, the same way
  // `ScaledStage` publishes its own `$state`.
  let published = $state(scale);
  $effect(() => { published = scale; });

  provideStageScale(() => published);
</script>

{#if canvas === 'spectrum'}
  <SpectrumCanvas data={null} />
{:else}
  <WaterfallCanvas />
{/if}
