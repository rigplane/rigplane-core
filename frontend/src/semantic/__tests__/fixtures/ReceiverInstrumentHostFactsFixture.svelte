<script lang="ts">
  import ReceiverInstrumentHost, {
    type ReceiverInstrumentHandles,
    type ReceiverVfoAppearance,
    type SubscribeReceiverAuthority,
  } from '../../ReceiverInstrumentHost.svelte';
  import type { VfoFactKind } from '../../VfoIndicatorRow.svelte';

  interface Props {
    subscribeControlAuthority: SubscribeReceiverAuthority;
  }

  let { subscribeControlAuthority }: Props = $props();

  const FACTS: readonly VfoFactKind[] = ['bandwidth', 'agc', 'nb', 'nr'];
</script>

{#snippet operations(appearance: ReceiverVfoAppearance)}
  <button type="button" data-vfo-operations data-vfo-operation-appearance={appearance}>VFO operations</button>
{/snippet}

{#snippet hosted(handles: ReceiverInstrumentHandles)}
  <div data-facts-host>
    <section data-facts-owner="MAIN">{@render handles.mainFacts(FACTS)}</section>
    {#if handles.subFacts}
      <section data-facts-owner="SUB">{@render handles.subFacts(FACTS)}</section>
    {/if}
  </div>
{/snippet}

<ReceiverInstrumentHost {subscribeControlAuthority} vfoOperations={operations} children={hosted} />
