<script lang="ts">
  import { runtime } from '$lib/runtime/frontend-runtime';
  import { scopeSlotKey } from '$lib/runtime/adapters/scope-adapter';
  import { getWsConnected, hasEverConnected } from '$lib/stores/connection.svelte';
  import { deriveLinkFault } from '$lib/runtime/adapters/link-fault';
  import StatusBar from '../../components-v2/layout/StatusBar.svelte';
  import SemanticRadioSurfaces from '../../components-v2/wiring/SemanticRadioSurfaces.svelte';
  import type { RadioViewModel } from '../../semantic/radio-view-model';
  import DualSdrFace from './DualSdrFace.svelte';

  // MOR-2241 — this face composes no layout shell, so it carries the
  // link-fault veil (app.css's shared `[data-link-fault]` rule) on its own
  // wrapper; `display: contents` keeps the wrapper out of the box tree, and
  // the rule filters the children, never the wrapper.
  let linkFault = $derived(deriveLinkFault({
    everConnected: hasEverConnected(),
    wsConnected: getWsConnected(),
    connectionStale: runtime.connectionStale,
  }));
  let linkFaultAttribute = $derived(linkFault === 'none' ? undefined : linkFault);

  const hardwareScope = {
    subscribe: (listener: (frame: import('$lib/runtime/scope-frame').ScopeFrame, slotKey: string) => void) =>
      runtime.scope.stream('hardware').subscribe(listener),
    subscribeHealth: (listener: (live: boolean) => void) =>
      runtime.scope.subscribeHealth((source, health) => {
        if (source === 'hardware') {
          listener(health.transport === 'connected' && health.frameSeen);
        }
      }),
    get subscriptionStream() { return runtime.scope.stream('hardware'); },
    get slotKeys() {
      const stream = runtime.scope.stream('hardware');
      return [scopeSlotKey(stream.sourceId, stream.kind === 'radio' ? 0 : null),
        scopeSlotKey(stream.sourceId, stream.kind === 'radio' ? 1 : null)] as const;
    },
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

<div class="dual-sdr-face-root" data-link-fault={linkFaultAttribute} style="display: contents">
  <StatusBar />
  <SemanticRadioSurfaces {readonlyDisplay} />
</div>
