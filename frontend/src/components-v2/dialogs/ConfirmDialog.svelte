<script lang="ts">
  /**
   * Generic in-page confirmation dialog.
   *
   * Replaces the native `window.confirm`/`window.alert` calls the status
   * bar used for connection/power toggles. Observed in one RigPlane Pro
   * build: the native confirm did not appear and DISCONNECT did nothing,
   * while the same build worked in Chromium — so the page no longer
   * relies on native dialogs. Structure and styling tokens follow
   * `SendReportDialog.svelte`; no new design language.
   *
   * The parent owns the outcome: `onConfirm` runs the action (closing the
   * dialog on success is the parent's job), `onCancel` fires for the
   * Cancel button, Escape, and backdrop clicks. On failure the parent
   * keeps `open` true and passes `error` — the dialog then shows the
   * error and offers only Close. While `busy` is true the confirm button
   * is disabled and visibly working (SendReportDialog's pattern); Cancel
   * stays enabled — cancelling cannot un-dispatch an in-flight action,
   * the parent just discards its late result.
   */
  import { t } from '$lib/i18n';

  type Props = {
    open: boolean;
    message: string;
    confirmLabel: string;
    cancelLabel: string;
    onConfirm: () => void | Promise<void>;
    onCancel: () => void;
    error?: string | null;
    busy?: boolean;
  };

  let { open, message, confirmLabel, cancelLabel, onConfirm, onCancel, error = null, busy = false }: Props =
    $props();

  let modalRoot = $state<HTMLDivElement | null>(null);
  let cancelBtn = $state<HTMLButtonElement | null>(null);
  let closeBtn = $state<HTMLButtonElement | null>(null);

  // Focus contract: when the dialog opens, remember the control that had
  // focus and move focus to the safe default (Cancel). When it closes —
  // by confirm, cancel, Escape, backdrop, or unmount — hand focus back.
  $effect(() => {
    if (!open) return;
    const opener = (document.activeElement as HTMLElement | null) ?? null;
    cancelBtn?.focus();
    return () => {
      opener?.focus();
    };
  });

  // In the error state the only offered control is Close — move focus
  // onto it so keyboard users are not dropped onto <body> when the
  // confirm button disappears under them.
  $effect(() => {
    if (open && error) closeBtn?.focus();
  });

  // Escape = cancel; keep the Tab cycle inside the dialog (same trap as
  // SendReportDialog).
  function handleKeydown(ev: KeyboardEvent): void {
    if (!open) return;
    if (ev.key === 'Escape') {
      ev.preventDefault();
      onCancel();
      return;
    }
    if (ev.key !== 'Tab' || !modalRoot) return;
    const focusables = modalRoot.querySelectorAll<HTMLElement>(
      'button, [href], [tabindex]:not([tabindex="-1"])',
    );
    if (focusables.length === 0) return;
    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    const active = document.activeElement as HTMLElement | null;
    if (ev.shiftKey && active === first) {
      ev.preventDefault();
      last.focus();
    } else if (!ev.shiftKey && active === last) {
      ev.preventDefault();
      first.focus();
    }
  }

  function handleBackdropClick(ev: MouseEvent): void {
    if (ev.target === ev.currentTarget) onCancel();
  }
</script>

<svelte:window onkeydown={handleKeydown} />

{#if open}
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <!-- svelte-ignore a11y_no_static_element_interactions -->
  <div class="modal-backdrop" onclick={handleBackdropClick} data-testid="confirm-dialog-backdrop">
    <div
      class="modal"
      role="alertdialog"
      aria-modal="true"
      aria-labelledby="confirm-dialog-title"
      aria-describedby="confirm-dialog-message"
      bind:this={modalRoot}
    >
      <div class="modal-header">
        <h2 id="confirm-dialog-title">{t('common.dialog.confirmTitle')}</h2>
      </div>
      <div class="modal-body">
        <p class="message" id="confirm-dialog-message" data-testid="confirm-dialog-message">{message}</p>
        {#if error}
          <p class="error-detail" role="alert" data-testid="confirm-dialog-error">{error}</p>
        {/if}
      </div>
      {#if error}
        <div class="modal-footer">
          <button
            type="button"
            class="btn btn-primary"
            bind:this={closeBtn}
            onclick={onCancel}
            data-testid="confirm-dialog-close"
          >
            {t('common.action.close')}
          </button>
        </div>
      {:else}
        <div class="modal-footer">
          <button
            type="button"
            class="btn btn-secondary"
            bind:this={cancelBtn}
            onclick={onCancel}
            data-testid="confirm-dialog-cancel"
          >
            {cancelLabel}
          </button>
          <button
            type="button"
            class="btn btn-primary"
            onclick={() => void onConfirm()}
            disabled={busy}
            data-testid="confirm-dialog-confirm"
          >
            {busy ? t('common.action.working') : confirmLabel}
          </button>
        </div>
      {/if}
    </div>
  </div>
{/if}

<style>
  .modal-backdrop {
    position: fixed;
    inset: 0;
    z-index: 9000;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(0, 0, 0, 0.55);
    backdrop-filter: blur(3px);
    padding: 24px;
  }

  .modal {
    background: var(--v2-bg-primary, #0f0f1a);
    border: 1px solid var(--v2-border, #2a2a3e);
    border-radius: 6px;
    box-shadow: 0 16px 48px rgba(0, 0, 0, 0.65);
    width: 100%;
    max-width: 420px;
    display: flex;
    flex-direction: column;
    color: var(--v2-text-primary, #e0e0e0);
    font-family: 'Roboto Mono', monospace;
  }

  .modal-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 16px;
    border-bottom: 1px solid var(--v2-border-darker, #1a1a2e);
  }

  .modal-header h2 {
    margin: 0;
    font-size: 14px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--v2-text-primary, #e0e0e0);
  }

  .modal-body {
    padding: 16px;
    display: flex;
    flex-direction: column;
    gap: 12px;
  }

  .message {
    margin: 0;
    font-size: 12px;
    line-height: 1.5;
    color: var(--v2-text-primary, #e0e0e0);
  }

  .error-detail {
    margin: 0;
    font-size: 12px;
    line-height: 1.5;
    color: var(--v2-accent-red, #ef4444);
  }

  .modal-footer {
    display: flex;
    justify-content: flex-end;
    gap: 8px;
    padding: 12px 16px;
    border-top: 1px solid var(--v2-border-darker, #1a1a2e);
  }

  .btn {
    background: var(--v2-bg-input, #1a1a2e);
    border: 1px solid var(--v2-border, #2a2a3e);
    border-radius: 3px;
    color: var(--v2-text-primary, #e0e0e0);
    padding: 6px 12px;
    font-family: inherit;
    font-size: 11px;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    cursor: pointer;
    transition: all 0.15s ease;
  }

  .btn:hover:not(:disabled) {
    background: var(--v2-bg-card, #252540);
    border-color: var(--v2-accent-cyan, #06b6d4);
  }

  .btn:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  .btn-primary {
    background: var(--v2-accent-cyan, #06b6d4);
    border-color: var(--v2-accent-cyan, #06b6d4);
    color: #0a0a0f;
  }

  .btn-primary:hover:not(:disabled) {
    background: #22c5e3;
    border-color: #22c5e3;
    color: #0a0a0f;
  }

  .btn-secondary {
    background: var(--v2-bg-input, #1a1a2e);
    border-color: var(--v2-border, #2a2a3e);
    color: var(--v2-text-primary, #e0e0e0);
  }
</style>
