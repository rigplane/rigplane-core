<script lang="ts">
  import RxAudioInstrumentHost from '../../RxAudioInstrumentHost.svelte';
  import RxAudioSurface from '../../RxAudioSurface.svelte';
  import type {
    AudioFocus, MonitorMode, RadioViewModel, RxAudioViewModel,
  } from '../../radio-view-model';
  import type {
    RxAudioAuthorityPublication,
    RxAudioInstrumentHandles,
    SubscribeRxAudioAuthority,
  } from '../../rx-audio-instruments';

  interface Props {
    publication: RxAudioAuthorityPublication;
    rxAudio?: RxAudioViewModel;
    view?: RadioViewModel;
    subscribeControlAuthority: SubscribeRxAudioAuthority;
    layout?: 'grouped' | 'independent';
    onAfLevelChange?: (value: number) => void;
    onMonitorMode?: (mode: MonitorMode) => void;
    onRoutingFocus?: (focus: AudioFocus) => void;
    onRoutingSplit?: (split: boolean) => void;
    onSetModInputLan?: () => void;
    onModInputChange?: (source: number) => void;
  }

  let {
    publication, rxAudio, view, subscribeControlAuthority, layout = 'grouped', onAfLevelChange,
    onMonitorMode, onRoutingFocus, onRoutingSplit, onSetModInputLan, onModInputChange,
  }: Props = $props();
  let presentation = $derived({ ...publication, rxAudio: rxAudio ?? view?.rxAudio });
</script>

<RxAudioInstrumentHost {presentation} {subscribeControlAuthority} {onAfLevelChange}>
  {#snippet children(handles: RxAudioInstrumentHandles)}
    {#if view}
      <RxAudioSurface
        {view} {handles} {onMonitorMode} {onRoutingFocus} {onRoutingSplit}
        {onSetModInputLan} {onModInputChange}
      />
    {:else}
      {#key layout}
        <section data-layout={layout}>
          <div data-af-slot={layout}>{@render handles.afLevel()}</div>
        </section>
      {/key}
    {/if}
  {/snippet}
</RxAudioInstrumentHost>
