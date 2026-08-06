<!--
  MOR-1085 — the "reference current layout" half of the fixture matrix.

  `SemanticRadioSurfaces.svelte` calls itself "the semantic VFO + RX/TX
  reference vertical" in its own header comment, and `strips="single"`
  (the default) is EXACTLY what `desktop-v2`/`sdr-test` compose today —
  `RadioLayout.svelte` mounts this same component with no `strips` prop,
  inside legacy chrome (sidebars, spectrum panel, status bar, keyboard
  handler) that this ticket's test grammar has no opinion about. Mounting
  `RadioLayout` itself in this lightweight harness would pull in that whole
  chrome tree — canvas rendering, ResizeObserver-driven layout, several
  stores this harness's four stubbed seams do not cover — for zero
  additional coverage of the MOR-1065/1067/1068/1069 VFO/RX-TX grammar this
  catalog actually exercises. This wrapper isolates exactly the vertical the
  grammar is about, the same isolation principle
  `dual-receiver-cockpit/DualReceiverCockpit.svelte` already applies to the
  dual composition (a thin shell around the identical wiring component).

  Unlike the cockpit shell, this wrapper adds NO inert `scope`/`controls`
  placeholder zones: those are `dual-receiver-cockpit`'s own structural
  promise (MOR-1067) that a future scope/controls surface has a named home.
  `desktop-v2` makes no such promise here — its scope area is the legacy
  `SpectrumPanel`, out of scope for this grammar — so fabricating a
  placeholder would assert a promise this layout never made.
-->
<script lang="ts">
  import '../src/components-v2/theme/index';
  import SemanticRadioSurfaces from '../src/components-v2/wiring/SemanticRadioSurfaces.svelte';
</script>

<div class="reference-layout" data-testid="reference-layout">
  <SemanticRadioSurfaces strips="single" />
</div>

<style>
  .reference-layout {
    height: 100%;
    overflow: auto;
  }
</style>
