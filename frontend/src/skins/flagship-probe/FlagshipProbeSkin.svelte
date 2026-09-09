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

  ARRANGEMENT. Two receivers side by side in BOTH arrangements, and the
  panorama always between the two rails, never under one:

    wide   — left rail | (deck over panorama) | right rail
    narrow — deck across the top, then left rail | panorama | right rail

  The switch is a container query on `.flagship-probe`, so it follows the
  width this skin is GIVEN rather than the viewport's. Its threshold is
  `--flagship-probe-switch-width` — two rails, two receiver strips and the
  RX/TX column, each at the minimum width declared below, PLUS the four
  gutters the grid puts between those five columns. Those minimums are
  DECLARED, not measured: nothing here measures what a surface actually
  renders at.
  `__tests__/FlagshipProbe.component.test.ts` recomputes the sum and requires
  the `@container` literal to equal it;
  `presentation/layouts/__tests__/flagship-probe-registration.test.ts`
  requires the manifest's declared reflow width to equal it too.

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
    <SemanticRadioSurfaces strips="dual" />
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
      The declared minimum track widths, and their sum with the four gutters
      `.probe-stage` puts between the five columns.
    */
    --flagship-probe-rail-width: 280px;
    --flagship-probe-strip-min-width: 360px;
    --flagship-probe-rx-tx-min-width: 160px;
    --flagship-probe-switch-width: 1472px;
  }

  /*
    NARROW is the base arrangement, so the container query below only ever
    adds the wide one: a container too small for the wide arrangement is also
    a container whose query has not matched.

    Five tracks in both arrangements. Columns 1 and 5 are the rails, and the
    panorama lives across columns 2-4 in both — which is what keeps it
    between them. The deck spans all five columns in the narrow arrangement
    and columns 2-4 in the wide one; that is the whole of the switch.
  */
  .probe-stage {
    display: grid;
    gap: 8px;
    align-content: start;
    grid-template-columns:
      minmax(var(--flagship-probe-rail-width), auto)
      minmax(0, 1fr)
      auto
      minmax(0, 1fr)
      minmax(var(--flagship-probe-rail-width), auto);
    grid-template-areas:
      'rx-main   rx-main    rx-mid     rx-sub     rx-sub'
      'vfo-ops   vfo-ops    vfo-ops    vfo-ops    vfo-ops'
      'rf        panorama   panorama   panorama   tx-aux'
      'dsp       panorama   panorama   panorama   cw-keyer'
      'filter    panorama   panorama   panorama   rit-xit'
      'rx-audio  panorama   panorama   panorama   .'
      'antenna   scope-ctl  scope-ctl  scope-ctl  .'
      'band      scope-disp scope-disp scope-disp .'
      'meters    meters     meters     meters     meters';
  }

  @container (min-width: 1472px) {
    .probe-stage {
      grid-template-areas:
        'rf        rx-main    rx-mid     rx-sub     tx-aux'
        'dsp       vfo-ops    vfo-ops    vfo-ops    cw-keyer'
        'filter    panorama   panorama   panorama   rit-xit'
        'rx-audio  panorama   panorama   panorama   .'
        'antenna   panorama   panorama   panorama   .'
        'band      scope-ctl  scope-ctl  scope-ctl  .'
        '.         scope-disp scope-disp scope-disp .'
        'meters    meters     meters     meters     meters';
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

  .probe-stage :global([data-strip-receiver='MAIN']) {
    grid-area: rx-main;
    min-width: var(--flagship-probe-strip-min-width);
  }
  .probe-stage :global([data-strip-receiver='SUB']) {
    grid-area: rx-sub;
    min-width: var(--flagship-probe-strip-min-width);
  }
  .probe-stage :global([data-zone-id='rx-tx']) {
    grid-area: rx-mid;
    min-width: var(--flagship-probe-rx-tx-min-width);
  }
  .probe-stage :global([data-zone-id='global']) { grid-area: vfo-ops; }
  .probe-stage :global([data-zone-id='rf-front-end']) { grid-area: rf; }
  .probe-stage :global([data-zone-id='dsp']) { grid-area: dsp; }
  .probe-stage :global([data-zone-id='filter']) { grid-area: filter; }
  .probe-stage :global([data-zone-id='rx-audio']) { grid-area: rx-audio; }
  .probe-stage :global([data-zone-id='antenna']) { grid-area: antenna; }
  .probe-stage :global([data-zone-id='band']) { grid-area: band; }
  .probe-stage :global([data-zone-id='tx-aux']) { grid-area: tx-aux; }
  .probe-stage :global([data-zone-id='cw-keyer']) { grid-area: cw-keyer; }
  .probe-stage :global([data-zone-id='rit-xit-scan']) { grid-area: rit-xit; }
  .probe-stage :global([data-zone-id='scope-controls']) { grid-area: scope-ctl; }
  .probe-stage :global([data-zone-id='scope-display']) { grid-area: scope-disp; }
  .probe-stage :global([data-zone-id='meters']) { grid-area: meters; }

  .probe-panorama {
    grid-area: panorama;
    min-width: 0;
  }
</style>
