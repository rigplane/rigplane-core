<!--
  MOR-2511 test fixture: echoes the S-meter value MobileRadioLayout binds.
  Pinned by MobileRadioLayout.component.svelte.test.ts, describe
  "mobile header follows the active receiver (MOR-2511)".
-->
<script lang="ts">
  let { value }: { value: number } = $props();
</script>

<span data-testid="smeter-echo">{value}</span>
