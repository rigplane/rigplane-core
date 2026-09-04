<!--
  Semantic RX/TX status and action surface (MOR-1064).

  Presentation only. It receives the MOR-1062 `RadioViewModel` and a snapshot
  of the server-owned TX projection as props, and emits TX intents as callbacks.
  It holds no TX state, keys nothing, and consults no controller — v3 ADR
  invariant 11. The authoritative global TX lamp stays in `AppGlobalHost`
  (MOR-1059); this surface is polite status, not a second alert.

  Colour is not a state channel here: every RF state carries distinct text and
  a distinct shape so it survives forced-colors (MOR-977). A design language
  may restyle any of this; it may not change which facts appear.
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
   *  Colour is a SECOND channel only: `RF_LABEL` and `RF_MARK` still carry the
   *  state as text and shape, which `rx-tx-surface.component.test.ts`'s
   *  'encodes RX/TX structurally, not by colour or class alone' case pins.
   *
   *  Every state is `active: true`. The base (non-active) `.v2-status-indicator`
   *  rule paints text in `--v2-badge-inactive-text` over a transparent
   *  background, which fails the fixture harness's 4.5:1
   *  `contrast-text-rx-tx-rf-label` check (`fixtures/assertions.ts`). */
  const RF_BADGE: Record<RfState, { color: 'green' | 'red' | 'amber' | 'muted'; active: boolean }> = {
    receiving: { color: 'green', active: true },
    transmitting: { color: 'red', active: true },
    uncertain: { color: 'amber', active: true },
    unknown: { color: 'muted', active: true },
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
  let viewBlocked = $derived(txDisabledReasons(view));
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
      disabled={tx.fresh === false} aria-pressed={pressed} aria-describedby={blockedId}
      onclick={onRequestKey}
    >Key transmitter</button>
    <!-- Never gated: no `disabled`, no `{#if}`, no guard in the handler. -->
    <button
      type="button" class="rx-tx-unkey v2-control-button v2-control-button--pill" data-testid="rx-tx-unkey"
      data-surface="hardware"
      onclick={onRequestUnkey}
    >Unkey transmitter</button>
  </div>

  <ul class="rx-tx-blocked" id={blockedId} data-testid="rx-tx-blocked">
    {#each blocked as code (code)}<li data-reason={code}>{blockedLabel(code)}</li>{/each}
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
