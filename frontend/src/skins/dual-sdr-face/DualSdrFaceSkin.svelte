<script lang="ts">
  import { runtime } from '$lib/runtime/frontend-runtime';
  import SemanticRadioSurfaces from '../../components-v2/wiring/SemanticRadioSurfaces.svelte';
  import type { RadioViewModel } from '../../semantic/radio-view-model';
  import DualSdrFace from './DualSdrFace.svelte';

  const hardwareScope = {
    subscribe: (listener: Parameters<typeof runtime.scope.subscribeHardware>[0]) =>
      runtime.scope.subscribeHardware(listener),
  };
</script>

{#snippet readonlyDisplay(view: RadioViewModel)}
  <!-- No command callback: this production entrypoint remains display-only. -->
  <DualSdrFace {view} scopeSource={hardwareScope} />
{/snippet}

<SemanticRadioSurfaces {readonlyDisplay} />
