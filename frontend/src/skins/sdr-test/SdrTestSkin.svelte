<!--
  SDR Test Skin — prototype desktop skin that swaps the twin-VFO + bridge
  block for an IC-7610-inspired "SDR screen" design (see SdrVfoScreen).
  Everything else (sidebars, spectrum, meters dock, status bar) is inherited
  from the standard desktop layout.

  Delegates to RadioLayout, which branches on skinId === 'sdr-test' and
  swaps VfoHeader for SdrVfoScreen in the top slot.
-->
<script lang="ts">
  import RadioLayout from '../../components-v2/layout/RadioLayout.svelte';
</script>

<RadioLayout skinId="sdr-test" />
