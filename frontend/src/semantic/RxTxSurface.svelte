<!--
  Semantic RX/TX status and action surface (MOR-1064).

  Presentation only. It receives the MOR-1062 `RadioViewModel` and a snapshot
  of the App-owned TX authority as props, and emits TX intents as callbacks.
  It holds no TX state, keys nothing, and consults no controller — v3 ADR
  invariant 11. The authoritative global TX lamp stays in `AppGlobalHost`
  (MOR-1059); this surface is polite status, not a second alert.

  Colour is not a state channel here: every RF state carries distinct text and
  a distinct shape so it survives forced-colors (MOR-977). A design language
  may restyle any of this; it may not change which facts appear.
-->
<script lang="ts">
  import type { RadioViewModel } from './radio-view-model';
  import {
    BLOCKED_LABEL, RF_LABEL, RF_MARK, SESSION_LABEL, keyBlockedReasons, nextSurfaceId,
    rfState, txDisabledReasons, txOrigin, txSessionState, type TxAuthoritySnapshot,
  } from './rx-tx-surface';

  interface Props {
    view: RadioViewModel;
    tx: TxAuthoritySnapshot;
    onRequestKey?: () => void;
    onRequestUnkey?: () => void;
  }
  let { view, tx, onRequestKey, onRequestUnkey }: Props = $props();

  const blockedId = nextSurfaceId();
  let rf = $derived(rfState(tx));
  let session = $derived(txSessionState(tx));
  let blocked = $derived(keyBlockedReasons(view, tx));
  let viewBlocked = $derived(txDisabledReasons(view));
  let pressed = $derived(tx.mayOwnKey || (tx.phase !== 'idle' && tx.phase !== 'failed'));
  let known = $derived(view.txTarget.status === 'known');
  let receiver = $derived(view.txTarget.status === 'known' ? view.txTarget.receiver : undefined);
  let slot = $derived(view.txTarget.status === 'known'
    ? (view.txTarget.slot.kind === 'slotted' ? view.txTarget.slot.id : view.txTarget.slot.kind)
    : undefined);
  let frequencyHz = $derived(view.txTarget.status === 'known' ? view.txTarget.frequencyHz : null);
  let reason = $derived(view.txTarget.status === 'unknown' ? view.txTarget.reason : undefined);
</script>

<section class="rx-tx-surface" data-testid="rx-tx-surface" aria-label="Transmitter status and control">
  <p
    class="rx-tx-state" role="status" data-testid="rx-tx-state"
    data-rf={rf} data-session={session} data-origin={txOrigin(tx)} data-intent={tx.intent ?? undefined}
  >
    <span class="rx-tx-mark" data-testid="rx-tx-rf-mark" aria-hidden="true">{RF_MARK[rf]}</span>
    <span class="rx-tx-label" data-testid="rx-tx-rf-label">{RF_LABEL[rf]}</span>
    <span class="rx-tx-session">{SESSION_LABEL[session]}</span>
    {#if tx.intent}<span class="rx-tx-intent">· {tx.intent}</span>{/if}
    <span class="rx-tx-origin">· {txOrigin(tx)}</span>
  </p>

  {#if tx.fault}
    <p class="rx-tx-fault" data-testid="rx-tx-fault" data-fault={tx.fault}>TX fault: {tx.fault}</p>
  {/if}

  {#if known}
    <p data-testid="rx-tx-target" data-target="known" data-receiver={receiver} data-slot={slot}>
      TX target: {receiver} {slot} · {frequencyHz ?? '—'} Hz
    </p>
  {:else}
    <p data-testid="rx-tx-target" data-target="unknown" data-reason={reason}>
      TX target unknown ({reason})
    </p>
  {/if}

  <div class="rx-tx-actions">
    <button
      type="button" class="rx-tx-key" data-testid="rx-tx-key"
      disabled={blocked.length > 0} aria-pressed={pressed} aria-describedby={blockedId}
      onclick={() => onRequestKey?.()}
    >Key transmitter</button>
    <!-- Never gated: no `disabled`, no `{#if}`, no guard in the handler. -->
    <button
      type="button" class="rx-tx-unkey" data-testid="rx-tx-unkey"
      onclick={() => onRequestUnkey?.()}
    >Unkey transmitter</button>
  </div>

  <ul class="rx-tx-blocked" id={blockedId} data-testid="rx-tx-blocked">
    {#each blocked as code (code)}<li data-reason={code}>{BLOCKED_LABEL[code]}</li>{/each}
    {#each viewBlocked as item (item.field + item.code)}
      <li data-reason={item.code} data-field={item.field}>{item.field}: {item.code}</li>
    {/each}
  </ul>
</section>

<style>
  /* Structure only — a design language owns colour and must never become the sole state channel. */
  .rx-tx-surface { display: flex; flex-direction: column; gap: 0.25rem; }
  .rx-tx-state { display: flex; align-items: baseline; gap: 0.4ch; margin: 0; }
  .rx-tx-label { font-weight: 700; letter-spacing: 0.08em; }
  .rx-tx-fault { margin: 0; font-weight: 700; }
  .rx-tx-actions { display: flex; gap: 0.5rem; }
  .rx-tx-blocked { margin: 0; padding-inline-start: 1.2em; }
  .rx-tx-blocked:empty { display: none; }
</style>
