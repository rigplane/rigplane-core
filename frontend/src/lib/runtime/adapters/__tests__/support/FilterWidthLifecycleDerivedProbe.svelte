<script lang="ts">
  import { getFilterWidthCommandLifecycle } from '../../panel-adapters';

  // This is intentionally a real compiled Svelte derived read, not a test
  // replica of the adapter projection.
  const lifecycle = $derived(getFilterWidthCommandLifecycle());
</script>

<output
  data-phase={lifecycle.phase}
  data-confirmed={lifecycle.confirmed ?? ''}
  data-target={lifecycle.target ?? ''}
  data-outcome={lifecycle.outcome?.phase ?? ''}
></output>
