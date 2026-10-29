<script lang="ts" module>
  /** Test-controlled effective region forwarded to the overlay, so a test
   *  can switch regions on a live overlay instance at an unchanged viewport
   *  (remounting would clear the fetch cache and miss the defect). */
  export const harnessRegion = $state({ value: 'US' });
</script>

<script lang="ts">
  import BandPlanOverlay from '../BandPlanOverlay.svelte';
</script>

<BandPlanOverlay
  startFreq={146_000_000}
  endFreq={146_500_000}
  visible={true}
  hiddenLayers={[]}
  region={harnessRegion.value}
/>
