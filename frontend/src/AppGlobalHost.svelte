<!--
  App-global status host (MOR-1059).

  Owns the operator's global surfaces — feedback (toasts), power/health, and
  the authoritative TX/fault indication — at the App composition root, above
  the presentation boundary. Layouts and skins must not host these: a
  presentation swap replaces the layout subtree, and a global surface mounted
  inside it would be recreated, drop its subscription, or duplicate itself.

  See docs/plans/2026-07-25-ui-composition-architecture-v3.md
  ("render safety/status overlays outside the selected layout").
-->
<script lang="ts">
  import { onDestroy } from 'svelte';
  import Toast from './components/shared/Toast.svelte';
  import { runtime } from '$lib/runtime';
  import { getAppTxController } from '$lib/runtime/tx-controller/app-host';
  import { t } from '$lib/i18n';

  // The App-owned TX controller (MOR-1008/MOR-982) is the ONLY legitimate
  // source for this lamp. Radio-state PTT is a command/readback echo that can
  // read RX while the key is still down, so it is never consulted here.
  const tx = getAppTxController();
  let txState = $state.raw(tx.snapshot());
  const stopWatchingTx = tx.subscribe((next) => { txState = next; });
  onDestroy(() => stopWatchingTx());

  // Fail closed: 'uncertain' means the browser may own the key without a
  // confirmed readback, and must still be shown as a transmitting radio.
  let txIndication = $derived(
    txState.radioTx === 'on' || txState.txRisk === 'confirmed-on'
      ? 'on'
      : txState.txRisk === 'uncertain'
        ? 'uncertain'
        : null,
  );

  async function handlePowerOn(): Promise<void> {
    try {
      await runtime.system.powerOn();
    } catch (err) {
      alert(t('core.overlay.poweredOff.failedPowerOn', { detail: String(err) }));
    }
  }
</script>

<div class="app-global-host" data-testid="app-global-host">
  <Toast />

  {#if txIndication}
    <div class="global-tx" data-testid="global-tx-indication" data-tx={txIndication} aria-live="assertive">
      <span class="global-tx-lamp" aria-hidden="true"></span>
      <span>{txIndication === 'on' ? 'TX' : 'TX?'}</span>
    </div>
  {/if}

  {#if txState.fault}
    <div class="global-tx-fault" role="alert" data-testid="global-tx-fault" data-fault={txState.fault}>
      TX FAULT: {txState.fault}
    </div>
  {/if}

  {#if runtime.radioPowerOn === false}
    <div
      class="power-off-overlay"
      role="dialog"
      aria-modal="true"
      data-testid="global-power-off"
      aria-label={t('core.overlay.poweredOff.label')}
    >
      <div class="power-off-content">
        <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
          <path d="M18.36 6.64a9 9 0 1 1-12.73 0" />
          <line x1="12" y1="2" x2="12" y2="12" />
        </svg>
        <span class="power-off-label">{t('core.overlay.poweredOff.label')}</span>
        <button class="power-on-btn" onclick={handlePowerOn}>
          {t('core.overlay.poweredOff.powerOnButton')}
        </button>
      </div>
    </div>
  {/if}
</div>

<style>
  /* The wrapper is a grouping node only — it must not participate in any
     layout's box model, and must not create a containing block that would
     break `position: fixed` on its children. */
  .app-global-host {
    display: contents;
  }

  .global-tx,
  .global-tx-fault {
    position: fixed;
    left: 50%;
    transform: translateX(-50%);
    z-index: 10001;
    pointer-events: none;
    font-family: var(--font-mono, 'Roboto Mono', monospace);
    font-weight: 700;
    letter-spacing: 0.12em;
  }

  .global-tx {
    top: 0;
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 3px 12px;
    border: 1px solid var(--v2-accent-red, #ef4444);
    border-top: none;
    border-radius: 0 0 6px 6px;
    font-size: 12px;
    color: var(--v2-accent-red, #ef4444);
    background: rgba(0, 0, 0, 0.72);
  }

  .global-tx[data-tx='uncertain'] {
    border-color: var(--warning, #f59e0b);
    color: var(--warning, #f59e0b);
  }

  .global-tx-lamp {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    background: currentColor;
    box-shadow: 0 0 6px currentColor;
  }

  .global-tx-fault {
    top: 28px;
    padding: 3px 12px;
    border-radius: 4px;
    font-size: 11px;
    color: #fff;
    background: var(--danger, #b91c1c);
  }

  .power-off-overlay {
    position: fixed;
    inset: 0;
    z-index: 9000;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(0, 0, 0, 0.85);
    backdrop-filter: blur(6px);
  }

  .power-off-content {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 12px;
    color: var(--v2-text-dim, #888);
  }

  .power-off-label {
    font-family: var(--font-mono, 'Roboto Mono', monospace);
    font-size: 16px;
    font-weight: 700;
    letter-spacing: 0.1em;
    color: var(--v2-text-primary, #fff);
  }

  .power-on-btn {
    margin-top: 8px;
    padding: 10px 24px;
    font-family: var(--font-mono, 'Roboto Mono', monospace);
    font-size: 14px;
    font-weight: 700;
    letter-spacing: 0.08em;
    color: #fff;
    background: rgba(40, 160, 40, 0.25);
    border: 1.5px solid rgba(40, 160, 40, 0.6);
    border-radius: 6px;
    cursor: pointer;
    transition: background 0.15s, border-color 0.15s;
  }

  .power-on-btn:hover {
    background: rgba(40, 160, 40, 0.4);
    border-color: rgba(40, 160, 40, 0.8);
  }
</style>
