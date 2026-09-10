<!--
  Semantic RX/TX status and action surface (MOR-1064).

  Presentation only. It receives the MOR-1062 `RadioViewModel` and a snapshot
  of the server-owned TX projection as props, and emits TX intents as callbacks.
  It holds no TX state, keys nothing, and consults no controller — v3 ADR
  invariant 11. The authoritative global TX lamp stays in `AppGlobalHost`
  (MOR-1059); this surface is polite status, not a second alert.

  Confirmed and uncertain TX carry text and shape so they survive forced
  colors. Passive RX and unknown reserve the same quiet space.
-->
<script lang="ts">
  import '../components-v2/controls/control-button.css';
  import { renderSlot } from './design-language-renderers';
  import type { RadioViewModel } from './radio-view-model';
  import {
    RF_LABEL, RF_MARK, SESSION_LABEL, blockedLabel, faultMessage, keyBlockedReasons, nextSurfaceId,
    rfState, targetUnknownMessage, txDisabledReasons, txSessionState,
    type RfState, type TxAuthoritySnapshot,
  } from './rx-tx-surface';

  /** The `.v2-status-indicator` badge treatment per RF state (the shared
   *  `control-button.css` vocabulary, applied as classes on the existing label
   *  span rather than through `StatusIndicator.svelte` — that component renders
   *  an extra span, and `semantic-tx-aux-wiring.component.test.ts`'s
   *  `DEFAULT_PATH_OUTLINE` pins this subtree's element sequence).
   *
   *  Confirmed and uncertain TX remain active and explicit. Receiving and
   *  unknown share the quiet, inactive treatment. */
  const RF_BADGE: Record<RfState, { color: 'green' | 'red' | 'amber' | 'muted'; active: boolean }> = {
    receiving: { color: 'muted', active: false },
    transmitting: { color: 'red', active: true },
    uncertain: { color: 'amber', active: true },
    unknown: { color: 'muted', active: false },
  };

  interface Props {
    view: RadioViewModel;
    tx: TxAuthoritySnapshot;
    onRequestKey: () => void;
    onRequestUnkey: () => void;
  }
  let { view, tx, onRequestKey, onRequestUnkey }: Props = $props();

  const blockedId = nextSurfaceId();
  let rf = $derived(rfState(tx));
  let session = $derived(txSessionState(tx));
  let blocked = $derived(keyBlockedReasons(view, tx));
  let visibleBlocked = $derived(blocked.filter((code) => code !== 'rf-state-unknown'));
  let viewBlocked = $derived(txDisabledReasons(view));
  let blockedDescription = $derived([
    ...blocked.map((code) => blockedLabel(code)),
    ...viewBlocked.map((item) => `${item.field}: ${item.code}`),
  ].join('; '));
  // Canonical server state may refuse a new ON before the request reaches
  // admission. Keep that fail-closed affordance separate from view-model
  // permit/target hints, which remain advisory and server-owned.
  let keyUnavailable = $derived(
    tx.fresh === false || tx.phase !== 'idle',
  );
  let pressed = $derived(tx.phase !== 'idle' && tx.phase !== 'failed');
  let known = $derived(view.txTarget.status === 'known');
  let receiver = $derived(view.txTarget.status === 'known' ? view.txTarget.receiver : undefined);
  let slot = $derived(view.txTarget.status === 'known'
    ? (view.txTarget.slot.kind === 'slotted' ? view.txTarget.slot.id : view.txTarget.slot.kind)
    : undefined);
  let frequencyHz = $derived(view.txTarget.status === 'known' ? view.txTarget.frequencyHz : null);
  let reason = $derived(view.txTarget.status === 'unknown' ? view.txTarget.reason : undefined);
  /** MOR-1474: the operator-legible unknown-target line, assembled through
   *  the per-reason catalog keys in `rx-tx-surface.ts` — never the raw
   *  `reason` enum word interpolated straight into prose. */
  let unknownTargetMessage = $derived(reason !== undefined ? targetUnknownMessage(reason) : '');

  /**
   * MOR-1275: the active design language's `stateFeedback` renderer.
   *
   * R9 — every field handed over is a CONCLUSION this surface already renders:
   * `rf`/`session` are `rfState()`/`txSessionState()` over the server
   * projection, `fault` is the snapshot's own code, and `keyBlocked` is
   * the very predicate that gates the key button below. No raw `ptt`, no store,
   * no new state path — and the descriptor comes back as annotations only, so
   * it cannot re-gate a control or rename one.
   */
  let stateFeedback = $derived(renderSlot('stateFeedback', {
    rf, session, fault: tx.fault, keyBlocked: blocked.length > 0,
  }));
</script>

<section
  class="rx-tx-surface" data-testid="rx-tx-surface" aria-label="Transmitter status and control"
  {...stateFeedback?.attributes ?? {}}
>
  <p
    class="rx-tx-state" role="status" data-testid="rx-tx-state"
    data-rf={rf} data-session={session} data-intent={tx.intent ?? undefined}
  >
    <span class="rx-tx-mark" data-testid="rx-tx-rf-mark" aria-hidden="true">{RF_MARK[rf]}</span>
    <span
      class="rx-tx-label v2-status-indicator" data-testid="rx-tx-rf-label"
      data-color={RF_BADGE[rf].color} data-active={RF_BADGE[rf].active}
    >{RF_LABEL[rf]}</span>
    <span class="rx-tx-session">{SESSION_LABEL[session]}</span>
    {#if tx.intent}<span class="rx-tx-intent">· {tx.intent}</span>{/if}
  </p>

  <!-- MOR-1792: `data-fault` stays the machine channel and gains
       `data-fault-legs` (the authority's own per-leg codes, space separated);
       the TEXT is the operator's sentence, never the bare enum word. -->
  {#if tx.fault}
    <p
      class="rx-tx-fault" data-testid="rx-tx-fault" data-fault={tx.fault}
      data-fault-legs={tx.faultDetail && tx.faultDetail.length > 0 ? tx.faultDetail.join(' ') : undefined}
    >{faultMessage(tx)}</p>
  {/if}

  {#if known}
    <p data-testid="rx-tx-target" data-target="known" data-receiver={receiver} data-slot={slot}>
      TX target: {receiver} {slot} · {frequencyHz ?? '—'} Hz
    </p>
  {:else}
    <p data-testid="rx-tx-target" data-target="unknown" data-reason={reason}>
      {unknownTargetMessage}
    </p>
  {/if}

  <div class="rx-tx-actions">
    <!-- The `v2-*` classes and `data-surface`/`data-indicator-*` attributes are
         the shared `control-button.css` vocabulary, applied to this existing
         button; they add no gate and change no handler. `data-active` restates
         `pressed`, the same value `aria-pressed` already carries. -->
    <button
      type="button" class="rx-tx-key v2-control-button v2-control-button--pill" data-testid="rx-tx-key"
      data-surface="hardware" data-indicator-style="dot" data-indicator-color="red" data-active={pressed}
      disabled={keyUnavailable} aria-pressed={pressed}
      aria-describedby={blockedDescription ? blockedId : undefined}
      onclick={onRequestKey}
    >Key transmitter</button>
    <!-- Never gated: no `disabled`, no `{#if}`, no guard in the handler. -->
    <button
      type="button" class="rx-tx-unkey v2-control-button v2-control-button--pill" data-testid="rx-tx-unkey"
      data-surface="hardware"
      onclick={onRequestUnkey}
    >Unkey transmitter</button>
  </div>

  {#if blockedDescription}<span id={blockedId} class="sr-only">{blockedDescription}</span>{/if}
  <ul class="rx-tx-blocked" data-testid="rx-tx-blocked">
    {#each visibleBlocked as code (code)}<li data-reason={code}>{blockedLabel(code)}</li>{/each}
    {#each viewBlocked as item (item.field + item.code)}
      <li data-reason={item.code} data-field={item.field}>{item.field}: {item.code}</li>
    {/each}
  </ul>
</section>

<style>
  /* Structure only — a design language owns colour and must never become the sole state channel. */
  .rx-tx-surface { display: flex; flex-direction: column; gap: 0.25rem; }
  .rx-tx-state { display: flex; align-items: baseline; gap: 0.4ch; margin: 0; }
  .rx-tx-mark { display: inline-block; min-inline-size: 1ch; }
  .rx-tx-label { min-inline-size: 3ch; font-weight: 700; letter-spacing: 0.08em; }
  .rx-tx-fault { margin: 0; font-weight: 700; }
  .rx-tx-actions { display: flex; flex-wrap: wrap; gap: 0.5rem; }
  .rx-tx-blocked { margin: 0; padding-inline-start: 1.2em; }
  .rx-tx-blocked:empty { display: none; }
  .sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
</style>
