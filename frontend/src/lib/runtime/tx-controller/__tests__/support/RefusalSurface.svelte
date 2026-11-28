<script lang="ts">
  import { onDestroy } from 'svelte';
  import RxTxSurface from '../../../../../semantic/RxTxSurface.svelte';
  import { topologyFixtures } from '../../../../../semantic/fixtures/topologies';
  import type { ManagedTxController } from '../../managed-controller';

  let { controller, standard = false }: { controller: ManagedTxController; standard?: boolean } = $props();
  let tx = $state(controller.snapshot());
  onDestroy(controller.subscribe((next) => { tx = next; }));
</script>

<RxTxSurface
  view={topologyFixtures['1/single']} {tx} {standard}
  onRequestKey={() => controller.transmitOn()}
  onRequestUnkey={() => { void controller.forceOff(); }}
/>
