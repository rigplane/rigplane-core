<script lang="ts">
  import { createFrequencyInstrumentBinding } from '../../primitives/frequency/frequency-instrument.svelte';
  import FrequencyRendererSeat from '../../primitives/frequency/FrequencyRendererSeat.svelte';

  interface Props {
    freq: number;       // frequency in Hz (e.g. 14235000)
    /**
     * MOR-2911 — the pending (not-yet-confirmed) tuning target, DISPLAY
     * ONLY: when non-null the digit readout shows THIS value instead of
     * `freq` while a tune is in flight, exactly the affordance
     * `FrequencyDisplayInteractive` gives the desktop through the same
     * `FrequencyRendererSeat`. This facade is passive and disabled, so no
     * gesture arithmetic exists here to contaminate; `freq` remains the
     * sole confirmed truth.
     */
    pendingDisplayHz?: number | null;
    compact?: boolean;  // smaller variant (~18px vs ~28px)
    active?: boolean;   // bright (var(--v2-text-bright)) vs dimmed (var(--v2-text-disabled))
    receiver?: 'main' | 'sub';
  }

  let { freq, pendingDisplayHz = null, compact = false, active = true, receiver = 'main' }: Props = $props();

  // MOR-2911 (MOR-2215 audit F5): the parallel copy of the seat logic that
  // used to live here (inline getSelectedFrequencyReadout, a hand-rolled
  // createFrequencyInteractionLease, a confirmed-only projection) is retired;
  // the facade is now a thin passive binding over the shared seat, so the
  // phone inherits the same renderer selection/lease lifetime (and pending
  // projection) the desktop mounts use.
  const binding = createFrequencyInstrumentBinding({
    get confirmedHz() { return freq; },
    get pendingDisplayHz() { return pendingDisplayHz; },
    disabled: true,
    context: {},
    get receiver() { return receiver; },
    minFreq: 0,
    maxFreq: 999_000_000,
  });
</script>

<FrequencyRendererSeat {binding} presentation="passive" {compact} {active} {receiver} vfoFreqHook={false} />
