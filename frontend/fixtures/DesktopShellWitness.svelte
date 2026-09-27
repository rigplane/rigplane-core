<!--
  MOR-2676 — the desktop-shell witness: the FULL `DesktopSkin`/`RadioLayout`
  composition (StatusBar, SpectrumPanel, sidebars) over the same four stubbed
  seams every other fixtures-server page uses.

  This is exactly `App.svelte`'s own shape for a `built-in-instrument-layout`
  presentation: `SemanticRadioSurfaces` owns the bindings and authority, and
  hands the resolved `InstrumentComposition` to the presentation through its
  `children` snippet. `ReferenceLayout.svelte` stands in only for the SRS
  wiring; it never mounts this chrome, which is why the desktop shell had no
  harness reach before MOR-2676.
-->
<script lang="ts">
  import SemanticRadioSurfaces from '../src/components-v2/wiring/SemanticRadioSurfaces.svelte';
  import DesktopSkin from '../src/skins/desktop-v2/DesktopSkin.svelte';
</script>

<SemanticRadioSurfaces>
  {#snippet children(instruments)}
    <DesktopSkin {instruments} />
  {/snippet}
</SemanticRadioSurfaces>
