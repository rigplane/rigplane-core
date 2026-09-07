<script lang="ts">
  import ReceiverInstrumentHost, {
    type ReceiverInstrumentHandles,
    type ReceiverVfoAppearance,
    type SubscribeReceiverAuthority,
  } from '../../ReceiverInstrumentHost.svelte';
  import type { ReceiverId } from '../../radio-view-model';

  interface Props {
    subscribeControlAuthority: SubscribeReceiverAuthority;
    pendingFrequencyHz?: Partial<Record<ReceiverId, number>>;
    onTuneFrequency?: (receiver: ReceiverId, frequencyHz: number) => void;
    layout?: 'grouped' | 'independent';
    layoutKey?: string;
  }

  let {
    subscribeControlAuthority,
    pendingFrequencyHz,
    onTuneFrequency,
    layout = 'grouped',
    layoutKey = 'initial',
  }: Props = $props();
</script>

{#snippet operations(appearance: ReceiverVfoAppearance)}
  <button type="button" data-vfo-operations data-vfo-operation-appearance={appearance}>VFO operations</button>
{/snippet}

{#snippet hosted(handles: ReceiverInstrumentHandles)}
  {#key layoutKey}
    <div data-receiver-layout={layout}>
      <section data-frequency-owner="MAIN">{@render handles.mainFrequency({ compact: true, vfoFreqHook: false })}</section>
      {#if handles.subFrequency}<section data-frequency-owner="SUB">{@render handles.subFrequency({ compact: false, vfoFreqHook: false })}</section>{/if}
      <aside data-operation-placement={layout}>{@render handles.vfoOperations(layout === 'grouped' ? 'semantic' : 'sdr')}</aside>
    </div>
  {/key}
{/snippet}

<ReceiverInstrumentHost {subscribeControlAuthority} {pendingFrequencyHz} {onTuneFrequency}
  vfoOperations={operations} children={hosted} />
