<!--
  Peer Split Layout (MOR-2153) — the `peer-split` glass CHASSIS.

  SCOPE CORRECTION (owner ruling, 2026-09-01): the coordinator's original
  brief for this ticket assumed the layout manifest already declared the
  full five-band zone set (status/DSP/memory rails, offsets, mode/filter
  cells). It does not — `presentation/layouts/segmentline-declarations.ts`
  currently declares exactly one zone (`peer-columns`: vfo+rxTx), and
  `docs/plans/2026-09-01-segmentline-peer-split.md` §7 sequences "the
  manifest's remaining zones" (row 7) BEFORE "compose the glass" (row 9,
  this ticket) — an order the brief itself violated. That work is
  MOR-2151(cont.), under review elsewhere, and this file does not touch it
  (`presentation/layouts/**` and every `*-declarability.test.ts` are
  explicitly out of scope here).

  What this file builds instead is the CHASSIS: everything that does not
  depend on a declared zone. Concretely, against
  `~/Projects/rigplane-archives/segmentline-handoff-2026-09-01/handoff/
  INTEGRATION.md` §4's five-band geometry (status rail 34 / rule 1 /
  receiver body flexible / DSP rail 40 / memory rail 34, native 1280x540,
  14px padding inside a 2px bezel, 10px radius):

    POPULATED today (real DOM the dual composition already emits with no
    zone declared — `SemanticRadioSurfaces.svelte`'s `strips="dual"`
    branch): the two VFO channel strips (`.channel-strips`), the
    radio-wide split/dual-watch row (`.cockpit-global-row`), the RX/TX
    status+action zone (`.rx-tx-zone`), and the three optional surfaces
    whose `allowBare` default is still `true` — `txAux`, `meters`,
    `scopeDisplay` (`SemanticRadioSurfaces.svelte` lines ~1410-1436).

    EMPTY, reserved (grid rows with no assigned child — see the `<style>`
    block below): the DSP rail (`dsp`/`rfFrontEnd`) and the memory rail
    (`band`, plus the geometry table's "telemetry" cell, which names no
    surface in `contract.ts`'s `SEMANTIC_SURFACE_NAMES` — left unassigned
    rather than guessed at). Every one of these renders NOTHING at all
    under the current manifest: `zoned(..., allowBare=false)` (MOR-2150)
    means "no declared zone" is "no DOM", not "bare". Those two rows keep
    the archived table's fixed 40/34px, since nothing occupies them yet to
    prove otherwise either way.

    NOT both fully visible without scrolling, at the native 540px height,
    with this fixture's actual content: MEASURED (real browser) `.semantic-
    surfaces` `scrollHeight` 531 against a `clientHeight` of 508 — the
    `auto` rows above (status/global/vfo) already claim more of the native
    height than is left for the two reserved rows plus the 72px meters/
    scope floor. Row 6 (DSP rail, 40px) sits fully inside the visible
    508px. Row 7 (memory rail, 34px) does not: only its first ~11.5px
    shows; the rest needs the scroll the base wiring's own `overflow: auto`
    on `.semantic-surfaces` (untouched here) already provides. This will
    move once real content lands in either row — recorded as the current
    fixture's measurement, not a claim about every future one.

    This file's placement rules below select surface classes directly
    (`.tx-aux-surface`, `.meters-surface`, `.scope-display-surface`) —
    NOT `.surface-zone`, the wrapper `zoned()` (MOR-2150,
    `SemanticRadioSurfaces.svelte`) actually emits once a zone IS declared
    for a surface. MEASURED (real browser, a synthetic plan declaring all
    fourteen surfaces): a declared zone puts `.surface-zone` in the grid,
    not the surface's own class, so it lands by the browser's implicit
    auto-placement — observed landing at rows 6/7/9 — and every
    surface-class rule below goes inert for that surface the moment a zone
    owns it. What (if anything) should route a declared zone to a specific
    row is undecided and unwritten here; that decision belongs to
    MOR-2151(cont.)'s own ticket, not a prediction in this file.

    NOT the archived table's fixed 34px status rail: MEASURED (real
    browser) that `RxTxSurface`/`TxAuxSurface` render normal-density
    buttons and sliders — 13 controls for `TxAuxSurface` alone — not the
    compact icon-sized flag cells 34px assumes. Row 1 is `auto`; see the
    `<style>` block's own note at that rule for what a fixed 34px actually
    did (clipped ~90% of both surfaces invisibly).

  Everything else in this header documents the four hard-won lessons the
  archived package's own author did not verify by running (the brief's own
  citation):

  1. THE GRID HOST IS `.semantic-surfaces`, NOT THE STAGE. `<ScaledStage>`'s
     only child is `SemanticRadioSurfaces`'s own root
     (`.semantic-surfaces`), so every zone is a GRANDCHILD of the stage —
     a grid declared on the stage places nothing. The `:global(...)`
     selectors below are rooted at `.semantic-surfaces` itself.
  2. THE GRID IS TWO COLUMNS UNIFORMLY (`grid-template-columns: 1fr 1fr`
     below), not per-row pairing logic — rows 6/7 (dsp-rail/memory-rail)
     inherit the same two tracks because the WHOLE grid does, not because
     anything here pairs DSP against front-end or memory against
     telemetry. MEASURED (see the reserved-rows note above): with a
     synthetic plan declaring `filter`/`dsp`/`rfFrontEnd`/`band`, the
     browser's own implicit auto-placement — not a design choice made
     here — put `filter`|`dsp` in row 6 and `rfFrontEnd`|`band` in row 7,
     an artifact of `zoned()`'s call order in `SemanticRadioSurfaces.svelte`,
     not anything this file controls or can promise for a different zone
     set.
  3. CHROME IS A SIBLING OF THE STAGE, NEVER A CHILD. `.peer-split-holder`
     wraps `<ScaledStage>`; nothing chrome-like lives inside its
     `children` snippet. The wall-clock below is NOT chrome by this
     definition — it is instrument face, same category as a real LCD's
     printed clock, so it lives inside the scaled stage and scales with
     everything else.
  4. THE STAGE'S PARENT NEEDS A DEFINITE HEIGHT ON BOTH AXES. `.peer-split-
     holder` itself is `display: flex; height: 100%; min-height: 0`;
     `flex: 1` lives one level down, on `ScaledStage`'s own
     `.scaled-stage-holder` root (reached via `:global()`, see the
     `<style>` block). Verified against the real `ScaledStage` in a
     browser (jsdom does no grid/flex sizing — see
     `ScaledStage.svelte`'s own header, and the MOR-2153 build report for
     the exact render command and what it showed).

  TX PERIMETER: `.dl-glass` (segmentline.css) already carries the whole
  mechanism — `.dl-glass[data-tx='active']` is the hot-bezel-plus-glow
  rule the tokens/renderer describe as "frame, not wash". This file
  applies the `dl-glass` class but does NOT set `data-tx` from live TX
  state: `AppGlobalHost.svelte`'s own header is explicit that "layouts and
  skins must not host" the TX/fault indication — mounting a second
  `getAppTxController()` subscription here to drive a border color would
  cross that boundary, not stay inside "all chrome, none zone-dependent"
  as the brief characterized it. Recorded as a real gap, not silently
  wired around: `data-tx` is unset, so the bezel currently never lights.

  WALL CLOCK: UTC/local time is not radio state — nothing in
  `semantic/radio-view-model.ts` or the segmentline renderers carries it
  (confirmed by grep — no clock/UTC field exists anywhere in that layer),
  because there is nothing to carry: the operator's own local and UTC time
  is already known to the client. Rendered directly below from `Date`,
  ticked every 30 seconds; this is not a violation of "radio truth flows
  through the model" because it is not radio truth.
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
    <div class="dl-glass peer-split-glass" data-testid="peer-split-glass">
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
  }

  /* Lesson 3: the clock is instrument face, inside the scaled stage, never
     app chrome. `.dl-glass > *` (segmentline.css) already promotes every
     direct child to `position: relative; z-index: 2` — MEASURED (real
     browser, `getComputedStyle`) to be an exact specificity TIE with the
     rule below, not the win the first draft of this comment assumed:
     segmentline.css's full selector is
     `[data-design-language='segmentline'][data-design-language] .dl-glass
     > *` (two attributes + one class = 0,3,0), and Svelte compiles this
     component's own scoping hash onto the second half of a `>` chain
     wrapped in `:where()` — zero specificity — so `.peer-split-glass
     .s-xxx > .peer-split-clock:where(.s-xxx)` also lands at exactly 0,3,0.
     A tie falls back to stylesheet ORDER, and segmentline.css loads AFTER
     this component's own compiled style (dynamic `import()` in
     `fixtures/main.ts`, versus this file's static import), so it won —
     confirmed live (clock rendered in-flow at the top of the glass,
     `getComputedStyle(...).position === 'relative'`) before this
     `!important` was added. `!important` is scoped to the one property two
     components' stylesheets both set on the same element with no reliable
     load-order guarantee (production's real activation timing is no more
     ordered than this harness's), not a general escape hatch. */
  .peer-split-glass > .peer-split-clock {
    position: absolute !important;
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
    /* status-rail(auto — see the row-1 note below) / rule(1) / global-row
       (auto, not one of the archived five bands — the wiring's own
       radio-wide row, given its own space rather than forced into a
       documented band it doesn't describe) / receiver-body: vfo(auto) /
       receiver-body: meters+scope remainder(min 72px, else 1fr —
       MEASURED, real browser: with a plain `minmax(0, 1fr)` the four
       `auto` tracks above it (status/global/vfo, 245+104+34px with the
       fixture's actual content) already claim nearly all of the native
       540px height, so the flexible track collapsed to ~1.5px — legible
       nothing rather than a visibly short meters/scope band. 72px is a
       floor, not a fit: the base wiring's own `overflow: auto` on
       `.semantic-surfaces`, untouched here, is what a real overrun of the
       native height falls back to) / dsp-rail(40, reserved) /
       memory-rail(34, reserved) */
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
