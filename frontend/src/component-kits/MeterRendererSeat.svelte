<script lang="ts">
  import { untrack, type Snippet } from 'svelte';
  import type { SignalMeterFrame } from '../components-v2/meters/signal-meter-motion.svelte';
  import type { MeterReading, MeterValueDomain } from '../semantic/radio-view-model';
  import { toSignalMeterRendererView } from '../semantic/meter-renderer-view';
  import * as activation from './activation';

  interface Props {
    frame: SignalMeterFrame;
    reading: MeterReading;
    domain?: MeterValueDomain;
    fallback: Snippet<[frame: SignalMeterFrame]>;
  }

  let { frame, reading, domain, fallback }: Props = $props();
  const selectedRenderer = untrack(() =>
    'getSelectedMeterAppearance' in activation
      ? activation.getSelectedMeterAppearance()?.signal
      : undefined,
  );
  const view = $derived(toSignalMeterRendererView(frame, reading, domain));
</script>

{#if selectedRenderer}
  {@const Renderer = selectedRenderer}
  <Renderer {view} />
{:else}
  {@render fallback(frame)}
{/if}
