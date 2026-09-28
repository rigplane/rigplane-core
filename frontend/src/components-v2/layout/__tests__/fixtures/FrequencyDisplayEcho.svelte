<!--
  MOR-2511 test fixture: echoes the props MobileRadioLayout's header binds.
  Pinned by MobileRadioLayout.component.svelte.test.ts, describe
  "mobile header follows the active receiver (MOR-2511)".
  MOR-2911: also echoes the in-flight tune target the header binds. The two
  spans are adjacent (no whitespace text node between them) so the MOR-2511
  suite's byte-exact .m-vfo-freq textContent pins keep passing.
-->
<script lang="ts">
  let {
    freq, pendingDisplayHz = null,
  }: { freq: number; pendingDisplayHz?: number | null } = $props();
</script>

<span data-testid="freq-echo">{freq}</span><span data-testid="freq-pending-echo">{pendingDisplayHz ?? ''}</span>
