<!--
  Dual Receiver Cockpit (MOR-1067) — the `dual-receiver-cockpit` manifest's
  compiled shell. Static composition only: places the primary/secondary VFO
  channel strips and the shared RX/TX status+action surface via
  SemanticRadioSurfaces' `strips="dual"` composition (MOR-1065 wiring,
  reused unmodified — its lease-safe TX internals live in exactly one file,
  components-v2/wiring/SemanticRadioSurfaces.svelte). No manufacturer
  conditionals, no runtime/store/command import here.

  MOR-1068: the manifest's four declared zones — `primary-vfo`,
  `secondary-vfo`, `global`, `rx-tx` — are realized by that one `strips="dual"`
  composition, in declaration order, each tagged with its `data-zone-id`. They
  cannot be split across separate mounts here: a second SemanticRadioSurfaces
  would be a second TX lease source, and single TX authority is the layout's
  hardest invariant.

  Scope and controls remain inert structural zones. MOR-1062/1065 ship only
  the vfo/rxTx semantic surfaces — nothing here may claim those regions are
  live, and they declare no manifest zone for the same reason. Same two-level
  gating as the surfaces themselves (MOR-977): present so the shell composes
  every named region, disabled because no real surface backs them yet — never
  falsely active. `global` left this list when the radio-wide row moved in:
  a zone holding live switches gates at the widget, not with a container
  marker (MOR-1067 verification F7).

  MOR-1069 — the cockpit's RESPONSIVE COMPOSITION lives in the style block
  below and nowhere else. Four policies, each pinned (see
  `presentation/layouts/__tests__/cockpit-responsive-composition.test.ts` and
  `__tests__/DualReceiverCockpit.component.test.ts`):

  1. PLACEMENT. Desktop-first: the base rules ARE the wide arrangement and
     each media block only reflows it. Compact (<768px) stacks the channel
     strips into one column in both orientations; the tablet band
     (768-1023px) stacks in PORTRAIT only, because there the width is not
     real. Desktop (>=1024px) and tablet landscape keep the two columns. The
     thresholds are declared on the manifest as this layout's reflow
     breakpoints, and a test requires the two descriptions to agree.

  2. NOTHING IS HIDDEN. No breakpoint may `display: none` or
     `visibility: hidden` any zone or control — the MOR-557 / F1-mirror lens
     applied to responsiveness: a control removed by viewport is a capability
     the operator silently lost, and the ticket's "hidden secondary zones do
     not destroy active runtime resources" cannot be violated by a
     composition that hides nothing. Stacking is the answer to narrow, not
     hiding.

  3. DOM ORDER IS FOCUS ORDER. The reflow never uses `order`, a `-reverse`
     direction, `grid-auto-flow: dense`, or out-of-flow positioning, so the
     visual sequence and the tab sequence stay the same sequence at every
     breakpoint and in both orientations ("keyboard and touch order remain
     logical").

  4. NO MOTION, NO STATE MACHINE. The reflow is instantaneous — this shell
     declares no transition, animation or keyframes, so there is nothing for
     `prefers-reduced-motion` to have to switch off (the app-wide reduce
     block in `styles/animations.css` still covers any descendant). And it is
     pure CSS: no `matchMedia`, no resize/orientation listener, no width or
     height state anywhere in the cockpit. The DOM is IDENTICAL across every
     viewport and orientation, so a portrait/landscape change cannot remount
     the surfaces, cannot re-key the TX lease identity, and cannot destroy a
     live runtime resource. That is also what keeps this layout free of the
     second mobile behavior state machine MOR-1069 rules out.

  Touch targets are enforced on `pointer: coarse` at EVERY band rather than
  at a width, and this chrome is never uniformly scaled (MOR-1160: scaled
  controls shrink below the minimum hit size, which is exactly why cockpit
  chrome stays fluid).
-->
<script lang="ts">
  import SemanticRadioSurfaces from '../../components-v2/wiring/SemanticRadioSurfaces.svelte';
</script>

<div class="dual-receiver-cockpit" data-testid="dual-receiver-cockpit">
  <div class="cockpit-surfaces" data-testid="cockpit-surfaces">
    <SemanticRadioSurfaces strips="dual" />
  </div>

  {#each ['scope', 'controls'] as zone (zone)}
    <div
      class="cockpit-inert-zone"
      data-testid={`cockpit-zone-${zone}`}
      data-zone-active="false"
      aria-disabled="true"
    ></div>
  {/each}
</div>

<style>
  .dual-receiver-cockpit {
    display: grid;
    grid-template-rows: 1fr auto auto;
    gap: 8px;
    height: 100%;
  }
  .cockpit-inert-zone {
    min-height: 0;
  }

  /* ── MOR-1069 responsive composition ──────────────────────────────────
     The `:global(...)` halves reach into the shared wiring's composed
     blocks (`components-v2/wiring/SemanticRadioSurfaces.svelte`) on purpose:
     the cockpit OWNS its own responsive rules, and rooting every selector at
     `.dual-receiver-cockpit` keeps them off sdr-test / LCD / mobile, which
     mount the same wiring in its single composition. The coupling to those
     class names is not silent — a component test requires every class named
     below to exist in the mounted tree, so a rename fails loudly here
     instead of quietly dropping the reflow. */

  /* Compact (<768px), both orientations: one column. A phone on its side is
     wider than it is tall but still far too narrow for two channel strips,
     so this band does not consult orientation at all. */
  @media (max-width: 767px) {
    .dual-receiver-cockpit :global(.channel-strips) {
      grid-template-columns: 1fr;
    }
  }

  /* Tablet band (768-1023px), PORTRAIT only: same stack — 768px of portrait
     width split in two leaves neither strip readable. In landscape at the
     same width the two columns stay, because there the width is real and the
     height is what is scarce. This is the orientation axis of the policy. */
  @media (min-width: 768px) and (max-width: 1023px) and (orientation: portrait) {
    .dual-receiver-cockpit :global(.channel-strips) {
      grid-template-columns: 1fr;
    }
  }

  /* Touch targets, every band and every orientation. Keyed off the pointer,
     never off a width: a touch laptop at desktop width needs the same hit
     size a phone does, and a mouse on a narrow window does not. */
  @media (pointer: coarse) {
    .dual-receiver-cockpit :global(button),
    .dual-receiver-cockpit :global([role='switch']) {
      min-height: 44px;
      min-width: 44px;
    }
  }
</style>
