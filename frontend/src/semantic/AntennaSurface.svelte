<script module lang="ts">
  import type { RadioViewModel } from './radio-view-model';
  import type { TxAuthoritySnapshot } from './rx-tx-surface';

  export { ANTENNA_PORTS, UNKNOWN_TEXT, ANTENNA_BLOCKED_LABEL, usable, textOf,
    tunerIdle, antennaSwitchBlocks, type AntennaSwitchBlock } from './AntennaInstrumentHost.svelte';
  import { ANTENNA_BLOCKED_LABEL, antennaSwitchBlocks,
    type AntennaInstrumentHandles, type AntennaInstrumentLayout,
  } from './AntennaInstrumentHost.svelte';
</script>

<script lang="ts">
  interface Props {
    view: RadioViewModel;
    tx: TxAuthoritySnapshot;
    handles: AntennaInstrumentHandles;
    layout: AntennaInstrumentLayout;
  }
  let { view, tx, handles, layout }: Props = $props();

  /** Absent group ⇒ this surface renders nothing (S0 optional-group doctrine):
   *  a single-port radio gets no empty panel and no zone had to learn about it. */
  let ant = $derived(view.antenna);
  let blocked = $derived(antennaSwitchBlocks(view, tx));
</script>

{#if ant}
  <section
    class="antenna-surface" data-testid="antenna-surface" aria-label="Antenna selection"
    data-antenna-count={ant.antennaCount} data-switch-blocked={blocked.length > 0}
  >
    {@render handles.txPort()}
    {@render handles.rxAnt()}
    <ul class="antenna-blocked" id={layout.blockedId} data-testid="antenna-blocked">
      {#each blocked as code (code)}<li data-reason={code}>{ANTENNA_BLOCKED_LABEL[code]}</li>{/each}
    </ul>
  </section>
{/if}

<style>
  /* Structure only — a design language owns colour and must never become the
     sole state channel (MOR-977, forced-colors). Nothing here animates. */
  .antenna-surface { display: flex; flex-direction: column; gap: 0.25rem; }
  .antenna-blocked { margin: 0; padding-inline-start: 1.2em; }
  .antenna-blocked:empty { display: none; }
</style>
