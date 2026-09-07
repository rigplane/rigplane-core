<script lang="ts">
  import { onDestroy, untrack, type Snippet } from 'svelte';
  import type { SignalMeterFrame } from '../components-v2/meters/signal-meter-motion.svelte';
  import type { ActionRendererSeat } from '../primitives/control-instruments/control-instrument-renderer.svelte';
  import type { MeterReading, MeterValueDomain } from '../semantic/radio-view-model';
  import type { StationLevelMeterFrame } from '../semantic/StationMeterInstrumentHost.svelte';
  import {
    toLevelMeterRendererView, toSignalMeterRendererView,
  } from '../semantic/meter-renderer-view';
  import { getSelectedMeterAppearance } from './activation';

  interface SignalProps {
    kind?: 'signal';
    frame: SignalMeterFrame;
    reading: MeterReading;
    domain?: MeterValueDomain;
    relevant?: boolean;
    selectedPresent?: boolean;
    fallback: Snippet<[frame: SignalMeterFrame]>;
  }
  interface LevelProps {
    kind: 'level';
    frame: StationLevelMeterFrame;
    resetPeakSeat?: ActionRendererSeat;
    fallback: Snippet<[frame: StationLevelMeterFrame, resetPeakSeat?: ActionRendererSeat]>;
  }
  type Props = SignalProps | LevelProps;

  let props: Props = $props();
  const appearance = untrack(() => getSelectedMeterAppearance());
  const signalRenderer = untrack(() => props.kind === 'level' ? undefined : appearance?.signal);
  const levelRenderer = untrack(() => props.kind === 'level' ? appearance?.level : undefined);
  const resetPeak = untrack(() => props.kind === 'level' && levelRenderer
    ? props.resetPeakSeat?.attachRenderer() : undefined);
  const signalView = $derived(props.kind === 'level' ? null
    : toSignalMeterRendererView(props.frame, props.reading, props.domain, props.relevant));
  const levelView = $derived(props.kind === 'level'
    ? toLevelMeterRendererView(props.frame) : null);
  onDestroy(() => resetPeak?.dispose());
</script>

{#if props.kind === 'level'}
  {#if levelRenderer}
    {@const Renderer = levelRenderer}
    <Renderer view={levelView!} {resetPeak} />
  {:else}
    {@render props.fallback(props.frame, props.resetPeakSeat)}
  {/if}
{:else if signalRenderer}
  {#if props.selectedPresent !== false}
    {@const Renderer = signalRenderer}
    <Renderer view={signalView!} />
  {/if}
{:else}
  {@render props.fallback(props.frame)}
{/if}
