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
  import type { RadioViewModel } from '../../semantic/radio-view-model';
  import { projectPeerSplitDisplay } from '../../semantic/radio-display-model';
  import CenterstageDisplay from './CenterstageDisplay.svelte';
  import DominantUnifiedDisplay from './DominantUnifiedDisplay.svelte';
  import LcdDisplayVariant, { type LcdDisplayVariantId } from './LcdDisplayVariant.svelte';
  import PanadapterDisplay from './PanadapterDisplay.svelte';
  import PeerSplitDisplay from './PeerSplitDisplay.svelte';

  /**
   * Canvas size and scale floor come from the shell (`components-v2/layout/
   * LcdLayout.svelte`), which resolves them from the `peer-split-glass`
   * instrument group through the `peer-split` manifest's zone reference
   * (MOR-2253 slice 1, MOR-2259). This
   * replaces this component's former local canvas-size constants, which
   * duplicated the layout declaration's derived stage as a second,
   * independent literal (instrument-group ADR §4, F2).
   */
  interface Props {
    canvasW: number;
    canvasH: number;
    /** The group's own `scaling.minScale`, resolved by the same shell and
     *  handed to `ScaledStage` as its scale floor (MOR-2259). */
    minScale: number;
    displayVariant?: LcdDisplayVariantId;
  }
  let { canvasW, canvasH, minScale, displayVariant = 'peer' }: Props = $props();
</script>

{#snippet readonlyDisplay(view: RadioViewModel)}
  {@const model = projectPeerSplitDisplay(view)}
  {#snippet peer()}<PeerSplitDisplay {model} />{/snippet}
  {#snippet dominant()}<DominantUnifiedDisplay {model} />{/snippet}
  {#snippet centerstage()}<CenterstageDisplay {model} />{/snippet}
  {#snippet panadapter()}<PanadapterDisplay {model} />{/snippet}
  <LcdDisplayVariant
    variant={displayVariant}
    {peer}
    {dominant}
    {centerstage}
    {panadapter}
  />
{/snippet}

<div class="peer-split-holder">
  <ScaledStage nativeW={canvasW} nativeH={canvasH} {minScale}>
    <div class="peer-split-glass" data-testid="peer-split-glass">
      <SemanticRadioSurfaces strips="dual" {readonlyDisplay} />
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
    border: 2px solid var(--dl-segmentline-bezel-edge, #8a7020);
    border-radius: 10px;
    background: var(--dl-segmentline-glass, #c8a030);
    color: var(--dl-segmentline-ink-strong, rgba(26, 16, 0, 1));
    box-shadow: inset 0 0 50px rgba(0, 0, 0, 0.06), 0 0 8px rgba(0, 0, 0, 0.5);
  }
  .peer-split-glass::before {
    content: '';
    position: absolute;
    z-index: 1;
    inset: 0;
    pointer-events: none;
    background: repeating-linear-gradient(
      to bottom, transparent 0 3px, rgba(0, 0, 0, 0.04) 3px 6px
    );
  }

  .peer-split-glass :global(.semantic-surfaces.semantic-surfaces) {
    position: relative;
    z-index: 2;
    gap: 0;
    overflow: hidden;
  }
</style>
