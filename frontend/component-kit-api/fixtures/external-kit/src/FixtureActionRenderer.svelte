<script lang="ts">
  import type { ActionRendererProps } from '@rigplane/component-kit-api';

  let { lease }: ActionRendererProps = $props();
  const view = $derived(lease.view);
</script>

<button
  type="button"
  data-fixture-action
  disabled={!view?.available}
  aria-label={view?.accessibleLabel ?? view?.label ?? 'Unavailable action'}
  title={view?.title}
  onclick={() => lease.invoke()}
>
  {view?.label ?? 'Unavailable action'}
</button>
