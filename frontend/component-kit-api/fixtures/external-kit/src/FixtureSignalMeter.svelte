<script lang="ts">
  import type { SignalMeterRendererProps } from '@rigplane/component-kit-api';

  let { view }: SignalMeterRendererProps = $props();
  const domain = $derived(
    view.evidence.domain.kind === 'engineering'
      ? `engineering:${view.evidence.domain.unit}`
      : view.evidence.domain.kind,
  );
</script>

<div
  data-fixture-signal
  data-state={view.evidence.state}
  data-domain={domain}
  data-value={view.evidence.state === 'current' ? view.evidence.value : undefined}
  data-scale={view.scaleMode}
  data-tick-kinds={view.ticks.map(({ kind }) => kind).join(',')}
  aria-label={view.accessibleDescription}
>
  <span>{view.primaryText}</span>
  <span>{view.secondaryText}</span>
</div>
