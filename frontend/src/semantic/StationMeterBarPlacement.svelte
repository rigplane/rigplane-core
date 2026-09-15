<script lang="ts">
  import { onDestroy, untrack } from 'svelte';
  import BarGauge from '../components-v2/meters/BarGauge.svelte';
  import type { Zone } from '../components-v2/meters/bar-gauge-utils';
  import type { ActionRendererLease, ActionRendererSeat } from '../primitives/control-instruments/control-instrument-renderer.svelte';
  import type { StationLevelMeterFrame } from './StationMeterInstrumentHost.svelte';
  interface Props {
    frame: StationLevelMeterFrame;
    label: string;
    displayValue: string;
    accessibleDescription?: string;
    zones?: readonly Zone[];
    compact?: boolean;
    fault?: boolean;
    resetPeakSeat?: ActionRendererSeat;
  }
  let {
    frame, label, displayValue, accessibleDescription, zones,
    compact = false, fault = false, resetPeakSeat,
  }: Props = $props();
  const lease: ActionRendererLease | null = untrack(() => resetPeakSeat?.attachRenderer() ?? null);
  onDestroy(() => lease?.dispose());
</script>
<BarGauge frame={frame.motion} {label} {displayValue} {accessibleDescription}
  {zones} {compact} {fault} onResetPeak={lease === null ? undefined : () => lease.invoke()} />
