<script lang="ts" module>
  import { defaultWaterfallOptions } from '../../../lib/renderers/waterfall-renderer';

  /** The waterfall options the harness publishes. A module `$state`, so a
   *  test can advance the viewport the way SpectrumPanel's panorama motion
   *  advances `waterfallOptions` — and the options effect inside
   *  WaterfallCanvas re-runs with it. */
  export const harnessOptions = $state({ value: defaultWaterfallOptions });
</script>

<script lang="ts">
  /**
   * Stands in for the SpectrumPanel → WaterfallCanvas prop edge where a test
   * must move `options` between a frame's delivery and its rAF paint: the
   * panel recomputes `waterfallOptions` as the panorama glides, and the
   * canvas's options effect applies the newer geometry before the queued
   * frame paints.
   */
  import {
    type WaterfallOptions,
  } from '../../../lib/renderers/waterfall-renderer';
  import type { TraceToken } from '../../../lib/runtime/scope-render-trace';
  import WaterfallCanvas from '../WaterfallCanvas.svelte';

  interface Props {
    onFreqClick?: (hz: number) => void;
    onRegisterPush?: (fn: (data: Uint8Array, options?: WaterfallOptions, token?: TraceToken | null) => void) => void;
  }

  let { onFreqClick, onRegisterPush }: Props = $props();
</script>

<WaterfallCanvas options={harnessOptions.value} {onFreqClick} {onRegisterPush} />
