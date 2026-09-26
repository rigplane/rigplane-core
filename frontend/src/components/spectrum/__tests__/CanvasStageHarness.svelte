<script lang="ts" module>
  /** The scale the harness publishes. A module `$state`, so a test can move
   *  it the way `ScaledStage` moves its own — and the published getter
   *  changes with it. */
  export const stageScale = $state({ value: 1 });
</script>

<script lang="ts">
  /**
   * Stands in for `ScaledStage` where jsdom cannot: it publishes `stageScale`
   * through the same context and getter shape that `ScaledStage` publishes
   * via `provideStageScale`. The canvas must recompute its backing store when
   * that value changes.
   */
  import { provideStageScale } from '../../../primitives/stage/stage-scale';
  import SpectrumCanvas from '../SpectrumCanvas.svelte';
  import WaterfallCanvas from '../WaterfallCanvas.svelte';

  interface Props {
    canvas: 'spectrum' | 'waterfall';
  }

  let { canvas }: Props = $props();

  provideStageScale(() => stageScale.value);
</script>

{#if canvas === 'spectrum'}
  <SpectrumCanvas data={null} />
{:else}
  <WaterfallCanvas />
{/if}
