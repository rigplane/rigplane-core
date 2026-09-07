<script lang="ts">
  import type { ToggleRendererProps } from '@rigplane/component-kit-api';

  let { lease }: ToggleRendererProps = $props();
  const view = $derived(lease.view);
</script>

<button
  type="button"
  data-fixture-toggle
  data-requested={view?.requested?.target ?? ''}
  disabled={!view?.available}
  aria-label={view?.accessibleLabel ?? view?.label ?? 'Unavailable toggle'}
  aria-pressed={view?.confirmed ?? view?.defaultValue ?? false}
  title={view?.title}
  onclick={() => lease.invoke()}
>
  {view?.label ?? 'Unavailable toggle'}
</button>
