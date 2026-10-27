<script lang="ts">
  import { untrack, type Snippet } from 'svelte';
  import { trackMemoryList, type MemoryListController } from '$lib/runtime/memory-list.svelte';

  let { controller, readable, children }: {
    controller: MemoryListController;
    readable: boolean;
    children: Snippet;
  } = $props();

  trackMemoryList(untrack(() => controller), () => readable);
</script>

{@render children()}
