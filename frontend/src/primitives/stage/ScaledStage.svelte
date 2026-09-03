<!--
  ScaledStage — the shared MOR-1160 "fixed-native" presentation primitive.

  Renders `children` at a declared native size (`nativeW` x `nativeH`),
  measures its own host box with `ResizeObserver`, and applies ONE uniform
  `transform: scale(...)` (see `stage-scale.ts`), anchored by default to the
  top-left corner — or, via the `anchor` prop, kept centred in the host at
  every scale by an added `translate()` computed from that same scale (see
  `computeStageCenterOffset`) — never growing past its authored size.
  Nothing inside the stage reflows in response to the host resizing — only
  the transform on the stage element changes.

  MOR-2251: `flex-shrink: 0` on `.scaled-stage` (below) makes one specific
  way that guarantee could be broken mechanically impossible, rather than
  relying on this paragraph: an ancestor rule that turns this element into
  a flex item — e.g. a `display: flex` re-added to `.scaled-stage-holder`,
  the exact shape of the MOR-2153 F1 defect — can no longer shrink it off
  its authored size. It does not block every conceivable override; see the
  property's own comment for what was checked about the equivalent grid
  case.

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
  import { computeStageCenterOffset, computeStageScale, type StageBox } from './stage-scale';

  interface Props {
    /** Native (authored) width of the stage content, in CSS pixels. */
    nativeW: number;
    /** Native (authored) height of the stage content, in CSS pixels. */
    nativeH: number;
    /**
     * Where the scaled box sits inside the measured host box. `'top-left'`
     * (the default) preserves today's behaviour exactly: the box stays
     * pinned to the holder's top-left corner at every scale, no translate.
     * `'center'` adds a translate — computed from the same `scale`, so it
     * stays exact at every scale, not only scale 1 — that keeps the box's
     * own center coincident with the host's, regardless of the host's
     * layout mode (flex, grid, or plain flow): it depends only on the
     * measured host box, not on how an ancestor positioned it.
     */
    anchor?: 'top-left' | 'center';
    /**
     * Lower bound on the computed scale, in the same ratio units as the
     * scale itself. Omitted (the default), there is no floor and the stage
     * shrinks to whatever fits, which is what every consumer got before
     * this prop existed. Given, the stage stops shrinking at this ratio and
     * the holder scrolls the part that no longer fits (owner decision
     * relayed 2026-09-02: an operator at a small window is better served by
     * controls too large to fit than by a face too small to read).
     */
    minScale?: number;
    children: Snippet;
  }

  let { nativeW, nativeH, anchor = 'top-left', minScale, children }: Props = $props();

  let holder: HTMLDivElement | undefined = $state();
  let scale = $state(1);
  let offsetX = $state(0);
  let offsetY = $state(0);
  /** Whether `minScale` is currently raising the scale above the fit — i.e.
   *  whether the stage is painted larger than the measured host box. Gates
   *  the holder's `overflow`; see the markup below. */
  let floorActive = $state(false);

  // `anchor === 'top-left'` ignores `offsetX`/`offsetY` entirely (see
  // `transform` below), so the default's rendered `transform` string is
  // byte-identical to before this prop existed — no `translate()` prefix.
  let transform = $derived(
    anchor === 'center' ? `translate(${offsetX}px, ${offsetY}px) scale(${scale})` : `scale(${scale})`,
  );

  $effect(() => {
    if (!holder) return;

    const native: StageBox = { width: nativeW, height: nativeH };

    // Writes only to `scale`/`offsetX`/`offsetY`/`floorActive` — never back
    // onto `holder`'s size (MOR-2147, see file header). `scale` and
    // `floorActive` are written but never READ as
    // `$state` inside this effect: `computeStageCenterOffset` takes the
    // local `nextScale` below instead of the `scale` binding. Reading
    // `scale` here would register it as a dependency of this effect, and
    // since the write just above just changed it, the effect would
    // self-invalidate and re-run once per mount — tearing down and
    // reconstructing the `ResizeObserver` below for no observable benefit
    // (the re-run recomputes the same scale from the same unchanged host
    // box). Regression found by an independent verifier on this branch.
    const measure = (host: StageBox) => {
      if (host.width <= 0 || host.height <= 0) return;
      const fit = computeStageScale(host, native);
      const nextScale = computeStageScale(host, native, minScale);
      scale = nextScale;
      floorActive = nextScale > fit;
      const offset = computeStageCenterOffset(host, native, nextScale);
      offsetX = offset.x;
      offsetY = offset.y;
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

<!-- `overflow` is gated on the floor here rather than declared in the
     `<style>` block below: `auto` only while the floor is holding the stage
     past this box, so that overflow stays reachable, and `hidden` otherwise.
     Unconditional `auto` is not inert when the floor is idle. The scrollable
     area is the UNTRANSFORMED layout box of `.scaled-stage`, which the
     inline width/height below hold at the native size at every scale — so
     it overflows the host at every scale below 1. Measured in Chrome
     on `LcdLayout variant="peer-split"` at a 1280x800 viewport: an 802x337
     holder with a 1280x540 scroll area, scrollable 478x203 past a stage the
     fit had already sized to fit it (scale 0.624, painted 799x337).
     `hidden` takes away the scrollbars and the wheel path, not the area:
     with the gate in place the same holder still reports `scrollWidth`/
     `scrollHeight` 1280x540 and still scrolls 478x203 from script. -->
<div class="scaled-stage-holder" style:overflow={floorActive ? 'auto' : 'hidden'} bind:this={holder}>
  <div class="scaled-stage" style:width="{nativeW}px" style:height="{nativeH}px" style:transform>
    {@render children()}
  </div>
</div>

<style>
  .scaled-stage-holder {
    position: relative;
    width: 100%;
    height: 100%;
    /* No `overflow` here on purpose — it is an inline, floor-gated value on
       the element itself. See the comment above the markup. */
  }

  .scaled-stage {
    transform-origin: top left;
    /* MOR-2251: makes the MOR-2153 defect class unrepresentable rather than
       patching the one instance of it. Any ancestor that turns this element
       into a flex item — including a `display: flex` re-added to its own
       `.scaled-stage-holder`, the exact shape of the deleted rule this
       guards against — can no longer shrink it off its native size.
       Verified NOT to be needed for the equivalent grid case: a headless
       Chrome probe (four ancestor variants: `place-items: center`, no
       `place-items` at all, an explicit `1fr` track, and `place-items:
       stretch`) found a grid item that declares an explicit width/height,
       as this element always does via the `style:width`/`style:height`
       above, is never shrunk by grid's stretch defaults — those fall back
       to `start` positioning at the item's own size once a definite
       preferred size is present, so `flex-shrink` (a no-op outside a flex
       formatting context) has nothing to correct there. */
    flex-shrink: 0;
  }
</style>
