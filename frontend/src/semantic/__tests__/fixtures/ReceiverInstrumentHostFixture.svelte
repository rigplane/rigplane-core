<script lang="ts">
  import ReceiverInstrumentHost, {
    type ReceiverInstrumentHandles,
    type ReceiverVfoAppearance,
    type SubscribeReceiverAuthority,
  } from '../../ReceiverInstrumentHost.svelte';
  import LinearSMeter from '../../../components-v2/meters/LinearSMeter.svelte';
  import type { SignalMeterFrame } from '../../../components-v2/meters/signal-meter-motion.svelte';
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

  const meterFrameIds = new WeakMap<SignalMeterFrame, number>();
  let nextMeterFrameId = 0;

  function meterFrameId(frame: SignalMeterFrame): number {
    const existing = meterFrameIds.get(frame);
    if (existing !== undefined) return existing;
    const created = ++nextMeterFrameId;
    meterFrameIds.set(frame, created);
    return created;
  }
</script>

{#snippet operations(appearance: ReceiverVfoAppearance)}
  <button type="button" data-vfo-operations data-vfo-operation-appearance={appearance}>VFO operations</button>
{/snippet}

{#snippet mainMeter(frame: SignalMeterFrame)}
  <div data-meter-frame={meterFrameId(frame)}>
    <LinearSMeter {frame} compact label="MAIN" variant="vfo" />
  </div>
{/snippet}

{#snippet subMeter(frame: SignalMeterFrame)}
  <div data-meter-frame={meterFrameId(frame)}>
    <LinearSMeter {frame} label="SUB" variant="vfo-wide" />
  </div>
{/snippet}

{#snippet hosted(handles: ReceiverInstrumentHandles)}
  {#key layoutKey}
    <div data-receiver-layout={layout}>
      <section data-frequency-owner="MAIN" data-frequency-tunable={handles.frequencyTunable('MAIN')}>{@render handles.mainFrequency({ compact: true, vfoFreqHook: false })}</section>
      {#if handles.subFrequency}<section data-frequency-owner="SUB" data-frequency-tunable={handles.frequencyTunable('SUB')}>{@render handles.subFrequency({ compact: false, vfoFreqHook: false })}</section>{/if}
      <section data-meter-owner="MAIN">{@render handles.mainSMeter(mainMeter)}</section>
      {#if handles.subSMeter}<section data-meter-owner="SUB">{@render handles.subSMeter(subMeter)}</section>{/if}
      <aside data-operation-placement={layout}>{@render handles.vfoOperations(layout === 'grouped' ? 'semantic' : 'sdr')}</aside>
    </div>
  {/key}
{/snippet}

<ReceiverInstrumentHost {subscribeControlAuthority} {pendingFrequencyHz} {onTuneFrequency}
  vfoOperations={operations} children={hosted} />
