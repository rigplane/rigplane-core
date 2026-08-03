<!--
  Test fixture (MOR-1059). Stands in for the selected presentation in
  App-level composition tests. `{#key skinId}` makes every skin change a
  genuine subtree destroy/recreate, so a test can tell "the presentation was
  replaced" apart from "a prop changed".
-->
<script lang="ts">
  let { skinId = 'desktop-v2' }: { skinId?: string } = $props();
</script>

{#key skinId}
  <div class="layout-stub" data-skin={skinId}></div>
{/key}
