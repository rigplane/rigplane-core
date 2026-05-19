<script lang="ts">
  import { onMount } from 'svelte';
  import { fly } from 'svelte/transition';
  import { onMessage } from '../../lib/transport/ws-client';
  import { makeCommandId } from '../../lib/types/protocol';
  import { t, messageFromReasonCode, type MessageParams } from '$lib/i18n';

  interface ToastItem {
    id: string;
    level: 'info' | 'warning' | 'error';
    /**
     * Stable English fallback. The server is expected to emit `code` for
     * every new toast (RP-ML-005); `message` is preserved as a legacy field
     * for backward compatibility and for entries the frontend creates
     * locally without a reason code.
     */
    message: string;
    /** Optional reason code used for localized resolution. */
    code?: string;
    /** Optional named-placeholder substitutions for the localized message. */
    params?: MessageParams;
  }

  let toasts = $state<ToastItem[]>([]);

  function dismiss(id: string) {
    toasts = toasts.filter((t) => t.id !== id);
  }

  function addToast(
    level: 'info' | 'warning' | 'error',
    message: string,
    code?: string,
    params?: MessageParams,
  ) {
    const id = makeCommandId();
    toasts = [...toasts, { id, level, message, code, params }];
    setTimeout(() => dismiss(id), 5_000);
  }

  /**
   * Display copy for a toast. When the server supplied a `code`, resolve
   * `core.toast.<code>` via the i18n runtime (RP-ML-005 wire schema). When
   * no `code` is present, keep the legacy English `message` verbatim so
   * out-of-tree producers continue to work. `$derived` re-resolves on
   * locale changes.
   */
  function renderToast(toast: ToastItem): string {
    if (toast.code) {
      return messageFromReasonCode(toast.code, toast.params);
    }
    return toast.message;
  }

  onMount(() => {
    return onMessage((msg) => {
      if (msg.type === 'notification') {
        const lvl = msg.level === 'warning' || msg.level === 'error' ? msg.level : 'info';
        const code = typeof msg.code === 'string' ? msg.code : undefined;
        const params =
          msg.params && typeof msg.params === 'object'
            ? (msg.params as MessageParams)
            : undefined;
        addToast(
          lvl as 'info' | 'warning' | 'error',
          (msg.message as string) ?? '',
          code,
          params,
        );
      }
    });
  });
</script>

<div class="toast-container" aria-live="polite" aria-label={t('core.toast.notificationsLabel')}>
  {#each toasts as toast (toast.id)}
    <button
      class="toast"
      class:info={toast.level === 'info'}
      class:warning={toast.level === 'warning'}
      class:error={toast.level === 'error'}
      aria-label={t('core.toast.dismiss')}
      onclick={() => dismiss(toast.id)}
      in:fly={{ x: 80, duration: 200, opacity: 0 }}
      out:fly={{ x: 80, duration: 150, opacity: 0 }}
    >
      <span class="toast-icon" aria-hidden="true">
        {#if toast.level === 'error'}✕{:else if toast.level === 'warning'}⚠{:else}ℹ{/if}
      </span>
      <span class="toast-msg">{renderToast(toast)}</span>
      <span class="toast-close" aria-hidden="true">×</span>
    </button>
  {/each}
</div>

<style>
  .toast-container {
    position: fixed;
    top: var(--space-4);
    right: var(--space-4);
    z-index: 9999;
    display: flex;
    flex-direction: column;
    gap: var(--space-2);
    max-width: 360px;
    pointer-events: none;
  }

  .toast {
    /* button reset */
    appearance: none;
    -webkit-appearance: none;
    border: none;
    font-family: inherit;
    font-size: inherit;
    text-align: left;
    width: 100%;

    display: flex;
    align-items: flex-start;
    gap: var(--space-2);
    padding: var(--space-3);
    border-radius: var(--radius);
    border-left: 3px solid;
    background: var(--panel-gradient);
    box-shadow: 0 4px 16px rgba(0, 0, 0, 0.4);
    font-size: 0.8125rem;
    font-family: var(--font-sans);
    cursor: pointer;
    pointer-events: all;
    transition: opacity var(--transition-fast);
  }

  .toast:hover {
    opacity: 0.85;
  }

  .toast.info {
    border-color: var(--accent);
  }

  .toast.warning {
    border-color: var(--warning);
  }

  .toast.error {
    border-color: var(--danger);
  }

  .toast-icon {
    flex-shrink: 0;
    font-size: 0.75rem;
    margin-top: 1px;
  }

  .toast.info .toast-icon { color: var(--accent); }
  .toast.warning .toast-icon { color: var(--warning); }
  .toast.error .toast-icon { color: var(--danger); }

  .toast-msg {
    flex: 1;
    line-height: 1.4;
    word-break: break-word;
    color: var(--text);
  }

  .toast-close {
    flex-shrink: 0;
    color: var(--text-muted);
    font-size: 1rem;
    line-height: 1;
    cursor: pointer;
    padding: 0 2px;
    margin-left: var(--space-1);
  }

  .toast-close:hover {
    color: var(--text);
  }

</style>
