<script lang="ts">
  import { runtime } from '$lib/runtime/frontend-runtime';
  import StatusBar from '../../components-v2/layout/StatusBar.svelte';
  import SemanticRadioSurfaces from '../../components-v2/wiring/SemanticRadioSurfaces.svelte';
  import type { RadioViewModel } from '../../semantic/radio-view-model';
  import DualSdrFace from './DualSdrFace.svelte';

  const hardwareScope = {
    subscribe: (listener: Parameters<typeof runtime.scope.subscribeHardware>[0]) =>
      runtime.scope.subscribeHardware(listener),
    subscribeHealth: (listener: (live: boolean) => void) =>
      runtime.scope.subscribeHealth((source, health) => {
        if (source === 'hardware') {
          listener(health.transport === 'connected' && health.frameSeen);
        }
      }),
  };

  $effect(() => {
    const lease = runtime.acquireHardwareScope('DualSdrFace');
    return () => runtime.releaseHardwareScope(lease);
  });
</script>

{#snippet readonlyDisplay(view: RadioViewModel)}
  <!-- No command callback: this production entrypoint remains display-only. -->
  <DualSdrFace {view} scopeSource={hardwareScope} />
{/snippet}

<StatusBar />
<SemanticRadioSurfaces {readonlyDisplay} />
