<script module lang="ts">
  /**
   * Test fixture (MOR-1086). Stands in for a presentation subtree in
   * App-level composition tests that run against the REAL
   * `presentationResources` host.
   *
   * It models exactly what a real presentation subtree does with App-session
   * resources: every viewer panel acquires its lease from inside an
   * `$effect` on mount and releases it in that effect's cleanup on destroy
   * (see `AudioSpectrumPanel.svelte`, `AmberScope.svelte`,
   * `AmberCockpit.svelte` and `SpectrumPanel.svelte`). Passing a skin's real
   * resource plan therefore reproduces the maximal-demand case — the one
   * where a swap could bounce a live resource through zero if App did not
   * bridge the demand across the commit.
   */
  export const stubEvents: string[] = [];
</script>

<script lang="ts">
  import { presentationResources } from '$lib/runtime/frontend-runtime';
  import type { AppResource } from '$lib/runtime/resource-demand';

  let { skinId = 'desktop-v2', resources = [] }: {
    skinId?: string;
    resources?: readonly AppResource[];
  } = $props();

  $effect(() => {
    const leases = resources.map(
      (resource) => presentationResources.acquire(resource, `${skinId}:viewer`),
    );
    stubEvents.push(`mount:${skinId}`);
    return () => {
      stubEvents.push(`destroy:${skinId}`);
      for (const lease of [...leases].reverse()) presentationResources.release(lease);
    };
  });
</script>

<div class="presentation-stub" data-skin={skinId}></div>
