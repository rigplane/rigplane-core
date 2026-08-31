<!--
  SDR Test Skin — the sdr-test presentation entrypoint (MOR-1066). A pure
  RadioLayout delegate; its import boundary (no transport, no audioManager)
  is pinned by architecture-boundaries.test.ts, "the sdr-test entrypoint's
  own import boundary" (MOR-1093).

  Which areas of that shell render as semantic surfaces and which keep their
  legacy twin is decided inside RadioLayout, off the ACTIVE layout manifest's
  zone declarations — NOT by this file, and no longer by a hardcoded skin-id
  comparison. The per-zone rule is stated once, in RadioLayout.svelte's own
  header comment (MOR-1313), and that comment is the authority. It is not
  restated here on purpose: the description this one replaces kept describing
  the pre-MOR-1313 `skinId === 'sdr-test'` boolean long after RadioLayout had
  stopped deciding anything with it.

  This skin's manifest is `sdrTestLayout` (presentation/layouts/declarations.ts);
  its registration, zones and requiredSemanticSurfaces are pinned by
  presentation/layouts/__tests__/sdr-registration.test.ts. `SdrVfoScreen.svelte`
  next door is a diagnostic renderer this entrypoint does not mount.
-->
<script lang="ts">
  import RadioLayout from '../../components-v2/layout/RadioLayout.svelte';
</script>

<RadioLayout skinId="sdr-test" />
