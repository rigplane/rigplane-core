<script lang="ts">
  import RxAudioInstrumentHost from '../../RxAudioInstrumentHost.svelte';
  import type { RxAudioViewModel } from '../../radio-view-model';
  import type {
    RxAudioAuthorityPublication,
    RxAudioInstrumentHandles,
    SubscribeRxAudioAuthority,
  } from '../../rx-audio-instruments';

  interface Props {
    publication: RxAudioAuthorityPublication;
    rxAudio: RxAudioViewModel | undefined;
    subscribeControlAuthority: SubscribeRxAudioAuthority;
    layout: 'grouped' | 'independent';
    onAfLevelChange?: (value: number) => void;
  }

  let {
    publication, rxAudio, subscribeControlAuthority, layout, onAfLevelChange,
  }: Props = $props();
  let presentation = $derived({ ...publication, rxAudio });
</script>

<RxAudioInstrumentHost {presentation} {subscribeControlAuthority} {onAfLevelChange}>
  {#snippet children(handles: RxAudioInstrumentHandles)}
    {#key layout}
      <section data-layout={layout}>
        <div data-af-slot={layout}>{@render handles.afLevel()}</div>
      </section>
    {/key}
  {/snippet}
</RxAudioInstrumentHost>
