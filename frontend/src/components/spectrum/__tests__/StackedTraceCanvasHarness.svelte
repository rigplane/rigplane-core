<script lang="ts">
  /**
   * Hosts `StackedTraceCanvas` the way the spectrum pane does (MOR-4463):
   * the host owns a real `CanvasSurface` attached to the pane element.
   */
  import { untrack } from 'svelte';
  import StackedTraceCanvas from '../StackedTraceCanvas.svelte';
  import { CanvasSurface } from '../../../lib/canvas/canvas-surface.svelte';
  import { defaultSpectrumOptions } from '../../../lib/renderers/spectrum-renderer';

  interface Props {
    centerHz?: number;
    onPush: (fn: (pixels: Uint8Array) => void) => void;
    onUnavailable: () => void;
  }

  let { centerHz = 14_050_000, onPush, onUnavailable }: Props = $props();

  const surface = new CanvasSurface();
  let host = $state<HTMLDivElement>();
  $effect(() => {
    const element = host;
    if (element) return untrack(() => surface.attach(element));
  });
  let options = $derived({ ...defaultSpectrumOptions, centerHz, spanHz: 100_000, tuneHz: centerHz, passbandHz: 2_400, mode: 'USB' });
</script>

<div bind:this={host}>
  <StackedTraceCanvas {options} colorScheme="classic" {surface} onRegisterPush={onPush} {onUnavailable} />
</div>
