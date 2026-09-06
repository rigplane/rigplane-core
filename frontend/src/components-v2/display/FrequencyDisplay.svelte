<script lang="ts">
  import StandardFrequencyReadout from '../../primitives/frequency/StandardFrequencyReadout.svelte';
  import { projectFrequencyReadout } from '../../primitives/frequency/frequency-readout';

  interface Props {
    freq: number;       // frequency in Hz (e.g. 14235000)
    compact?: boolean;  // smaller variant (~18px vs ~28px)
    active?: boolean;   // bright (var(--v2-text-bright)) vs dimmed (var(--v2-text-disabled))
  }

  let { freq, compact = false, active = true }: Props = $props();

  let model = $derived(projectFrequencyReadout({ confirmedHz: freq }));
</script>

<StandardFrequencyReadout {model} presentation="passive" {compact} {active} />
