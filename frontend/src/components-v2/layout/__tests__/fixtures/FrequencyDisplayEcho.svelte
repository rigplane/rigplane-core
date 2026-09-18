<!--
  MOR-2511 test fixture: echoes the props MobileRadioLayout's header binds.
  Pinned by MobileRadioLayout.component.svelte.test.ts, describe
  "mobile header follows the active receiver (MOR-2511)".
-->
<script lang="ts">
  let { freq }: { freq: number } = $props();
</script>

<span data-testid="freq-echo">{freq}</span>
