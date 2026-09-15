<script lang="ts">
  import { getRadioState } from '../../../stores/radio.svelte';
  import { getConnectionStatus } from '../../../stores/connection.svelte';
  import { hasAudio } from '../../../stores/capabilities.svelte';
  import CwKeyerInstrumentHostFixture from '../../../../semantic/__tests__/fixtures/CwKeyerInstrumentHostFixture.svelte';
  import { topologyFixtures, withCwKeyer } from '../../../../semantic/fixtures/topologies';

  const view = withCwKeyer(topologyFixtures['1/single']);
  let state = $derived(getRadioState());
  let feedback = $derived({
    confirmed: state?.breakInDelay ?? null,
    target: null,
    requestedTarget: null,
    phase: state === null ? 'unavailable' as const : 'idle' as const,
    transitionId: null,
    outcome: null,
    providerGeneration: state?.providerGeneration,
  });
</script>

<div data-testid="connection">{getConnectionStatus()}</div>
<div data-testid="frequency">{state?.main.freqHz ?? 'unknown'}</div>
{#if hasAudio()}<div data-testid="audio">Audio WebSocket</div>{/if}
<CwKeyerInstrumentHostFixture {view} breakInDelayFeedback={feedback} />
