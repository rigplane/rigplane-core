<!--
  MOR-2511 test fixture: echoes the props MobileRadioLayout's header binds.
  Pinned by MobileRadioLayout.component.svelte.test.ts, describe
  "mobile header follows the active receiver (MOR-2511)".
-->
<script lang="ts">
  let {
    freq, pendingDisplayHz = null,
  }: { freq: number; pendingDisplayHz?: number | null } = $props();
</script>

<span data-testid="freq-echo">{freq}</span>
<!-- MOR-2911: echoes the in-flight tune target the header binds. -->
<span data-testid="freq-pending-echo">{pendingDisplayHz ?? ''}</span>
