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

  ARRANGEMENT. Two receivers side by side in BOTH arrangements, and the
  panorama always between the two rails, never under one:

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
  receivers, and the deck's middle track is gone rather than empty: the same
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
    <SemanticRadioSurfaces strips="dual" bandPermitCaption={false} />
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
      ONE-KEY floor: re-measured 2026-09-09 with every rail key's text
      replaced by `lib/i18n/pseudo.ts`'s `pseudoize()` output, on the real
      skin through the fixture-stub seam (`vite.fixtures.config.ts`) in
      headless Chromium; do not lower it to make a layout fit.

      192 is the larger of two measured terms rounded up: the widest single
      key in any of the ten rail surfaces — the CW keyer's
      `⟦Ŕéṽéŕšé þáđđłé: — ~~~~~~⟧`, 191.77px at a computed font-size of
      13.3333px, with no padding between its border box and its zone box —
      and the transmit zone's min-content, 185.20px, which is that zone's own
      `⟦Ķéý ťŕáñšɱíťťéŕ ~~~~~~⟧` button now that
      `semantic/RxTxSurface.svelte`'s `.rx-tx-actions` row may wrap.

      Derived from labels that CAN grow, not from the ones shipped today.
      `__tests__/FlagshipProbe.component.test.ts` pins this value and the
      shape of the two rail tracks below.
    */
    --flagship-probe-rail-floor: 192px;

    /*
      The declared track widths — a rail's width and a receiver strip's
      minimum — and their sum with the three gutters `.probe-stage` puts
      between the four columns.

      THE WIDTH IS THE FLOOR. Art-direction ruling of 2026-09-09: a rail is
      as wide as the widest thing that must be READABLE in one column of
      controls, which is ONE key — not a row of two, and not the transmit
      pair, whose `.rx-tx-actions` row may wrap
      (`semantic/RxTxSurface.svelte`). That ruling also confines
      pseudo-localisation to the key whose clipping would be a SAFETY
      failure, the transmit key; every other rail key is measured in
      English. Measured 2026-09-09 the same way as the floor above, over
      every key the ten rail surfaces mount on that harness's dual-receiver
      fixture, each toggle taken at both values its own fact renders: the
      widest English key is the CW keyer's `Reverse paddle: on`, 131.61px —
      below the floor. So the floor is the binding term and this track is a
      fixed 192px.

      Two properties for one number, not one: the tracks below stay written
      as `minmax(floor, width)`, and `tests/e2e/i18n/desktop-geometry.spec.ts`
      overrides BOTH to set the rail column it measures the transmit pair in.
    */
    --flagship-probe-rail-width: 192px;
    --flagship-probe-strip-min-width: 360px;
    --flagship-probe-switch-width: 1128px;
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

  @container (min-width: 1128px) {
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

  .probe-stage :global([data-strip-receiver='MAIN']) { grid-area: rx-main; }
  .probe-stage :global([data-strip-receiver='SUB']) { grid-area: rx-sub; }
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
