<script lang="ts" generics="Lease extends { dispose(): void }, Seat extends FiniteRendererSeat<Lease>">
  import { onDestroy, untrack, type Component } from 'svelte';
  import type { TransmitCapability } from '$lib/transmit-capabilities';
  import type { FiniteRendererSeat } from './control-instrument-renderer.svelte';

  interface Props {
    seat: Seat;
    renderer: Component<{ lease: Lease }>;
    capability?: TransmitCapability;
  }

  let { seat, renderer: Renderer, capability }: Props = $props();
  // The keyed parent makes renderer/context replacement an instance boundary.
  const lease = untrack(() => seat.attachRenderer());
  onDestroy(() => lease.dispose());
</script>

{#if capability}
  <div style="display: contents" data-rp-capability={capability}><Renderer {lease} /></div>
{:else}
  <Renderer {lease} />
{/if}
