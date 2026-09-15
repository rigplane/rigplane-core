<script lang="ts">
  import { onDestroy } from 'svelte';
  import { getManagedAppTxController } from '$lib/runtime/tx-controller/managed-app-host';
  import ManagedTotControl from './ManagedTotControl.svelte';

  // This is deliberately a presentation consumer of the App-root facade. It
  // has no write path: the editor remains the existing full control.
  const tx = getManagedAppTxController();
  let txState = $state.raw(tx.snapshot());
  let open = $state(false);

  const stopWatching = tx.subscribe((next) => {
    txState = next;
  });
  onDestroy(stopWatching);

  function close(): void {
    open = false;
  }
</script>

<div class="managed-tot-status" data-testid="managed-tot-status">
  <button
    type="button"
    class="managed-tot-trigger"
    data-testid="managed-tot-trigger"
    aria-expanded={open}
    aria-haspopup="dialog"
    onclick={() => (open = !open)}
  >
    TOT {txState.fresh ? (txState.configuredSeconds === null ? 'OFF' : `${txState.configuredSeconds}s`) : '---'}
  </button>

  {#if open}
    <!-- The full editor needs vertical space; it must not be constrained by
         StatusBar's fixed 28px line. -->
    <div class="managed-tot-popover-backdrop" data-testid="managed-tot-popover-backdrop" onclick={close} role="presentation">
      <!-- svelte-ignore a11y_no_static_element_interactions -->
      <div
        class="managed-tot-popover"
        data-testid="managed-tot-popover"
        role="dialog"
        aria-modal="true"
        aria-label="Transmit timeout"
        tabindex="-1"
        onclick={(event) => event.stopPropagation()}
        onkeydown={(event) => { if (event.key === 'Escape') close(); }}
      >
        <div class="managed-tot-popover-header">
          <span>TRANSMIT TIMEOUT</span>
          <button type="button" class="managed-tot-close" data-testid="managed-tot-close" onclick={close} aria-label="Close transmit timeout editor">×</button>
        </div>
        <ManagedTotControl />
      </div>
    </div>
  {/if}
</div>

<style>
  .managed-tot-status {
    display: inline-flex;
    align-items: center;
    min-block-size: 20px;
  }

  .managed-tot-trigger {
    box-sizing: border-box;
    min-block-size: 20px;
    padding: 2px 6px;
    border: 1px solid var(--v2-border, #2a2a3e);
    border-radius: 3px;
    background: var(--v2-bg-input, #1a1a2e);
    color: var(--v2-text-primary, #fff);
    cursor: pointer;
    font: inherit;
    font-size: 9px;
    font-weight: 700;
    letter-spacing: .05em;
    line-height: 1;
    white-space: nowrap;
  }

  .managed-tot-trigger:hover,
  .managed-tot-trigger:focus-visible {
    border-color: var(--v2-accent-cyan, #06b6d4);
  }

  .managed-tot-popover-backdrop {
    position: fixed;
    inset: 0;
    z-index: 1000;
  }

  .managed-tot-popover {
    position: fixed;
    top: 36px;
    right: 10px;
    z-index: 1001;
    min-inline-size: 250px;
    padding: 10px 12px 12px;
    border: 1px solid var(--v2-border, #2a2a3e);
    border-radius: 5px;
    background: var(--v2-bg-primary, #0f0f1a);
    box-shadow: 0 12px 40px rgb(0 0 0 / 60%);
  }

  .managed-tot-popover-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    color: var(--v2-text-primary, #fff);
    font-size: 10px;
    font-weight: 700;
    letter-spacing: .06em;
  }

  .managed-tot-close {
    padding: 0 2px;
    border: 0;
    background: transparent;
    color: var(--v2-text-dim, #888);
    cursor: pointer;
    font: inherit;
    font-size: 16px;
    line-height: 1;
  }
</style>
