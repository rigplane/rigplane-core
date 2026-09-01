<!--
  ScaledStage — the shared MOR-1160 "fixed-native" presentation primitive.

  Renders `children` at a declared native size (`nativeW` x `nativeH`),
  measures its own host box with `ResizeObserver`, and applies ONE uniform
  `transform: scale(...)` (see `stage-scale.ts`), anchored to the top-left
  corner, never growing past its authored size. Nothing inside the stage
  reflows in response to the host resizing — only the transform on the
  stage element changes.

  CHROME MUST BE A SIBLING OF THE STAGE, NEVER A CHILD. `transform: scale()`
  establishes a new containing block for `position: fixed` descendants, and
  a scaled 44px touch target inside `children` is no longer 44px on screen.
  Toolbars, buttons, or any chrome meant to stay real-sized belongs outside
  `<ScaledStage>`, not inside its `children` snippet.

  The outer "holder" element is what `ResizeObserver` measures; the caller
  must give it a definite box on both axes — a parent that content-sizes its
  rows leaves the holder at the native height, so the height axis never
  constrains.

  MOR-2147: ScaledStage must never write back onto the element it measures.
  It used to collapse the holder's own height to the scaled native height
  after every measurement, which desynchronized the holder's reported box
  from its host's actual size — a shrink the observer then never reports
  (the holder's real box didn't change), and a grow the observer reports on
  the wrong axis (only width still tracked the host once height was pinned).
-->
<script lang="ts">
  import type { Snippet } from 'svelte';
  import { computeStageScale, type StageBox } from './stage-scale';

  interface Props {
    /** Native (authored) width of the stage content, in CSS pixels. */
    nativeW: number;
    /** Native (authored) height of the stage content, in CSS pixels. */
    nativeH: number;
    children: Snippet;
  }

  let { nativeW, nativeH, children }: Props = $props();

  let holder: HTMLDivElement | undefined = $state();
  let scale = $state(1);

  $effect(() => {
    if (!holder) return;

    const native: StageBox = { width: nativeW, height: nativeH };

    // Writes only to `scale` — never back onto `holder` (MOR-2147, see file
    // header). `scale` is written but never read inside this effect.
    const measure = (host: StageBox) => {
      if (host.width <= 0 || host.height <= 0) return;
      scale = computeStageScale(host, native);
    };

    const box = holder.getBoundingClientRect();
    measure({ width: box.width, height: box.height });

    if (typeof ResizeObserver === 'undefined') return;

    const observer = new ResizeObserver((entries) => {
      const entry = entries[0];
      if (!entry) return;
      measure({ width: entry.contentRect.width, height: entry.contentRect.height });
    });
    observer.observe(holder);

    return () => observer.disconnect();
  });
</script>

<div class="scaled-stage-holder" bind:this={holder}>
  <div
    class="scaled-stage"
    style:width="{nativeW}px"
    style:height="{nativeH}px"
    style:transform="scale({scale})"
  >
    {@render children()}
  </div>
</div>

<style>
  .scaled-stage-holder {
    position: relative;
    width: 100%;
    height: 100%;
    overflow: hidden;
  }

  .scaled-stage {
    transform-origin: top left;
  }
</style>
