<!--
  Flagship geometry probe — a GEOMETRY PROBE, not the flagship face.

  What it does: places the EXISTING semantic surfaces, drawn by the existing
  wiring, in the flagship's arrangement, and mounts the existing
  `SpectrumPanel` between the two rails. Its style block declares one grid
  and nothing else: no colour, no font, no border and no control of its own,
  so what appears inside it is drawn entirely by
  `components-v2/wiring/SemanticRadioSurfaces.svelte`, by the surfaces it
  composes, and by `SpectrumPanel` — each at its own native size.

  What it deliberately does NOT do: the `memory` surface is not declared, the
  type ladder is not applied, and no key is given a lamp.

  THE BAND KEY PRINTS ITS NAME ONLY: `bandPermitCaption={false}` suppresses
  the key's printed permit caption and nothing else, the permit itself
  staying in the key's accessible name and its `data-default-permit` —
  `semantic/__tests__/BandInstrumentHost.isolated.test.ts`'s "suppresses only
  the printed caption, keeping the accessible name and the attribute" pins
  that split at the surface, and `__tests__/FlagshipProbe.component.test.ts`
  requires it of the keys this shell mounts.

  ARRANGEMENT. Two DECK SLOTS side by side in BOTH arrangements, and the
  panorama always between the two rails, never under one. `stripBy="slot"`
  is what makes a column a slot rather than a receiver (owner ruling R58,
  2026-09-09): on a two-receiver radio a column is still a receiver, and on
  a single-receiver one the right column holds the unselected VFO —
  `__tests__/FlagshipProbe.component.test.ts` requires both:

    wide   — left rail | (deck over panorama) | right rail
    narrow — deck across the top, then left rail | panorama | right rail

  THE TRANSMIT KEY IS NOT IN THE DECK. The art-direction line's rule of
  2026-09-09: nothing that keys the transmitter may live in a scrolling or
  clipped column. `semantic/RxTxSurface.svelte` renders that key, so its
  `rx-tx` zone is placed FIRST in the right rail, above the other transmit
  controls, in both arrangements — `__tests__/FlagshipProbe.component.test.ts`
  requires the rail column, that position and the rail's declared track
  together. The rail's own minimum is the width one key needs — see
  `--flagship-probe-rail-floor` below. Nothing then stands between the two
  slots, and the deck's middle track is gone rather than empty: the same
  test requires every track in both column templates to be a column some area
  names.

  The switch is a container query on `.flagship-probe`, so it follows the
  width this skin is GIVEN rather than the viewport's. Its threshold is
  `--flagship-probe-switch-width`: the four widths the wide arrangement's
  column template declares — a rail twice and a receiver strip's minimum
  twice — PLUS the three gutters between them, which is the width below which
  that template cannot give every track its declared size.
  `__tests__/FlagshipProbe.component.test.ts` recomputes the sum from the
  parsed column template and requires the `@container` literal to equal it;
  `presentation/layouts/__tests__/flagship-probe-registration.test.ts`
  requires the manifest's declared reflow width to equal it too.

  NO SURFACE MAY SIZE A TRACK. Every track in both column templates is a
  declared size: the rails are `minmax(<floor>, <rail>)` and the two receiver
  strips are the flexible tracks that take the remainder. None is `auto`,
  `min-content`, `max-content` or `fit-content`, in either direction. A
  surface wider than its column overflows inside that column. The component
  test parses both templates and fails on any content-sized track, on a rail
  written any other way, and on a grid item left without `min-width: 0`.

  R52. This shell renders no control and no readout, so it announces nothing
  it does not know: it contributes no dash, no glyph and no disabled
  element, and its grid is identical in receive and in transmit — no row,
  area or track below is conditional on anything.
-->
<script lang="ts">
  // The same side-effect import, for the same reason, that
  // `dual-receiver-cockpit/DualReceiverCockpit.svelte` carries (MOR-1257 N4):
  // the components-v2 theme layer is code-split, and this shell composes
  // neither of the two layouts that pull it in (`RadioLayout.svelte` and
  // `LcdLayout.svelte` both `import '../theme/index'`).
  import '../../components-v2/theme/index';
  import SemanticRadioSurfaces from '../../components-v2/wiring/SemanticRadioSurfaces.svelte';
  import SpectrumPanel from '../../components/spectrum/SpectrumPanel.svelte';
</script>

<div class="flagship-probe" data-testid="flagship-geometry-probe">
  <div class="probe-stage" data-testid="probe-stage">
    <SemanticRadioSurfaces strips="dual" stripBy="slot" bandPermitCaption={false} />
    <!--
      `hideScopeControls`: the fact-backed half of the spectrum toolbar is
      suppressed because the semantic `scopeControls` surface renders it,
      once, in its own declared zone directly under this panel. No
      `scopeControls` snippet is passed: that snippet exists only on the
      hosted `instruments` composition, which a self-contained skin does not
      receive. No `colorRoles`: this probe adds no colour.
      `scopeProjection` is likewise not passed, which leaves SpectrumPanel on
      its own unmanaged path — it acquires and releases its own scope lease,
      exactly as it does under `RadioLayout`'s second `<SpectrumPanel>`.
    -->
    <div class="probe-panorama" data-testid="probe-panorama">
      <SpectrumPanel hideSourceControls={true} hideScopeControls={true} />
    </div>
  </div>
</div>

<style>
  /*
    The query container. It must be an ancestor of the grid rather than the
    grid itself: an element cannot answer a container query about its own
    size. Same idiom as `skins/dual-sdr-face/DualSdrFace.svelte`.
  */
  .flagship-probe {
    container-type: inline-size;
    height: 100%;

    /*
      THE RAIL RULE (art direction, 2026-09-09). A rail is as wide as the
      widest thing that must be READABLE in one column of controls, which is
      ONE key — not a row of two, and not the transmit pair, whose
      `.rx-tx-actions` row may wrap (`semantic/RxTxSurface.svelte`).
      Pseudo-localisation is a stress model, not a width source: it fixes a
      number only where clipping would be a SAFETY failure — the transmit
      key — and every other rail key is measured in English.

      Both terms below are that one rule. Measured 2026-09-09 in headless
      Chromium on this skin, mounted through the fixture-stub seam
      (`vite.fixtures.config.ts`) from an entry written for the measurement:
      every `<button>` the ten rail zones mount, each taken alone at
      `width: max-content` plus the padding and border between its border box
      and its zone box, and taken again with `on` and with `off` in place of
      an unread fact. Pseudo text is `lib/i18n/pseudo.ts`'s `pseudoize()`
      applied to the rendered string.

        safety term  — the transmit key, pseudo-localised, 185.20px. 186 is
                       that rounded up, and it GOVERNS both properties.
        English term — the widest rail key in English is the CW keyer's
                       `Reverse paddle: on`, 131.61px at a computed
                       font-size of 13.3333px. Below 186, so it does not
                       bind.

      Pseudo-localised, that CW key measures 191.77px with its fact unread
      and 201.05 / 208.47 at `on` / `off`. The rule excludes all three: it is
      no safety key. Its value sits inside its own label —
      `semantic/CwKeyerSurface.svelte`'s `cw-keyer-reverse-paddle` prints the
      state after the name — which is a labelling defect to fix.

      Two properties, one number: the templates below stay written as
      `minmax(floor, width)`, and `tests/e2e/i18n/desktop-geometry.spec.ts`
      overrides BOTH to set the rail column it measures the transmit pair in.
      Do not lower either to make a layout fit.

      `--flagship-probe-switch-width` is the wide template's four declared
      widths plus the three gutters `.probe-stage` puts between its columns:
      2*186 + 2*360 + 3*8 = 1116. `__tests__/FlagshipProbe.component.test.ts`
      requires the `@container` literal and the manifest breakpoint to equal
      this sum, and pins the shape of the two rail tracks.
    */
    --flagship-probe-rail-floor: 186px;
    --flagship-probe-rail-width: 186px;
    --flagship-probe-strip-min-width: 360px;
    --flagship-probe-switch-width: 1116px;
  }

  /*
    NARROW is the base arrangement, so the container query below only ever
    adds the wide one: a container too small for the wide arrangement is also
    a container whose query has not matched.

    Four tracks in both arrangements. Columns 1 and 4 are the rails, and the
    panorama lives across columns 2-3 in both — which is what keeps it
    between them. The deck spans all four columns in the narrow arrangement
    and columns 2-3 in the wide one; that is the whole of the switch.

    Both templates floor the rails at the one-key floor, cap them at the
    declared rail width and give the remainder to columns 2 and 3. The wide
    one alone floors those two at the declared strip minimum, because it is
    the arrangement in which a receiver strip occupies one of them on its own
    — in the narrow one each strip spans a rail column and its neighbour.

    The rail floor is a hard track minimum, so the two flexible columns are
    what gives first: at a container narrower than both rails and the three
    gutters together they reach 0, and this grid then overflows its container
    rather than compressing a rail. That is the intended answer — below this
    arrangement is the mobile skin, which `skins/registry.ts: resolveSkinId`
    returns ahead of this one whenever the app reports a mobile viewport, and
    which is a separate design rather than this one squeezed.
  */
  .probe-stage {
    display: grid;
    gap: 8px;
    align-content: start;
    grid-template-columns:
      minmax(var(--flagship-probe-rail-floor), var(--flagship-probe-rail-width))
      minmax(0, 1fr)
      minmax(0, 1fr)
      minmax(var(--flagship-probe-rail-floor), var(--flagship-probe-rail-width));
    grid-template-areas:
      'rx-main   rx-main    rx-sub     rx-sub'
      'vfo-ops   vfo-ops    vfo-ops    vfo-ops'
      'rf        panorama   panorama   rx-tx'
      'dsp       panorama   panorama   tx-aux'
      'filter    panorama   panorama   cw-keyer'
      'rx-audio  panorama   panorama   rit-xit'
      'antenna   scope-ctl  scope-ctl  .'
      'band      scope-disp scope-disp .'
      'meters    meters     meters     meters';
  }

  @container (min-width: 1116px) {
    .probe-stage {
      grid-template-columns:
        minmax(var(--flagship-probe-rail-floor), var(--flagship-probe-rail-width))
        minmax(var(--flagship-probe-strip-min-width), 1fr)
        minmax(var(--flagship-probe-strip-min-width), 1fr)
        minmax(var(--flagship-probe-rail-floor), var(--flagship-probe-rail-width));
      grid-template-areas:
        'rf        rx-main    rx-sub     rx-tx'
        'dsp       vfo-ops    vfo-ops    tx-aux'
        'filter    panorama   panorama   cw-keyer'
        'rx-audio  panorama   panorama   rit-xit'
        'antenna   panorama   panorama   .'
        'band      scope-ctl  scope-ctl  .'
        '.         scope-disp scope-disp .'
        'meters    meters     meters     meters';
    }
  }

  /*
    The `:global(...)` halves reach into the shared wiring's composed blocks
    on purpose — this skin owns its own arrangement, and rooting every
    selector at `.probe-stage` keeps them off every other face that mounts
    the same wiring. The coupling to those attribute values is not silent:
    the component test requires every `data-zone-id` named here to exist in
    the mounted tree.

    `display: contents` on the wiring's two containers is what makes the
    zones below grid items of THIS grid rather than of a box this skin does
    not own.
  */
  .probe-stage :global(.semantic-surfaces),
  .probe-stage :global(.channel-strips) {
    display: contents;
  }

  .probe-stage :global([data-strip-slot='primary']) { grid-area: rx-main; }
  .probe-stage :global([data-strip-slot='secondary']) { grid-area: rx-sub; }
  .probe-stage :global([data-zone-id='global']) { grid-area: vfo-ops; }
  .probe-stage :global([data-zone-id='rf-front-end']) { grid-area: rf; }
  .probe-stage :global([data-zone-id='dsp']) { grid-area: dsp; }
  .probe-stage :global([data-zone-id='filter']) { grid-area: filter; }
  .probe-stage :global([data-zone-id='rx-audio']) { grid-area: rx-audio; }
  .probe-stage :global([data-zone-id='antenna']) { grid-area: antenna; }
  .probe-stage :global([data-zone-id='band']) { grid-area: band; }
  .probe-stage :global([data-zone-id='rx-tx']) { grid-area: rx-tx; }
  .probe-stage :global([data-zone-id='tx-aux']) { grid-area: tx-aux; }
  .probe-stage :global([data-zone-id='cw-keyer']) { grid-area: cw-keyer; }
  .probe-stage :global([data-zone-id='rit-xit-scan']) { grid-area: rit-xit; }
  .probe-stage :global([data-zone-id='scope-controls']) { grid-area: scope-ctl; }
  .probe-stage :global([data-zone-id='scope-display']) { grid-area: scope-disp; }
  .probe-stage :global([data-zone-id='meters']) { grid-area: meters; }

  /*
    CONTAINMENT. `min-width: 0` replaces the automatic minimum size a grid
    item carries by default — its min-content width — which is the one
    remaining path by which a surface could push its own box past its column.
    Overflow decides what then happens to the part that does not fit, and
    every region here gets the same answer, `auto`: a clip removes content
    without a trace, and a clipped control is an unreachable one. The
    panorama is not the exception it looks like — `SpectrumPanel` mounts its
    toolbar inside it in every scope mode but audio FFT. A scroll container
    leaves what does not fit reachable
    and makes the too-wide surface look wrong inside its own column, which
    is where that fix belongs.
  */
  .probe-stage :global([data-zone-id]) {
    min-width: 0;
    overflow: auto;
  }

  .probe-panorama {
    grid-area: panorama;
    min-width: 0;
    overflow: auto;
  }
</style>
