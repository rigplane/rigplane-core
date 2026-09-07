<!--
  Semantic RF-front-end surface (MOR-1306, vocabulary slice 6B; grown by
  MOR-2425 RF-B to place finite-control handles alongside the continuous
  RF/SQL/gain/squelch handles already required from
  `RfFrontEndInstrumentHost`).

  Presentation only. It renders the MOR-1262 decomposition family 11
  `rfFrontEnd` fact group (MOR-1292/MOR-1293) by placing host-owned
  instrument handles; it holds no state and consults no controller.
  Preamp/attenuator/DIGI-SEL/IP+ rendering, the preamp mutex, and the
  MOR-1441 leg 2 pending affordance are all owned by
  `RfFrontEndInstrumentHost.svelte` (see its own doc comment) — this file
  only decides WHERE each handle is placed.

  Two-level availability (MOR-977/1256), same as every sibling surface:
  `structural: false` renders NOTHING for that field — "this radio has no
  squelch" is a different claim from "squelch was never observed", which
  renders present-and-disabled. RF gain/squelch are gated here so a
  structurally absent field never even reaches its handle; the other four
  fields self-gate inside their own host-owned handle.
-->
<script module lang="ts">
  export {
    RF_FRONT_END_LEVELS,
    type RfFrontEndLevelField,
    type RfSqlControlModel,
    RF_FRONT_END_TOGGLES,
    type RfFrontEndToggleField,
    DISABLED_REASON_LABEL,
  } from './rf-front-end-instruments';

  /** The one rendering of "not read". Never 0, never the last value. */
  export const UNKNOWN_TEXT = '?';
</script>

<script lang="ts">
  import type { RadioViewModel } from './radio-view-model';
  import type { RfFrontEndFiniteLayout, RfFrontEndLevelHandles } from './rf-front-end-instruments';

  interface Props {
    view: RadioViewModel;
    levelHandles: RfFrontEndLevelHandles;
    finiteLayout?: RfFrontEndFiniteLayout;
  }
  let { view, levelHandles, finiteLayout }: Props = $props();

  let rf = $derived(view.rfFrontEnd);
</script>

{#if rf}
  <section class="rf-front-end-surface" data-testid="rf-front-end-surface" aria-label="RF front end">
    {#if finiteLayout}
      {@render finiteLayout(levelHandles)}
    {:else}
      {@render levelHandles.preamp()}
      {@render levelHandles.attenuator()}
    {/if}

    {#if levelHandles.kind === 'combined'}
      {@render levelHandles.rfSql()}
    {:else}
      {#if rf.rfGain.availability.structural}{@render levelHandles.rfGain()}{/if}
      {#if rf.squelch.availability.structural}{@render levelHandles.squelch()}{/if}
    {/if}

    {#if !finiteLayout}
      {@render levelHandles.digiSel()}
      {@render levelHandles.ipPlus()}
    {/if}
  </section>
{/if}

<style>
  /* Structure only — a design language owns colour and must never become the
     sole state channel (MOR-977, forced-colors). */
  .rf-front-end-surface { display: flex; flex-direction: column; gap: 0.25rem; }
</style>
