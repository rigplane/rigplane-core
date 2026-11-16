<script lang="ts">
  /**
   * Hosts `StackedTraceCanvas` the way the spectrum pane does (MOR-4463):
   * the host owns a real `CanvasSurface` attached to the pane element.
   */
  import { untrack } from 'svelte';
  import StackedTraceCanvas from '../StackedTraceCanvas.svelte';
  import { CanvasSurface } from '../../../lib/canvas/canvas-surface.svelte';
  import { defaultSpectrumOptions, type SpectrumOptions } from '../../../lib/renderers/spectrum-renderer';
  import type { StackedTraceLocate } from '../../../lib/renderers/stacked-trace-geometry';
  import type { ScopeRenderTrace, TraceToken } from '../../../lib/runtime/scope-render-trace';

  interface Props {
    centerHz?: number;
    trace?: ScopeRenderTrace | null;
    /** Options of the host pane laid over the defaults. */
    patch?: Partial<SpectrumOptions>;
    onLocate?: (locate: StackedTraceLocate | null) => void;
    onPush: (fn: (pixels: Uint8Array, token?: TraceToken | null) => void) => void;
    onUnavailable: () => void;
  }

  let { centerHz = 14_050_000, trace = null, patch = {}, onLocate, onPush, onUnavailable }: Props = $props();

  const surface = new CanvasSurface();
  let host = $state<HTMLDivElement>();
  $effect(() => {
    const element = host;
    if (element) return untrack(() => surface.attach(element));
  });
  let options = $derived({ ...defaultSpectrumOptions, centerHz, spanHz: 100_000, tuneHz: centerHz, passbandHz: 2_400, mode: 'USB', ...patch });
</script>

<div bind:this={host}>
  <StackedTraceCanvas {options} colorScheme="classic" {surface} {trace} onRegisterPush={onPush} onRegisterLocate={onLocate} {onUnavailable} />
</div>
