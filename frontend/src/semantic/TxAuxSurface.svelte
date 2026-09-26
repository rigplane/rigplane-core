<!-- Grouped presentation for host-owned TX auxiliary instruments. -->
<script module lang="ts">
  import { TX_AUX_LEVELS, type TxAuxScalarHandles } from './tx-aux-scalar';
  import { TX_AUX_TOGGLES, type TxAuxFiniteHandles } from './tx-aux-finite';

  export {
    TX_AUX_FEEDBACK_LEVELS,
    TX_AUX_LEVELS,
    type TxAuxFeedbackLevelField,
    type TxAuxLevelFeedback,
    type TxAuxLevelField,
  } from './tx-aux-scalar';
  export { TX_AUX_TOGGLES, type TxAuxToggleField } from './tx-aux-finite';
</script>

<script lang="ts">
  import type { RadioViewModel } from './radio-view-model';
  import { blockedLabel, keyBlockedReasons, nextSurfaceId, type TxAuthoritySnapshot } from './rx-tx-surface';

  interface Props {
    view: RadioViewModel;
    tx: TxAuthoritySnapshot;
    scalarHandles: TxAuxScalarHandles;
    finiteHandles: TxAuxFiniteHandles;
    showScalars?: boolean;
    showFinite?: boolean;
  }
  let {
    view, tx, scalarHandles, finiteHandles, showScalars = true, showFinite = true,
  }: Props = $props();
  let txAux = $derived(view.txAux);
  let tuneBlocked = $derived(keyBlockedReasons(view, tx));
  let visibleTuneBlocked = $derived(tuneBlocked.filter((code) => code !== 'rf-state-unknown'));
  // MOR-1350: the surface-level half of the DisabledReason doctrine,
  // mirroring `RxTxSurface`'s aria-describedby pattern — the joined block
  // reasons are exposed to assistive tech on the section itself, not only
  // as visible `<li data-reason>` list items (TUNE's own button carries its
  // per-control reason in `TxAuxFiniteHost`, MOR-1481).
  const blockedId = nextSurfaceId();
  let tuneBlockedText = $derived(
    txAux?.atu.availability.structural
      ? visibleTuneBlocked.map((code) => blockedLabel(code)).join('; ') : '',
  );
</script>

{#if txAux}
  <section class="tx-aux-surface" data-testid="tx-aux-surface" aria-label="Transmit auxiliary controls"
    aria-describedby={tuneBlockedText ? blockedId : undefined}
  >
    {#if showFinite}
      <div class="tx-aux-row">
        {#each TX_AUX_TOGGLES as [field] (field)}
          {@render finiteHandles[field]()}
        {/each}
        {@render finiteHandles.atuTune()}
      </div>
    {/if}

    {#if showScalars}
      {#each TX_AUX_LEVELS as [field] (field)}
        {@render scalarHandles[field]()}
      {/each}
    {/if}

    {#if txAux.atu.availability.structural}
      {#if tuneBlockedText}<span id={blockedId} class="sr-only">{tuneBlockedText}</span>{/if}
      <ul class="tx-aux-blocked" data-testid="tx-aux-tune-blocked">
        {#each visibleTuneBlocked as code (code)}<li data-reason={code}>{blockedLabel(code)}</li>{/each}
      </ul>
    {/if}
  </section>
{/if}

<style>
  .tx-aux-surface { display: flex; flex-direction: column; gap: 0.25rem; }
  .tx-aux-row { display: flex; flex-wrap: wrap; gap: 0.5rem; }
  .tx-aux-blocked { margin: 0; padding-inline-start: 1.2em; }
  .tx-aux-blocked:empty { display: none; }
  .sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
</style>
