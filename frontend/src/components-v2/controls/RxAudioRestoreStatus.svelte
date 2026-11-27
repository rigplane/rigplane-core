<script lang="ts">
  import { t } from '$lib/i18n';
  import type { AudioReceiveFailurePublic } from '$lib/types/state';

  let { failure, block = false }: {
    failure?: AudioReceiveFailurePublic | null;
    block?: boolean;
  } = $props();
</script>

{#if failure?.code === 'core_rx_restore_failed'}
  <span class:block role="status" data-testid="rx-audio-restore-failed">{t('core.statusbar.audio.restoreFailed')}</span>
{/if}

<style>
  .block {
    box-sizing: border-box;
    display: block;
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
