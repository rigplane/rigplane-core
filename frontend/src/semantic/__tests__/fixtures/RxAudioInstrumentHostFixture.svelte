<script lang="ts">
  import RxAudioInstrumentHost from '../../RxAudioInstrumentHost.svelte';
  import RxAudioSurface from '../../RxAudioSurface.svelte';
  import type {
    FiniteControlAppearance, FiniteRendererContext,
  } from '../../../primitives/control-instruments/control-instrument-renderer.svelte';
  import type {
    AudioFocus, MonitorMode, RadioViewModel, RxAudioViewModel,
  } from '../../radio-view-model';
  import type {
    RxAudioAuthorityPublication,
    RxAudioFiniteChoiceValue,
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
    finiteAppearance?: FiniteControlAppearance<RxAudioFiniteChoiceValue>;
    rendererContext?: FiniteRendererContext | null;
  }

  let {
    publication, rxAudio, view, subscribeControlAuthority, layout = 'grouped', onAfLevelChange,
    onMonitorMode, onRoutingFocus, onRoutingSplit, onSetModInputLan, onModInputChange,
    finiteAppearance, rendererContext = null,
  }: Props = $props();
  let presentation = $derived({ ...publication, rxAudio: rxAudio ?? view?.rxAudio });
  /** The same "undefined selects the no-appearance branch" spread the shipped
   *  `DspInstrumentHostFixture`/`TxAuxScalarHostFixture` use to satisfy
   *  `RxAudioInstrumentHost`'s discriminated `finiteAppearance`/
   *  `rendererContext` prop pair from two independently-optional fixture
   *  props. */
  let selection = $derived(finiteAppearance === undefined
    ? {} : { finiteAppearance, rendererContext });
</script>

<RxAudioInstrumentHost
  {presentation} {subscribeControlAuthority} {onAfLevelChange}
  onMonitorModeChange={onMonitorMode} onFocusChange={onRoutingFocus}
  onSplitStereoChange={onRoutingSplit} {onModInputChange} {onSetModInputLan}
  {...selection}
>
  {#snippet children(handles: RxAudioInstrumentHandles)}
    {#if view}
      <RxAudioSurface {view} {handles} />
    {:else}
      {#key layout}
        <section data-layout={layout}>
          <div data-af-slot={layout}>{@render handles.afLevel()}</div>
        </section>
      {/key}
    {/if}
  {/snippet}
</RxAudioInstrumentHost>
