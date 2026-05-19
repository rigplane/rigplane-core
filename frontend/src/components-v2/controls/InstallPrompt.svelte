<script lang="ts">
  import { onMount } from 'svelte';
  import { X } from 'lucide-svelte';
  import {
    detectPlatform,
    isStandalone,
    isDismissed,
    setDismissed,
    getInstruction,
    hasInstallButton,
    type Platform,
  } from './install-prompt-utils';
  import { t } from '$lib/i18n';

  let visible = $state(false);
  let platform = $state<Platform>('desktop');
  let deferredPrompt = $state<any>(null);

  let instruction = $derived(getInstruction(platform, !!deferredPrompt));
  let showButton = $derived(hasInstallButton(platform, !!deferredPrompt));

  onMount(() => {
    if (isStandalone() || isDismissed()) return;

    platform = detectPlatform(navigator.userAgent);
    visible = true;

    const handler = (e: Event) => {
      e.preventDefault();
      deferredPrompt = e;
    };
    window.addEventListener('beforeinstallprompt', handler);
    return () => window.removeEventListener('beforeinstallprompt', handler);
  });

  function dismiss() {
    visible = false;
    setDismissed();
  }

  function installApp() {
    deferredPrompt?.prompt();
    dismiss();
  }
</script>

{#if visible}
  <div class="install-banner" class:slide-in={visible}>
    <span class="install-text">
      {#if showButton}
        <button type="button" class="install-btn" onclick={installApp}>{t('core.installPrompt.install')}</button>
      {:else}
        {instruction}
      {/if}
    </span>
    <button type="button" class="dismiss-btn" onclick={dismiss} aria-label={t('core.installPrompt.dismiss')}>
      <X size={16} />
    </button>
  </div>
{/if}

<style>
  .install-banner {
    display: flex;
    align-items: center;
    justify-content: space-between;
    height: 44px;
    padding: 0 12px;
    background: var(--v2-surface, #1a1a2e);
    border-bottom: 1px solid var(--v2-border, #333);
    font-family: 'Roboto Mono', monospace;
    font-size: 13px;
    color: var(--v2-text-secondary, #aab);
    animation: slide-down 0.25s ease-out;
  }

  .install-text {
    flex: 1;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  .install-btn {
    background: var(--v2-accent, #00bcd4);
    color: var(--v2-surface, #1a1a2e);
    border: none;
    border-radius: 4px;
    padding: 4px 14px;
    font-family: inherit;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
  }

  .install-btn:hover {
    opacity: 0.85;
  }

  .dismiss-btn {
    display: flex;
    align-items: center;
    justify-content: center;
    background: none;
    border: none;
    color: var(--v2-text-secondary, #aab);
    cursor: pointer;
    padding: 4px;
    margin-left: 8px;
    border-radius: 4px;
  }

  .dismiss-btn:hover {
    color: var(--v2-text-primary, #eee);
    background: var(--v2-surface-hover, rgba(255, 255, 255, 0.06));
  }

  @keyframes slide-down {
    from {
      transform: translateY(-100%);
      opacity: 0;
    }
    to {
      transform: translateY(0);
      opacity: 1;
    }
  }
</style>
