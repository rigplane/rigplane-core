<!--
  Desktop V2 Skin — standard layout with spectrum/waterfall.

  This is a thin delegation wrapper during migration. It mounts the existing
  RadioLayout internals. Once Phase 4 (#651) migrates all panels to the
  runtime adapter pattern, this skin will compose semantic components directly
  instead of delegating.
-->
<script lang="ts">
  import RadioLayout from '../../components-v2/layout/RadioLayout.svelte';
</script>

<RadioLayout skinId="desktop-v2" />
