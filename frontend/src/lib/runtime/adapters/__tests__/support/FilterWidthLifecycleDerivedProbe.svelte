<script lang="ts">
  import { getFilterWidthCommandLifecycle } from '../../panel-adapters';

  // This is intentionally a real compiled Svelte derived read, not a test
  // replica of the adapter projection. The test-only refresh seam makes one
  // mounted consumer re-read after its controlled lifecycle input changes.
  let refreshEpoch = $state(0);
  export function refresh(): void { refreshEpoch += 1; }
  const refreshedProjection = $derived({ refreshEpoch, value: getFilterWidthCommandLifecycle() });
  const lifecycle = $derived(refreshedProjection.value);
</script>

<output
  data-phase={lifecycle.phase}
  data-confirmed={lifecycle.confirmed ?? ''}
  data-target={lifecycle.target ?? ''}
  data-outcome={lifecycle.outcome?.phase ?? ''}
></output>
