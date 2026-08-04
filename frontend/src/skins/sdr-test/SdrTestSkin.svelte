<!--
  SDR Test Skin — the MOR-1065 migrated reference vertical. Delegates to
  RadioLayout, which branches on skinId === 'sdr-test' and mounts the
  semantic VFO/RX-TX surfaces (SemanticRadioSurfaces, MOR-1063/1064) in
  place of the legacy twin-VFO block and sidebar TX panel. Everything else
  (sidebars, spectrum, meters dock, status bar) is inherited from the
  standard desktop layout.

  Registered as the 'sdr-test' v1 layout manifest under this same id
  (MOR-1066, presentation/layouts/declarations.ts) — see
  presentation/layouts/__tests__/sdr-registration.test.ts. The pre-migration
  SdrVfoScreen prototype is no longer mounted here; RadioLayout stopped
  swapping it in once the semantic surfaces landed.
-->
<script lang="ts">
  import RadioLayout from '../../components-v2/layout/RadioLayout.svelte';
</script>

<RadioLayout skinId="sdr-test" />
