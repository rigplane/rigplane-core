<script lang="ts">
  import type { SkinId } from '../../../../skins/registry';
  import RadioLayout from '../../RadioLayout.svelte';
  import SemanticRadioSurfaces, {
    type InstrumentComposition,
  } from '../../../wiring/SemanticRadioSurfaces.svelte';

  let { skinId = 'desktop-v2' }: { skinId?: SkinId } = $props();
</script>

<SemanticRadioSurfaces>
  {#snippet children(instruments: InstrumentComposition)}
    <RadioLayout {skinId} {instruments} />
  {/snippet}
</SemanticRadioSurfaces>
