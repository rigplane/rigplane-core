<script module lang="ts">
  import type { RadioViewModel } from './radio-view-model';
  import type { TxAuthoritySnapshot } from './rx-tx-surface';
  import { pressedOf } from './pressed-of';

  export { ANTENNA_PORTS, UNKNOWN_TEXT, ANTENNA_BLOCKED_LABEL, usable, textOf, valueOf,
    tunerIdle, antennaSwitchBlocks, type AntennaSwitchBlock } from './AntennaInstrumentHost.svelte';
  import { ANTENNA_PORTS, ANTENNA_BLOCKED_LABEL, usable, textOf, valueOf,
    antennaSwitchBlocks, type AntennaInstrumentHandles, type AntennaInstrumentLayout,
  } from './AntennaInstrumentHost.svelte';

  /** Per-instance DOM id, so several mounted surfaces keep distinct aria targets. */
  let sequence = 0;
</script>

<script lang="ts">
  import {
    bindAbsoluteChoiceInstrument, bindToggleInstrument,
  } from '../primitives/control-instruments/control-instrument-behavior';

  interface Props {
    view: RadioViewModel;
    tx: TxAuthoritySnapshot;
    onSelectPort?: (port: number) => void;
    onToggleRxAnt?: () => void;
    handles?: AntennaInstrumentHandles;
    layout?: AntennaInstrumentLayout;
  }
  let { view, tx, onSelectPort, onToggleRxAnt, handles, layout }: Props = $props();

  const legacyBlockedId = `antenna-blocked-${++sequence}`;
  let blockedId = $derived(layout?.blockedId ?? legacyBlockedId);
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
    {#if handles}
      {@render handles.txPort()}
      {@render handles.rxAnt()}
    {:else}
    {@const portBehavior = bindAbsoluteChoiceInstrument<number>(() => ({
      choices: ANTENNA_PORTS, selected: ant ? valueOf(ant.txAntenna) : undefined,
      available: ant !== undefined, blocked: blocked.length > 0,
      invoke: (port) => onSelectPort?.(port),
    }))}
    {@const rxAntBehavior = bindToggleInstrument(() => ({
      field: ant?.rxAnt, blocked: blocked.length > 0, invoke: () => onToggleRxAnt?.(),
    }))}
    <div
      class="antenna-row" role="radiogroup" aria-label="Transmit antenna"
      data-testid="antenna-ports" data-observed={usable(ant.txAntenna)}
    >
      {#each ANTENNA_PORTS as port (port)}
        <button
          type="button" role="radio" class="antenna-choice"
          data-testid={`antenna-port-${port}`} data-port={port}
          aria-checked={portBehavior.isSelected(port)} aria-describedby={blockedId}
          disabled={!portBehavior.available}
          onclick={() => portBehavior.invoke(port)}
        >ANT {port}</button>
      {/each}
      <output data-testid="antenna-port-value">{textOf(ant.txAntenna)}</output>
    </div>

    {#if ant.rxAnt.availability.structural}
      <div class="antenna-row" data-testid="antenna-rx" data-observed={usable(ant.rxAnt)}>
        <button
          type="button" class="antenna-choice" data-testid="antenna-rx-toggle"
          aria-pressed={pressedOf(ant.rxAnt)} aria-describedby={blockedId}
          disabled={!rxAntBehavior.available}
          onclick={() => rxAntBehavior.invoke()}
        >RX-ANT: {textOf(ant.rxAnt)}</button>
      </div>
    {/if}

    {/if}
    <ul class="antenna-blocked" id={blockedId} data-testid="antenna-blocked">
      {#each blocked as code (code)}<li data-reason={code}>{ANTENNA_BLOCKED_LABEL[code]}</li>{/each}
    </ul>
  </section>
{/if}

<style>
  /* Structure only — a design language owns colour and must never become the
     sole state channel (MOR-977, forced-colors). Nothing here animates. */
  .antenna-surface { display: flex; flex-direction: column; gap: 0.25rem; }
  .antenna-row { display: flex; flex-wrap: wrap; align-items: baseline; gap: 0.5rem; }
  .antenna-choice[aria-checked='true'], .antenna-choice[aria-pressed='true'] { font-weight: 700; }
  .antenna-blocked { margin: 0; padding-inline-start: 1.2em; }
  .antenna-blocked:empty { display: none; }
  /* Second channel beside `data-observed`, never the only one: the unknown
     text itself is the primary one and survives forced-colors. */
  [data-observed='false'] { font-style: italic; }
  button:disabled { cursor: not-allowed; }
</style>
