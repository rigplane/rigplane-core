<!--
  MOR-2241: persistent "radio disconnected, reconnecting…" banner.

  Shown while the server reports the rig link down (rigConnected=false,
  synced on every state_update — see radio.svelte.ts) and cleared on
  reconnect. Unlike a toast it never auto-dismisses: it is the persistent
  indicator the MOR-2241 toast suppression (Toast.svelte) points to.

  Visibility is gated on the current transport session: wsConnected must be
  up (a dropped control link is the StatusBar control-link-lost case, not
  this banner's) and the rig facts must have been observed on this session
  (hasObservedFactsThisSession — the MOR-1526 F2 honesty guard), so a cold
  start never flashes the banner before the first state_update arrives.

  Reads only the existing connection store — no second state channel.
-->
<script lang="ts">
  import { getRigConnected, getWsConnected, hasObservedFactsThisSession } from '$lib/stores/connection.svelte';
  import { t } from '$lib/i18n';

  let visible = $derived(
    getWsConnected() && hasObservedFactsThisSession() && !getRigConnected(),
  );
</script>

{#if visible}
  <div class="radio-link-banner" role="status" data-testid="radio-link-banner">
    {t('core.banner.radioDisconnected')}
  </div>
{/if}

<style>
  .radio-link-banner {
    position: fixed;
    top: var(--rp-chrome-top, 0px);
    left: 50%;
    transform: translateX(-50%);
    /* Below the power-off overlay (9000) and the global TX lamp (10001);
       AppGlobalHost suppresses this banner while the overlay is up. */
    z-index: 8990;
    padding: 4px 16px;
    border: 1px solid var(--warning, #f59e0b);
    border-top: none;
    border-radius: 0 0 6px 6px;
    font-family: var(--font-mono, 'Roboto Mono', monospace);
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 0.08em;
    color: var(--warning, #f59e0b);
    background: rgba(0, 0, 0, 0.72);
    pointer-events: none;
  }
</style>
