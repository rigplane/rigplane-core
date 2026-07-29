<script lang="ts">
  import { onMount } from 'svelte';
  import { initBatteryMonitor } from './lib/utils/battery';
  import RadioLayoutV2 from './components-v2/layout/RadioLayout.svelte';
  import LocalExtensionsHost from './lib/local-extensions/LocalExtensionsHost.svelte';
  import { initMediaSession, destroyMediaSession } from './lib/media/media-session';
  import { runtime } from './lib/runtime/frontend-runtime';
  import { systemController } from '$lib/runtime/system-controller';
  import { provideAppTxControllerHost } from '$lib/runtime/tx-controller/app-host';
  import { hasAnyScope } from './lib/stores/capabilities.svelte';
  import { getLayoutMode } from './lib/stores/layout.svelte';
  import { resolveSkinId, type SkinId } from './skins/registry';
  import { t } from '$lib/i18n';
  import './app.css';

  let backendError = $state<string | null>(null);
  let retrying = $state(false);
  let retryCount = 0;
  let retryAttempt = $state(0);
  let retryDelaySec = $state(0);
  const MAX_RETRIES = 5;
  const RETRY_DELAYS = [3000, 5000, 10000, 20000, 30000];
  const demoMode = typeof window !== 'undefined'
    ? new URLSearchParams(window.location.search).get('demo')
    : null;
  let windowWidth = $state(typeof window !== 'undefined' ? window.innerWidth : 1200);
  let windowHeight = $state(typeof window !== 'undefined' ? window.innerHeight : 800);
  // Mobile = narrow portrait OR short landscape (touch device rotated)
  let isMobile = $derived(
    Math.min(windowWidth, windowHeight) < 640 ||
    ('ontouchstart' in globalThis && Math.min(windowWidth, windowHeight) < 500)
  );
  let skinId = $derived<SkinId>(resolveSkinId({
    capabilities: runtime.caps,
    layoutPreference: getLayoutMode(),
    isMobile,
    hasAnyScope: hasAnyScope(),
  }));

  const txHost = provideAppTxControllerHost({
    registerPreDisconnectBarrier: (barrier) =>
      systemController.registerPreDisconnectBarrier(barrier),
    lifecycleReleaseSource: (release) => {
      if (typeof window === 'undefined' || typeof document === 'undefined') {
        return () => {};
      }
      const onPageHide = () => release();
      const onVisibilityLoss = () => {
        if (document.visibilityState === 'hidden') release();
      };
      window.addEventListener('pagehide', onPageHide);
      document.addEventListener('visibilitychange', onVisibilityLoss);
      return () => {
        window.removeEventListener('pagehide', onPageHide);
        document.removeEventListener('visibilitychange', onVisibilityLoss);
      };
    },
  });
  let txAuthorityReady = $state(false);
  $effect(() => {
    const state = runtime.state, caps = runtime.caps;
    void JSON.stringify([
      state?.stateRevision, state?.freshnessRevision, state?.observationSeq, state?.ptt,
      state?.active, state?.main?.dataMode, state?.sub?.dataMode, state?.txTarget, state?.fieldStatus,
      caps?.tx, caps?.audioTx, caps?.audioTxRequiredModInputSource, caps?.capabilities, caps?.vfoScheme, caps?.txBands,
    ]);
    if (txAuthorityReady) txHost.refreshAuthority();
  });

  onMount(() => {
    if (demoMode === 'control-buttons') {
      return () => txHost.dispose();
    }

    // Stale-bookmark notice: ?ui=v1 is no longer supported (v0.20+). Emit once per session.
    if (typeof window !== 'undefined') {
      const uiParam = new URLSearchParams(window.location.search).get('ui');
      if (uiParam === 'v1') {
        console.warn('[rigplane] ?ui=v1 is no longer supported; v2 is the only UI. Update bookmarks.');
      }
    }

    initMediaSession();

    let cleanupBootstrap: (() => void) | null = null;
    let cleanupBattery: (() => void) | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let mounted = true;
    const handleResize = () => {
      windowWidth = window.innerWidth;
      windowHeight = window.innerHeight;
    };
    window.addEventListener('resize', handleResize);

    initBatteryMonitor((multiplier) => {
      runtime.setPollingMultiplier(multiplier);
    }).then(cleanup => { cleanupBattery = cleanup; });

    (async () => {
      try {
        cleanupBootstrap = await runtime.bootstrap();
        if (!mounted) return cleanupBootstrap();
        txAuthorityReady = true;
        backendError = null;
      } catch (err) {
        console.error('init error:', err);
        backendError = t('core.app.backendError', { detail: String(err) });
        if (retryCount < MAX_RETRIES) {
          const delay = RETRY_DELAYS[Math.min(retryCount, RETRY_DELAYS.length - 1)];
          retrying = true;
          retryAttempt = retryCount + 1;
          retryDelaySec = Math.round(delay / 1000);
          retryTimer = setTimeout(() => location.reload(), delay);
          retryCount++;
        } else {
          backendError = t('core.app.serverUnreachable');
          retrying = false;
        }
      }
    })();

    return () => {
      mounted = false;
      txAuthorityReady = false;
      txHost.dispose();
      destroyMediaSession();
      cleanupBattery?.();
      cleanupBootstrap?.();
      if (retryTimer) clearTimeout(retryTimer);
      window.removeEventListener('resize', handleResize);
    };
  });
</script>

{#if demoMode === 'control-buttons'}
  {#await import('./components-v2/controls/ControlButtonDemo.svelte') then mod}
    <mod.default />
  {/await}
{:else if backendError}
  <div class="error-overlay" role="alert" aria-live="assertive">
    <div class="error-box">
      <div class="error-icon">⚠</div>
      <p class="error-msg">{backendError}</p>
      {#if retrying}
        <div class="retry-indicator">
          <span class="spinner"></span>
          <span>{t('core.app.retryIndicator', { attempt: retryAttempt, max: MAX_RETRIES, seconds: retryDelaySec })}</span>
        </div>
      {/if}
    </div>
  </div>
{:else}
  <RadioLayoutV2 {skinId} />
{/if}

{#if demoMode !== 'control-buttons' && !backendError}
  <LocalExtensionsHost />
{/if}

<style>
  .error-overlay {
    position: fixed;
    inset: 0;
    z-index: 10000;
    display: flex;
    align-items: center;
    justify-content: center;
    background: rgba(11, 15, 20, 0.85);
    backdrop-filter: blur(4px);
  }

  .error-box {
    background: var(--panel);
    border: 1px solid var(--danger);
    border-radius: var(--radius-lg);
    padding: var(--space-6);
    max-width: 360px;
    width: 90%;
    text-align: center;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: var(--space-3);
  }

  .error-icon {
    font-size: 2rem;
    color: var(--warning);
  }

  .error-msg {
    color: var(--text);
    font-size: 0.9375rem;
    margin: 0;
    line-height: 1.5;
  }

  .retry-indicator {
    display: flex;
    align-items: center;
    gap: var(--space-2);
    color: var(--text-muted);
    font-size: 0.8125rem;
    font-family: var(--font-mono);
  }

  .spinner {
    width: 14px;
    height: 14px;
    border: 2px solid var(--panel-border);
    border-top-color: var(--accent);
    border-radius: 50%;
    animation: spin 0.8s linear infinite;
    flex-shrink: 0;
  }

  @keyframes spin {
    to { transform: rotate(360deg); }
  }
</style>
