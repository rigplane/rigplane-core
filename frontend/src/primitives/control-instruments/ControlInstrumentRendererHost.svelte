<script lang="ts" generics="Lease extends { dispose(): void }, Seat extends FiniteRendererSeat<Lease>">
  import { onDestroy, untrack, type Component } from 'svelte';
  import type { FiniteRendererSeat } from './control-instrument-renderer.svelte';

  interface Props {
    seat: Seat;
    renderer: Component<{ lease: Lease }>;
  }

  let { seat, renderer: Renderer }: Props = $props();
  // The keyed parent makes renderer/context replacement an instance boundary.
  const lease = untrack(() => seat.attachRenderer());
  onDestroy(() => lease.dispose());
</script>

<Renderer {lease} />
