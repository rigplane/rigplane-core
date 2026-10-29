<script lang="ts" module>
  /** Test-controlled effective region forwarded to the overlay, so a test
   *  can switch regions on a live overlay instance at an unchanged viewport
   *  (remounting would clear the fetch cache and miss the defect). The
   *  production wiring (toolbar -> panel -> overlay) is covered separately
   *  in SpectrumPanel.component.test.ts. Viewport 145.800-146.200 MHz, the
   *  window fixtures/band-plan-responses.json was recorded for. */
  export const harnessRegion = $state({ value: 'US' });
  /** Test-controlled viewport, so a test can pan a live overlay (A -> B -> A)
   *  without remounting it. */
  export const harnessViewport = $state({ start: 145_800_000, end: 146_200_000 });
</script>

<script lang="ts">
  import BandPlanOverlay from '../BandPlanOverlay.svelte';
</script>

<BandPlanOverlay
  startFreq={harnessViewport.start}
  endFreq={harnessViewport.end}
  visible={true}
  hiddenLayers={[]}
  region={harnessRegion.value}
/>
