<script lang="ts">
  /**
   * Hosts `SpectrumCanvas` the way `SpectrumPanel` does for MOR-4463: the
   * host owns the `CanvasSurface`, attaches it to the pane element and hands
   * the canvas the surface and (optionally) the waterfall palette.
   */
  import { untrack } from 'svelte';
  import SpectrumCanvas from '../SpectrumCanvas.svelte';
  import { CanvasSurface } from '../../../lib/canvas/canvas-surface.svelte';
  import { defaultSpectrumOptions } from '../../../lib/renderers/spectrum-renderer';
  import type { ColorSchemeName } from '../../../lib/renderers/waterfall-renderer';
  import type { StackedTraceLocate } from '../../../lib/renderers/stacked-trace-geometry';

  interface Props {
    colorScheme?: ColorSchemeName;
    showRfOverlays?: boolean;
    /** False for a host that leaves the canvas to size itself. */
    hostSurface?: boolean;
    onLocate?: (locate: StackedTraceLocate | null) => void;
    onPush: (fn: (pixels: Uint8Array) => void) => void;
  }

  let { colorScheme, showRfOverlays = true, hostSurface = true, onLocate, onPush }: Props = $props();

  const surface = new CanvasSurface();
  let host = $state<HTMLDivElement>();
  $effect(() => {
    const element = host;
    if (element) return untrack(() => surface.attach(element));
  });
  let options = $derived({ ...defaultSpectrumOptions, centerHz: 14_050_000, spanHz: 100_000, showRfOverlays });
</script>

<div bind:this={host}>
  <SpectrumCanvas data={null} {options} spanHz={100_000} surface={hostSurface ? surface : undefined} {colorScheme} onRegisterPush={onPush} onRegisterLocate={onLocate} />
</div>
