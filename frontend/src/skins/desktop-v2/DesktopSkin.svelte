<!--
  Desktop V2 Skin — standard layout with spectrum/waterfall, and the v3
  presentation entrypoint registered under the `desktop-v2` layout manifest
  (MOR-1266, `presentation/layouts/desktop-declarations.ts`).

  App's persistent semantic host supplies the instrument composition. This
  replaceable entrypoint names the layout and forwards those exact handles to
  RadioLayout, which owns their Standard placement and responsive geometry.
-->
<script lang="ts">
  import type { InstrumentComposition } from '../../components-v2/wiring/SemanticRadioSurfaces.svelte';
  import RadioLayout from '../../components-v2/layout/RadioLayout.svelte';
  import './semantic-controls.css';

  let { instruments }: { instruments: InstrumentComposition } = $props();
</script>

<RadioLayout skinId="desktop-v2" {instruments} />
