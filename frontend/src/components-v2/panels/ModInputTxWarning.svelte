<script lang="ts">
  /**
   * MOD-input TX preflight warning (MOR-617).
   *
   * Presentation-only banner over the runtime guard adapter: shows when a
   * network voice TX was keyed while the active DATA group's MOD input is
   * not LAN, with a one-click "Set LAN" fix. Clears reactively once the
   * source becomes LAN (readback) or the user dismisses it.
   */
  import { t } from '$lib/i18n';
  import {
    deriveModInputTxGuardProps,
    getModInputTxGuardHandlers,
  } from '$lib/runtime/adapters/mod-input-tx-guard.svelte';

  const handlers = getModInputTxGuardHandlers();
  let p = $derived(deriveModInputTxGuardProps());
</script>

{#if p.visible}
  <div class="mod-input-tx-warning" role="alert" data-testid="mod-input-tx-warning">
    <span class="warning-text">
      {t('core.txGuard.modInputNotLan', { source: p.sourceLabel ?? '?' })}
    </span>
    <div class="warning-actions">
      <button
        type="button"
        class="set-lan"
        data-testid="mod-input-set-lan"
        onclick={handlers.onSetLan}
      >
        {t('core.txGuard.setLan')}
      </button>
      <button
        type="button"
        class="dismiss"
        data-testid="mod-input-dismiss"
        aria-label={t('core.txGuard.dismiss')}
        title={t('core.txGuard.dismiss')}
        onclick={handlers.onDismiss}
      >
        ✕
      </button>
    </div>
  </div>
{/if}

<style>
  .mod-input-tx-warning {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 8px;
    border: 1px solid var(--v2-accent-orange, #f59e0b);
    border-radius: 4px;
    background: rgba(245, 158, 11, 0.12);
  }

  .warning-text {
    color: var(--v2-accent-orange, #f59e0b);
    font-size: 11px;
    line-height: 1.35;
  }

  .warning-actions {
    display: flex;
    align-items: center;
    gap: 6px;
  }

  .set-lan {
    flex: 1;
    padding: 4px 8px;
    border: 1px solid var(--v2-accent-orange, #f59e0b);
    border-radius: 4px;
    background: transparent;
    color: var(--v2-accent-orange, #f59e0b);
    font-family: 'Roboto Mono', monospace;
    font-size: 11px;
    font-weight: 700;
    letter-spacing: 0.06em;
    cursor: pointer;
    transition: all 0.15s;
  }

  .set-lan:hover {
    background: var(--v2-accent-orange, #f59e0b);
    color: #1a1a1a;
  }

  .dismiss {
    border: none;
    background: none;
    color: var(--v2-text-dim, #888);
    font-size: 12px;
    cursor: pointer;
    padding: 2px 4px;
  }

  .dismiss:hover {
    color: var(--v2-accent-orange, #f59e0b);
  }
</style>
