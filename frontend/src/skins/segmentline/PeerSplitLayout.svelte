<!--
  Peer Split Layout (MOR-2153) — the `peer-split` glass CHASSIS.

  SCOPE CORRECTION (owner ruling, 2026-09-01): the coordinator's original
  brief for this ticket assumed the layout manifest already declared the
  full five-band zone set (status/DSP/memory rails, offsets, mode/filter
  cells). It does not — `presentation/layouts/segmentline-declarations.ts`
  currently declares exactly one zone (`peer-columns`: vfo+rxTx). That work
  is MOR-2151(cont.), under review elsewhere, and this file does not touch
  it (`presentation/layouts/**` and every `*-declarability.test.ts` are
  explicitly out of scope here).

-->
<script lang="ts">
  // MOR-1257 (N4): the components-v2 theme layer is code-split per skin —
  // see `DualReceiverCockpit.svelte`'s identical import for the full
  // rationale (MOR-1070 evidence run finding N4).
  import '../../components-v2/theme/index';
  import ScaledStage from '../../primitives/stage/ScaledStage.svelte';
  import SemanticRadioSurfaces from '../../components-v2/wiring/SemanticRadioSurfaces.svelte';

  /** Matches `SEGMENTLINE_GLASS_STAGE` in
   *  `presentation/layouts/segmentline-declarations.ts` — duplicated here
   *  because `ScaledStage` takes `nativeW`/`nativeH` as props and reads no
   *  manifest (the manifest's own native-size declaration stays
   *  declaration-only outside `presentation/layouts/` per MOR-1247; see
   *  that file's own header). */
  const NATIVE_W = 1280;
  const NATIVE_H = 540;

  /** Wall-clock only — see the file header. `Date`, not radio state. */
  function clockLabel(date: Date): { utc: string; local: string } {
    const pad = (n: number) => String(n).padStart(2, '0');
    return {
      utc: `${pad(date.getUTCHours())}:${pad(date.getUTCMinutes())}Z`,
      local: `${pad(date.getHours())}:${pad(date.getMinutes())}`,
    };
  }

  let now = $state(new Date());
  $effect(() => {
    const id = setInterval(() => { now = new Date(); }, 30_000);
    return () => clearInterval(id);
  });
  let clock = $derived(clockLabel(now));
</script>

<div class="peer-split-holder">
  <ScaledStage nativeW={NATIVE_W} nativeH={NATIVE_H}>
    <div class="peer-split-glass" data-testid="peer-split-glass">
      <div class="peer-split-clock" data-testid="peer-split-clock" aria-label="Clock">
        <span data-testid="peer-split-clock-utc">{clock.utc}</span>
        <span data-testid="peer-split-clock-local">{clock.local}</span>
      </div>
      <SemanticRadioSurfaces strips="dual" />
    </div>
  </ScaledStage>
</div>

<style>
  /* Lesson 4: a definite box on both axes for ScaledStage's own
     ResizeObserver to measure. Verified in a real browser — see the file
     header and the MOR-2153 build report. */
  .peer-split-holder {
    display: flex;
    height: 100%;
    min-height: 0;
  }
  /* ScaledStage's own root (`scaled-stage-holder`) is `width:100%;
     height:100%` against ITS containing block; as a flex child of the
     holder above it also needs to actually claim that space rather than
     content-size to zero. Reached via :global() rather than edited in the
     shared primitive — the same "chrome is a sibling, stage stays
     untouched" discipline applies to the primitive's own file. */
  .peer-split-holder :global(.scaled-stage-holder) {
    flex: 1;
    min-width: 0;
    min-height: 0;
  }

  .peer-split-glass {
    height: 100%;
    position: relative;
    overflow: hidden;
    box-sizing: border-box;
    padding: 14px;
    border: 2px solid var(--dl-segmentline-bezel-edge, #8a7020);
    border-radius: 10px;
    background: var(--dl-segmentline-glass, #c8a030);
    color: var(--dl-segmentline-ink-strong, rgba(26, 16, 0, 1));
  }

  /* Lesson 3: the clock is instrument face, inside the scaled stage, never
     app chrome. Positioned absolute against `.peer-split-glass` itself,
     which sets `position: relative` above. No `!important` needed: the
     specificity tie a previous draft of this rule fought (MOR-2153 review)
     existed only because this element wore the `dl-glass` class, so
     segmentline.css's `.dl-glass > *` / `.rx-tx-surface > *` promotion rule
     could reach it — it no longer wears that class (see the glass rule
     above and the markup below), so that rule cannot match this element at
     all and there is nothing left to out-rank. */
  .peer-split-glass > .peer-split-clock {
    position: absolute;
    top: 14px;
    right: 14px;
    height: 34px;
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.1em;
    pointer-events: none;
  }

  /* Lesson 1 + 2: the grid host is the WIRING'S OWN root, reached by
     descendant combinator, not the stage. Compiled, this rule is
     `.peer-split-glass.s-xxx :global(.semantic-surfaces.semantic-surfaces)`
     — 4 classes (0,4,0), doubling `.semantic-surfaces` deliberately —
     against the wiring's own base rule, compiled to `.semantic-surfaces
     .s-yyy` (2 classes, 0,2,0). (0,4,0) outranks (0,2,0) regardless of
     which component's <style> the bundler places first — CONFIRMED live
     (`getComputedStyle(surfaces).display === 'grid'`), unlike the clock
     rule below, which needed the same measurement to find it does NOT win
     this way. Every other override below only ADDS grid-row/grid-column,
     properties nothing else sets on these elements, so no such doubling is
     needed there. */
  .peer-split-glass :global(.semantic-surfaces.semantic-surfaces) {
    display: grid;
    grid-template-columns: 1fr 1fr;
    grid-template-rows: auto 1px auto auto minmax(72px, 1fr) 40px 34px;
    column-gap: 8px;
    /* The wiring's own base rule sets `gap: 8px` (a flex gap in its
       unmodified form), which — MEASURED, real browser — survives as an
       inherited `row-gap: 8px` here: overriding `display`/`grid-template-
       *`/`column-gap` above does not touch that longhand, since cascade
       resolution runs per property, not per rule. Six row gaps at 8px was
       48px of the total overflow past the native 540px height (`.semantic-
       surfaces` `scrollHeight` measured taller than `clientHeight` before
       this line existed). Zeroed explicitly rather than left inherited. */
    row-gap: 0;
  }
  /* The 1px ink rule between status rail and body. No real DOM element for
     it — `.semantic-surfaces` is the wiring's own root, not this file's
     markup — so it is drawn as a pseudo-element on the grid host itself,
     occupying row 2 like any other grid item. */
  .peer-split-glass :global(.semantic-surfaces.semantic-surfaces)::before {
    content: '';
    grid-row: 2;
    grid-column: 1 / -1;
    background: var(--dl-segmentline-ink-soft, rgba(26, 16, 0, 0.34));
  }
  /* Row 1 is `auto`, not the archived geometry's fixed 34px: MEASURED (real
     browser) that `RxTxSurface`/`TxAuxSurface` render normal-density
     buttons and sliders (13 controls for txAux alone), not the compact
     icon-sized flag cells the mockup's 34px status rail assumed. A fixed
     34px + `overflow: hidden` was tried first and clipped ~90% of both
     surfaces invisibly — real content hidden behind a band that LOOKED
     complete, exactly the "screenshot that hides which half is real"
     shape to avoid. `auto` shows what is actually there. */
  .peer-split-glass :global(.rx-tx-zone) {
    grid-row: 1;
    grid-column: 1;
  }
  .peer-split-glass :global(.tx-aux-surface) {
    grid-row: 1;
    grid-column: 2;
  }
  .peer-split-glass :global(.cockpit-global-row) {
    grid-row: 3;
    grid-column: 1 / -1;
  }
  .peer-split-glass :global(.channel-strips) {
    grid-row: 4;
    grid-column: 1 / -1;
  }
  .peer-split-glass :global(.meters-surface) {
    grid-row: 5;
    grid-column: 1;
    overflow: auto;
  }
  .peer-split-glass :global(.scope-display-surface) {
    grid-row: 5;
    grid-column: 2;
    overflow: auto;
  }
  /* Rows 6 (dsp-rail) and 7 (memory-rail) intentionally have no selector
     below: nothing currently mounts into them (see the file header), and
     an empty grid row with no assigned item is simply empty space at the
     declared height — not a placeholder to build. */
</style>
