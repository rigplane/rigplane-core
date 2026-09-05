<script lang="ts">
  import type { PeerSplitDisplayModel } from '../../semantic/radio-display-model';
  import { telemetryText, telemetryDescription } from './lcd-display-helpers';

  interface Props {
    telemetry: PeerSplitDisplayModel['telemetry'];
  }

  let { telemetry: model }: Props = $props();

  const items = $derived([
    { label: 'VD', field: model.drainVoltage },
    { label: 'ID', field: model.drainCurrent },
    { label: 'PWR', field: model.power },
    { label: 'SWR', field: model.swr },
    { label: 'ALC', field: model.alc },
    { label: 'COMP', field: model.compression },
  ]);
</script>

<footer class="aux-rail">
  <div class="memory-seam" data-state="unsupported" aria-hidden="true">
    {#each [0, 1, 2, 3] as slot}<span data-memory-slot={slot}>MEM</span>{/each}
  </div>
  <div class="telemetry">
    {#each items as item}
      <span class:irrelevant={!item.field.relevant} data-state={item.field.state}
        role="group" aria-label={telemetryDescription(item.label, item.field)}>
        <small>{item.label}</small> {telemetryText(item.field)}
      </span>
    {/each}
  </div>
</footer>

<style>
  .aux-rail {
    display: flex;
    align-items: center;
    justify-content: space-between;
    min-width: 0;
    border-top: 1px solid var(--ink-ghost);
    padding-top: 4px;
  }
  .memory-seam { display: flex; gap: 5px; color: var(--ink-ghost); font-size: 9px; letter-spacing: 0.1em; }
  .memory-seam span { min-width: 42px; padding: 2px 5px; border: 1px solid currentColor; }
  .memory-seam[data-state='unsupported'] { visibility: hidden; }
  .telemetry { display: flex; gap: 14px; align-items: center; color: var(--ink-telemetry); font-size: 10px; }
  .telemetry small { opacity: 0.7; font-size: inherit; }
  .telemetry .irrelevant { opacity: 0.34; }
  .telemetry [data-state='unsupported'] { visibility: hidden; }
</style>
