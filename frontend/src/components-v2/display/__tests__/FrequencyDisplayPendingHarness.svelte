<!--
  MOR-2911 test harness: drives the passive FrequencyDisplay facade's
  `freq`/`pendingDisplayHz` props from test code through $state, so a single
  mount can cross the pending → resolved transition. Same idiom as
  AlternateFrequencyReadoutHarness.svelte (a fixture beside its suite).
-->
<script lang="ts">
  import FrequencyDisplay from '../FrequencyDisplay.svelte';

  let freq = $state(14_235_000);
  let pendingDisplayHz = $state<number | null>(null);

  export function setFrequency(nextFreq: number, nextPending: number | null): void {
    freq = nextFreq;
    pendingDisplayHz = nextPending;
  }
</script>

<FrequencyDisplay {freq} {pendingDisplayHz} compact active />
