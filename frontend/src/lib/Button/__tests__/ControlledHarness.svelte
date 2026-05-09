<script lang="ts">
  import ControlButton from '../ControlButton.svelte';
  import FillButton from '../FillButton.svelte';
  import DotButton from '../DotButton.svelte';
  import HardwareButton from '../HardwareButton.svelte';
  import HardwarePlainButton from '../HardwarePlainButton.svelte';

  export type Family = 'control' | 'fill' | 'dot' | 'hardware' | 'hardware-plain';

  interface Props {
    family?: Family;
    initial?: boolean;
  }

  let { family = 'control', initial = false }: Props = $props();

  let active = $state(false);

  $effect(() => {
    active = initial;
  });
</script>

<!-- data-testid="parent-toggle" lets the test drive parent state externally -->
<button type="button" data-testid="parent-toggle" onclick={() => { active = !active; }}>
  toggle
</button>

{#if family === 'fill'}
  <FillButton {active} onclick={() => {}} />
{:else if family === 'dot'}
  <DotButton {active} onclick={() => {}} />
{:else if family === 'hardware'}
  <HardwareButton {active} onclick={() => {}} />
{:else if family === 'hardware-plain'}
  <HardwarePlainButton {active} onclick={() => {}} />
{:else}
  <ControlButton {active} onclick={() => {}} />
{/if}
