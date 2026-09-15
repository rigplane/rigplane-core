<script lang="ts">
  import type { LevelMeterRendererProps } from '@rigplane/component-kit-api';

  let { view, resetPeak }: LevelMeterRendererProps = $props();
  const domain = $derived(
    view.evidence.domain.kind === 'engineering'
      ? `engineering:${view.evidence.domain.unit}`
      : view.evidence.domain.kind,
  );
</script>

<div
  data-fixture-level={view.key}
  data-state={view.evidence.state}
  data-domain={domain}
  data-value={view.evidence.state === 'current' || view.evidence.state === 'stale'
    ? view.evidence.value
    : undefined}
  data-ratio-scale={view.key === 'swr' ? view.ratioScale : undefined}
  aria-label={view.accessibleDescription}
>
  <span>{view.displayText}</span>
  {#if resetPeak?.view?.available}
    <button type="button" onclick={() => resetPeak?.invoke()}>Reset peak</button>
  {/if}
</div>
