<script lang="ts">
  import { onDestroy } from 'svelte';
  import StandardFrequencyReadout from '../../primitives/frequency/StandardFrequencyReadout.svelte';
  import {
    createFrequencyInteraction, createFrequencyInteractionLease,
  } from '../../primitives/frequency/frequency-interaction.svelte';
  import { projectFrequencyReadout } from '../../primitives/frequency/frequency-readout';

  interface Props {
    freq: number;       // frequency in Hz (e.g. 14235000)
    compact?: boolean;  // smaller variant (~18px vs ~28px)
    active?: boolean;   // bright (var(--v2-text-bright)) vs dimmed (var(--v2-text-disabled))
    receiver?: 'main' | 'sub';
  }

  let { freq, compact = false, active = true, receiver = 'main' }: Props = $props();

  let model = $derived(projectFrequencyReadout({ confirmedHz: freq }));
  const owner = createFrequencyInteraction({
    get confirmedHz() { return freq; },
    get digits() { return model.digits; },
    disabled: true,
    get receiver() { return receiver; },
    minFreq: 0,
    maxFreq: 999_000_000,
  });
  const lease = createFrequencyInteractionLease(owner, () => true);
  onDestroy(lease.revoke);
</script>

<StandardFrequencyReadout
  {model}
  interaction={lease.interaction}
  presentation="passive"
  {compact}
  {active}
  {receiver}
  vfoFreqHook={false}
/>
