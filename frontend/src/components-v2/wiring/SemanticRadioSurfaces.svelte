<!--
  Wiring for the semantic VFO surface (MOR-1065, slice b).

  This is the ONLY place the pure VFO surface meets live state: it derives the
  MOR-1062 view model from the real runtime through
  `lib/runtime/adapters/radio-view-model-adapter` and turns the surface's
  callback intents into commands. The surface itself stays presentation-only.

  Slice c adds the RX/TX half to this file: the App-owned TX authority
  (v3 ADR invariant 11 — `lib/runtime/tx-controller/app-host`, provided once by
  App.svelte), the RX/TX surface, the fault-recovery affordance, and the
  MOR-617 preflight. Until then `sdr-test` keeps the legacy TxPanel in the
  sidebars, so this layout has exactly one PTT affordance and loses no
  capability. The authoritative global TX lamp stays in AppGlobalHost
  (MOR-1059) throughout and is never duplicated here.
-->
<script lang="ts">
  import { runtime } from '$lib/runtime';
  import { toRadioViewModel } from '$lib/runtime/adapters/radio-view-model-adapter';
  import type { RadioViewModel } from '../../semantic/radio-view-model';
  import VfoSurface, { type VfoSelection } from '../../semantic/VfoSurface.svelte';
  import { makeVfoHandlers } from './command-bus';

  const vfo = makeVfoHandlers();

  // Belt-and-braces contract pin. The adapter annotates its own return type
  // (MOR-1065 ruling 2), so this is the second of two compile-time links.
  let view: RadioViewModel | null = $derived(toRadioViewModel(runtime.state, runtime.caps));

  function selectVfo(target: VfoSelection): void {
    vfo.onVfoSelect(target.receiver, target.slot.kind === 'slotted' ? target.slot.id : null);
  }
  function toggleDualWatch(): void {
    if (view?.dualWatch.status === 'known') vfo.onDualWatchToggle(!view.dualWatch.value);
  }
</script>

<div class="semantic-surfaces" data-testid="semantic-radio-surfaces">
  {#if view}
    <VfoSurface
      viewModel={view}
      onSelectVfo={selectVfo}
      onToggleSplit={vfo.onSplitToggle}
      onToggleDualWatch={toggleDualWatch}
    />
  {/if}
</div>

<style>
  /* Layout only — the surfaces own their own presentation. */
  .semantic-surfaces {
    display: flex;
    flex-direction: column;
    gap: 8px;
    height: 100%;
    overflow: auto;
    font-family: 'Roboto Mono', monospace;
    color: var(--v2-text-primary, #e8e8e8);
  }
</style>
