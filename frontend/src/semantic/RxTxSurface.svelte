<!--
  Semantic RX/TX status and action surface (MOR-1064).

  Presentation only. It receives the MOR-1062 `RadioViewModel` and a snapshot
  of the server-owned TX projection as props, and emits TX intents as callbacks.
  It holds no TX state, keys nothing, and consults no controller — v3 ADR
  invariant 11. The authoritative global TX lamp stays in `AppGlobalHost`
  (MOR-1059); this surface is polite status, not a second alert.

  Confirmed and uncertain TX carry text and shape so they survive forced
  colors. Idle receiving and unknown states do not reserve an indicator row.
-->
<script lang="ts">
  import { onDestroy } from 'svelte';
  import '../components-v2/controls/control-button.css';
  import { t } from '$lib/i18n';
  import { renderSlot } from './design-language-renderers';
  import type { RadioViewModel } from './radio-view-model';
  import {
    RF_LABEL, RF_MARK, SESSION_LABEL, blockedLabel, faultMessage, keyBlockedReasons, nextSurfaceId,
    rfState, rfUnconfirmedLabel, targetUnknownMessage, txDisabledReasons, txSessionState,
    viewBlockedLabel,
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
    standard?: boolean;
    momentary?: boolean;
    onPttDown?: () => void;
    onPttUp?: () => void;
    onRequestKey: () => void;
    onRequestUnkey: () => void;
  }
  let { view, tx, standard = false, momentary = false, onPttDown = () => {}, onPttUp = () => {}, onRequestKey, onRequestUnkey }: Props = $props();

  const blockedId = nextSurfaceId();
  let rf = $derived(rfState(tx));
  let session = $derived(txSessionState(tx));
  let blocked = $derived(keyBlockedReasons(view, tx));
  let visibleBlocked = $derived(blocked.filter((code) => code !== 'rf-state-unknown'));
  let viewBlocked = $derived(txDisabledReasons(view));
  // MOR-2705: the accessible description is made of the same catalog
  // sentences as the visible list — a raw `field: code` pair never reaches
  // the operator, here or on screen.
  let blockedDescription = $derived([
    ...blocked.map((code) => blockedLabel(code)),
    ...viewBlocked.map((item) => viewBlockedLabel(item)),
  ].join('; '));
  // Canonical server state may refuse a new ON before the request reaches
  // admission. Keep that fail-closed affordance separate from view-model
  // permit/target hints, which remain advisory and server-owned.
  let keyUnavailable = $derived(
    tx.fresh === false || tx.phase !== 'idle',
  );
  // Input ownership only; displayed TX state remains the server projection.
  let held = $state(false);
  // Retain the matching release synchronously, independent of render batches.
  let releaseHeld: (() => void) | null = null;
  let alive = true;
  function beginHold(): void {
    if (!alive || !momentary || releaseHeld !== null || keyUnavailable) return;
    releaseHeld = onPttUp;
    held = true;
    onPttDown();
  }
  function endHold(): void {
    const release = releaseHeld;
    if (release === null) return;
    releaseHeld = null;
    held = false;
    release();
  }
  $effect(() => { if (!momentary || tx.fresh === false) endHold(); });
  onDestroy(() => { alive = false; endHold(); });
  let pressed = $derived(tx.phase !== 'idle' && tx.phase !== 'failed');
  let showTxState = $derived(
    tx.phase !== 'idle' || rf === 'transmitting' || rf === 'uncertain' || tx.fault !== null,
  );
  let known = $derived(view.txTarget.status === 'known');
  let receiver = $derived(view.txTarget.status === 'known' ? view.txTarget.receiver : undefined);
  /**
   * MOR-2705: only a SLOTTED target names its slot (`A`/`B`). `unslotted`
   * and `relative` are internal slot-kind words and never reach the
   * operator — the one-space separator before the middle dot lives in the
   * template's own literal text node, so a radio without slots reads
   * `TX target: MAIN · 14195000 Hz` and a slotted radio reads
   * `TX target: MAIN A · 7100000 Hz`. The `data-slot` machine attribute
   * mirrors this and goes absent for the internal kinds.
   */
  let slot = $derived(view.txTarget.status === 'known' && view.txTarget.slot.kind === 'slotted'
    ? view.txTarget.slot.id
    : undefined);
  /** The slot word including its leading space, '' plain — evaluated inside
   *  the expression so the Svelte compiler's block-edge whitespace rule
   *  (MOR-2711) cannot strip it. */
  let slotText = $derived(slot === undefined ? '' : ` ${slot}`);
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
  class="rx-tx-surface" class:standard data-testid="rx-tx-surface" aria-label="Transmitter status and control"
  {...stateFeedback?.attributes ?? {}}
>
  <p
    class="rx-tx-state" class:sr-only={standard}
    role="status" data-testid="rx-tx-state" hidden={!standard && !showTxState}
    data-rf={rf} data-session={session} data-intent={tx.intent ?? undefined}
  >
    <span class="rx-tx-mark" data-testid="rx-tx-rf-mark" aria-hidden="true">{RF_MARK[rf]}</span>
    <span
      class="rx-tx-label v2-status-indicator" data-testid="rx-tx-rf-label"
      data-color={RF_BADGE[rf].color} data-active={RF_BADGE[rf].active}
      aria-label={rfUnconfirmedLabel(rf) ?? undefined}
    >{standard && rf === 'receiving' ? 'RX' : RF_LABEL[rf]}</span>
    {#if session !== 'idle'}<span class="rx-tx-session">{SESSION_LABEL[session]}</span>{/if}
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
    <p class:sr-only={standard} data-testid="rx-tx-target" data-target="known"
      data-receiver={receiver} data-slot={slot}>
      TX target: {receiver}{slotText} · {frequencyHz !== null ? `${frequencyHz}\u00A0${t('core.filter.unit.hz')}` : ''}
    </p>
  {:else}
    <p class:sr-only={standard} data-testid="rx-tx-target" data-target="unknown" data-reason={reason}>
      {unknownTargetMessage}
    </p>
  {/if}

  <div class="rx-tx-actions">
    <!-- The `v2-*` classes and `data-surface`/`data-indicator-*` attributes are
         the shared `control-button.css` vocabulary, applied to this existing
         button; they add no gate and change no handler. `data-active` restates
         `pressed`, the same value `aria-pressed` already carries. -->
    {#if momentary}
    <button
      type="button" class="rx-tx-key v2-control-button v2-control-button--pill" data-testid="rx-tx-key"
      data-surface="hardware" data-indicator-style="dot" data-indicator-color="red" data-active={pressed}
      disabled={keyUnavailable && !held} aria-pressed={pressed}
      aria-describedby={blockedDescription ? blockedId : undefined}
      aria-label="PTT"
      onpointerdown={(event) => {
        if (event.button !== 0) return;
        event.preventDefault();
        event.currentTarget.setPointerCapture?.(event.pointerId);
        beginHold();
      }}
      onpointerup={endHold} onpointercancel={endHold} onlostpointercapture={endHold} onblur={endHold}
      onkeydown={(event) => {
        if (event.key !== ' ' && event.key !== 'Enter') return;
        event.preventDefault();
        if (!event.repeat) beginHold();
      }}
      onkeyup={(event) => {
        if (event.key !== ' ' && event.key !== 'Enter') return;
        event.preventDefault();
        endHold();
      }}
    >PTT</button>
    {:else}
    <button
      type="button" class="rx-tx-key v2-control-button v2-control-button--pill" data-testid="rx-tx-key"
      data-surface="hardware" data-indicator-style="dot" data-indicator-color="red" data-active={pressed}
      disabled={keyUnavailable} aria-pressed={pressed}
      aria-describedby={blockedDescription ? blockedId : undefined}
      onclick={onRequestKey}
      aria-label="Key transmitter"
    >{standard ? 'PTT' : 'Key transmitter'}</button>
    {/if}
    <!-- Never gated: no `disabled`, no `{#if}`, no guard in the handler. -->
    <button
      type="button" class="rx-tx-unkey v2-control-button v2-control-button--pill" data-testid="rx-tx-unkey"
      data-surface="hardware"
      aria-label="Unkey transmitter"
      onclick={onRequestUnkey}
    >{standard ? 'UNKEY' : 'Unkey transmitter'}</button>
  </div>

  {#if blockedDescription}<span id={blockedId} class="sr-only">{blockedDescription}</span>{/if}
  <ul class="rx-tx-blocked" class:sr-only={standard} data-testid="rx-tx-blocked">
    {#each visibleBlocked as code (code)}<li data-reason={code}>{blockedLabel(code)}</li>{/each}
    {#each viewBlocked as item (item.field + item.code)}
      <li data-reason={item.code} data-field={item.field}>{viewBlockedLabel(item)}</li>
    {/each}
  </ul>
</section>

<style>
  /* Structure only — a design language owns colour and must never become the sole state channel. */
  .rx-tx-surface { display: flex; flex-direction: column; gap: 0.25rem; }
  .rx-tx-state { display: flex; align-items: baseline; gap: 0.4ch; margin: 0; }
  .rx-tx-state[hidden] { display: none !important; }
  .rx-tx-mark { display: inline-block; min-inline-size: 1ch; }
  .rx-tx-label { min-inline-size: 3ch; font-weight: 700; letter-spacing: 0.08em; }
  .rx-tx-fault { margin: 0; font-weight: 700; }
  .rx-tx-actions { display: flex; flex-wrap: wrap; gap: 0.5rem; }
  .rx-tx-surface.standard { gap: 8px; }
  .rx-tx-surface.standard .rx-tx-state {
    justify-content: center; padding: 6px; border: 1px solid var(--v2-border);
    border-radius: 3px;
  }
  .rx-tx-surface.standard .rx-tx-actions { display: grid; grid-template-columns: 1fr; gap: 6px; }
  /* Standard keeps the authority status available to assistive technology,
     while the PTT control below is the panel's only visible keyed indicator. */
  :global(.desktop-control-face.standard-face) .rx-tx-surface.standard .rx-tx-state.sr-only {
    padding: 0 !important;
    border: 0 !important;
    border-radius: 0 !important;
    background: none !important;
  }
  :global(.desktop-control-face.standard-face) .rx-tx-surface.standard .rx-tx-actions .rx-tx-key.v2-control-button {
    width: 100%; min-height: 78px; border: 2px solid var(--v2-accent-red, #ef4444);
    color: var(--v2-accent-red, #ef4444); font-size: 1.5rem; font-weight: 700;
    letter-spacing: 0.1em;
  }
  :global(.desktop-control-face.standard-face) .rx-tx-surface.standard .rx-tx-actions .rx-tx-key.v2-control-button[data-active='true'] {
    opacity: 1;
    color: #fff;
    border-color: #ff5a68;
    background: color-mix(in srgb, var(--v2-accent-red, #ef4444) 76%, #24070c);
    box-shadow:
      inset 0 0 0 1px color-mix(in srgb, #fff 24%, transparent),
      0 0 18px color-mix(in srgb, var(--v2-accent-red, #ef4444) 78%, transparent);
    text-shadow: 0 1px 2px #520710;
  }
  :global(.desktop-control-face.standard-face) .rx-tx-surface.standard .rx-tx-state .rx-tx-label.v2-status-indicator {
    padding: 0; border: 0; background: none; box-shadow: none;
  }
  .rx-tx-surface.standard .rx-tx-unkey { width: 100%; }
  .rx-tx-blocked { margin: 0; padding-inline-start: 1.2em; }
  .rx-tx-blocked:empty { display: none; }
  .sr-only { position: absolute; width: 1px; height: 1px; padding: 0; margin: -1px; overflow: hidden; clip: rect(0, 0, 0, 0); white-space: nowrap; border: 0; }
</style>
