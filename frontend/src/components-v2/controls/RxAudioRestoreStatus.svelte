<script lang="ts">
  import { t } from '$lib/i18n';
  import { onDestroy } from 'svelte';
  import { getAudioRetryHandler } from '$lib/runtime/adapters/audio-retry-adapter';
  import type { AudioReceiveFailurePublic } from '$lib/types/state';

  let { failure, block = false }: {
    failure?: AudioReceiveFailurePublic | null;
    block?: boolean;
  } = $props();

  let pending = $state(false);
  const retryAudio = getAudioRetryHandler();
  let refusal = $state<'unsupported' | 'not_applicable' | 'failed' | null>(null);
  let request: AbortController | null = null;

  function cancelRequest(): void {
    const previous = request;
    request = null;
    previous?.abort();
  }

  $effect(() => {
    if (failure?.code !== 'core_rx_restore_failed') {
      cancelRequest();
      pending = false;
      refusal = null;
    }
  });
  onDestroy(cancelRequest);

  async function retry(): Promise<void> {
    if (pending) return;
    const current = new AbortController();
    request = current;
    pending = true;
    refusal = null;
    try {
      const result = await retryAudio(current.signal);
      if (request === current && !result.accepted) refusal = result.reason;
    } catch {
      if (request === current) refusal = 'failed';
    } finally {
      if (request === current) {
        request = null;
        pending = false;
      }
    }
  }
</script>

{#if failure?.code === 'core_rx_restore_failed'}
  <span class="restore-status" class:block role="status" data-testid="rx-audio-restore-failed">
    <span data-testid="rx-audio-restore-message">{t('core.statusbar.audio.restoreFailed')}</span>
    <button type="button" disabled={pending} onclick={retry}>{t('core.statusbar.audio.retry')}</button>
    {#if refusal === 'unsupported'}
      <span>{t('core.statusbar.audio.retryUnsupported')}</span>
    {:else if refusal === 'not_applicable'}
      <span>{t('core.statusbar.audio.retryNotApplicable')}</span>
    {:else if refusal === 'failed'}
      <span>{t('core.statusbar.audio.retryFailed')}</span>
    {/if}
  </span>
{/if}

<style>
  .restore-status {
    display: inline-flex;
    align-items: center;
    flex-wrap: wrap;
    gap: 4px 8px;
  }

  button {
    padding: 2px 8px;
    border: 1px solid currentColor;
    border-radius: 3px;
    background: transparent;
    color: inherit;
    font: inherit;
    cursor: pointer;
  }

  button:disabled {
    opacity: 0.6;
    cursor: wait;
  }

  .block {
    box-sizing: border-box;
    display: flex;
    justify-content: center;
    flex-shrink: 0;
    padding: 6px 10px;
    background: #7f1d1d;
    color: #fff;
    font-size: 16px;
    line-height: 1.3;
    text-align: center;
    overflow-wrap: anywhere;
  }
</style>
