<script lang="ts">
  import { onMount, tick } from 'svelte';
  import AppGlobalHost from './AppGlobalHost.svelte';
  import LocalExtensionsHost from './lib/local-extensions/LocalExtensionsHost.svelte';
  import { initMediaSession, destroyMediaSession } from './lib/media/media-session';
  import { presentationResources, runtime } from './lib/runtime/frontend-runtime';
  import type { AppResource, ResourceLease } from '$lib/runtime/resource-demand';
  import { systemController } from '$lib/runtime/system-controller';
  import { provideManagedAppTxHost } from '$lib/runtime/tx-controller/managed-app-host';
  import { hasAnyScope } from './lib/stores/capabilities.svelte';
  import { getLayoutMode } from './lib/stores/layout.svelte';
  import { readQaCockpitLayoutOverride } from './lib/stores/qa-cockpit-override';
  import { getAvailableThemes } from './components-v2/theme/theme-switcher';
  import { getDesignLanguage, listDesignLanguageIds } from './presentation/languages/contract';
  // Side-effect import: populates the design-language registry the lookup
  // above resolves against, exactly as `semantic/design-language-renderers.ts`
  // does. Imported here too so the activation effect below cannot depend on a
  // lazily-loaded skin having pulled the barrel in first.
  import './presentation/languages/declarations';
  // Every scoped language stylesheet ships with the production composition
  // root. They stay inert until the canonical activation attribute below is
  // present; loading them here prevents the fixture harness from being their
  // only build path.
  import './presentation/languages/studioline/studioline.css';
  import './presentation/languages/fieldline/fieldline.css';
  import './presentation/languages/segmentline/segmentline.css';
  import { getLayout } from './presentation/layouts/contract';
  // MOR-1082, the layout half of the same idiom: a side-effect import that
  // populates the LAYOUT registry, so `getLayout(skinId)` below can resolve
  // the active layout's declared zones. Manifests stay declarations — the
  // surface plan reads `zones` and `requiredSemanticSurfaces`, nothing else.
  import './presentation/layouts/declarations';
  import { designLanguageActivation } from './presentation/workspace/activation';
  import {
    densityActivation, provideSurfacePlan, resolveSurfacePlan,
  } from './presentation/workspace/resolution';
  import { getWorkspace, initWorkspaceStore } from './presentation/workspace/store.svelte';
  import SemanticRadioSurfaces, {
    type ExternalPresentation as ExternalPresentationInput,
  } from './components-v2/wiring/SemanticRadioSurfaces.svelte';
  import { getSelectedPresentationId } from './component-kits/activation';
  import {
    getPresentationRecord, loadSkin, resolveSkinId,
    type InstrumentHandlesPresentation, type PresentationComponent,
    type PresentationId, type SelfContainedPresentation, type SkinId,
  } from './skins/registry';
  import { t } from '$lib/i18n';
  import './app.css';

  // MOR-1081: the workspace store owns layout, design-language and theme
  // selection. Initialised here — the composition root, and the first thing
  // in it — so it is loaded before the first selection read below
  // (`getLayoutMode()`) and before any skin, theme or surface mounts.
  initWorkspaceStore();

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
  // MOR-1257: interim QA-only reachability for the dual-receiver cockpit —
  // resolved once at load, exactly like `demoMode` above. See
  // `lib/stores/qa-cockpit-override.ts`.
  const qaCockpitLayoutOverride = readQaCockpitLayoutOverride();
  let windowWidth = $state(typeof window !== 'undefined' ? window.innerWidth : 1200);
  let windowHeight = $state(typeof window !== 'undefined' ? window.innerHeight : 800);
  // Mobile = narrow portrait OR short landscape (touch device rotated)
  let isMobile = $derived(
    Math.min(windowWidth, windowHeight) < 640 ||
    ('ontouchstart' in globalThis && Math.min(windowWidth, windowHeight) < 500)
  );
  let skinId = $derived<SkinId>(resolveSkinId({
    capabilities: runtime.caps,
    layoutPreference: qaCockpitLayoutOverride ?? getLayoutMode(),
    isMobile,
    hasAnyScope: hasAnyScope(),
  }));

  type CommittedPresentation =
    | { id: SkinId; layoutId: SkinId; component: InstrumentHandlesPresentation; hostMode: 'instrument-handles' }
    | { id: SkinId; layoutId: SkinId; component: SelfContainedPresentation; hostMode: 'self-contained' }
    | {
        id: PresentationId;
        layoutId: string;
        hostMode: 'external-instruments-v1';
        externalPresentation: ExternalPresentationInput;
      };
  let presentation = $state<CommittedPresentation | null>(null);
  let committedLayoutId = $derived<string | null>(presentation?.layoutId ?? null);

  // MOR-1081: the workspace's `designLanguage` is the ONLY source the
  // `[data-design-language]` activation attribute (MOR-1278) is written from,
  // and this is its only writer — the MOR-1275 renderer wiring and the
  // language stylesheets both read that same attribute, so there is no second
  // activation path. `designLanguageActivation` gates on the language's own
  // manifest, which is what keeps the shipped v2 skins unchanged until the
  // cutover (MOR-1048/MOR-1263) declares them compatible.
  //
  // MOR-1082 rides the SAME effect and the same gate rather than adding a
  // second one: `[data-density]` carries the resolved density (the workspace
  // override clamped by the ACTIVE language's own DensityClamp) on the same
  // semantic-vertical root, so the two attributes can never disagree about
  // which language is in force. No stylesheet consumes it yet — density
  // rules arrive with the cutover — and it is absent entirely wherever the
  // language is not active, so no shipped v2 skin sees a new attribute.
  $effect(() => {
    const layoutId = committedLayoutId;
    if (layoutId === null) {
      delete document.documentElement.dataset.designLanguage;
      delete document.documentElement.dataset.languageMode;
      delete document.documentElement.dataset.density;
      return;
    }
    const stored = getDesignLanguage(getWorkspace().designLanguage);
    let language = stored;
    let activated = designLanguageActivation(stored, layoutId);
    if (activated === null) {
      // The stored preference cannot activate on the resolved skin
      // (e.g. `segmentline` off `peer-split`, or the default `studioline` ON
      // `peer-split`, which no v2 skin's default ever declared compatible).
      // Falls back to the FIRST registered language whose `layoutCompatibility`
      // declares this skin, in `presentation/languages/declarations.ts`
      // registration order (studioline, fieldline, segmentline). The STORED
      // preference itself is untouched here — only this render's activation
      // falls back, so leaving `peer-split` restores it without a re-choice.
      for (const id of listDesignLanguageIds()) {
        const candidate = getDesignLanguage(id);
        const candidateActivated = designLanguageActivation(candidate, layoutId);
        if (candidateActivated !== null) {
          language = candidate;
          activated = candidateActivated;
          break;
        }
      }
    }
    if (activated === null) {
      delete document.documentElement.dataset.designLanguage;
      delete document.documentElement.dataset.languageMode;
    } else {
      document.documentElement.dataset.designLanguage = activated;
      const theme = getAvailableThemes().find(({ id }) => id === getWorkspace().theme);
      document.documentElement.dataset.languageMode = theme?.category === 'light' ? 'light' : 'dark';
    }
    const density = densityActivation(language, layoutId, getWorkspace().density);
    if (density === null) delete document.documentElement.dataset.density;
    else document.documentElement.dataset.density = density;
  });

  // MOR-1082 — the workspace's per-zone `visibleSurfaces`/`zoneOrder`, resolved
  // against the ACTIVE layout manifest and handed down as a getter.
  // A getter, so a consumer's `$derived` re-runs when either input changes.
  provideSurfacePlan(() => {
    const layoutId = committedLayoutId;
    if (layoutId === null) return null;
    const manifest = getLayout(layoutId);
    return manifest === undefined ? null : resolveSurfacePlan(manifest, getWorkspace());
  });

  // ── Lazy presentation loading (MOR-1060) ──
  //
  // App is the sole owner of presentation selection, loading and commit. Each
  // request takes a fresh loader generation — including A → B → A — so exactly
  // one resolution can commit: any completion whose generation is no longer
  // current is discarded without mounting, without writing state and without
  // touching the resources of the presentation that is actually on screen.
  //
  // The committed presentation stays mounted for the whole time the next
  // loader is in flight, so a switch never blanks the operator's screen and
  // never replays bootstrap, transport, audio or TX ownership — all of which
  // live above this seam (MOR-973, MOR-1008, MOR-1059).
  let presentationFailed = $state(false);
  let HostedPresentation = $derived(
    presentation?.hostMode === 'instrument-handles' ? presentation.component : null,
  );
  let SelfContainedComponent = $derived(
    presentation?.hostMode === 'self-contained' ? presentation.component : null,
  );
  let ExternalPresentation = $derived(
    presentation?.hostMode === 'external-instruments-v1'
      ? presentation.externalPresentation
      : null,
  );
  let loaderGeneration = 0;
  let requestedPresentationId: PresentationId | null = null;
  /** Cleared by App teardown; a resolution that lands afterwards is inert. */
  let presentationActive = true;

  $effect(() => {
    const builtInFallback = skinId;
    const requested = getSelectedPresentationId() ?? builtInFallback;
    if (demoMode === 'control-buttons') return;
    if (requested === requestedPresentationId) return;
    requestedPresentationId = requested;
    void requestPresentation(requested);
  });

  async function requestPresentation(id: PresentationId): Promise<void> {
    const generation = ++loaderGeneration;
    let bridge: ResourceLease[] = [];
    try {
      const record = getPresentationRecord(id);
      if (record === undefined) throw new Error(`Presentation "${id}" is not registered.`);
      if (record.kind === 'external-instruments-v1') {
        const component = await loadSkin(record);
        if (!presentationActive || generation !== loaderGeneration) return;
        bridge = acquireSwapBridge(id, record.resources);
        let occurrence: ExternalPresentationInput;
        occurrence = Object.freeze({
          record,
          component,
          isCurrent: () => presentationActive
            && presentation?.hostMode === 'external-instruments-v1'
            && presentation.externalPresentation === occurrence,
        });
        presentation = Object.freeze({
          id: record.id,
          layoutId: record.layoutId,
          hostMode: record.kind,
          externalPresentation: occurrence,
        });
      } else {
        const loaded: PresentationComponent = await loadSkin(id);
        if (!presentationActive || generation !== loaderGeneration) return;
        bridge = acquireSwapBridge(id, record.resources);
        presentation = record.kind === 'built-in-instrument-layout'
          ? { id: record.id, layoutId: record.id, component: loaded as InstrumentHandlesPresentation, hostMode: 'instrument-handles' }
          : { id: record.id, layoutId: record.id, component: loaded as SelfContainedPresentation, hostMode: 'self-contained' };
      }
      presentationFailed = false;
      await tick();
    } catch (err) {
      // A stale or post-teardown failure is inert: only the newest request
      // owns the error surface (same guard doctrine as the bootstrap catch).
      if (!presentationActive || generation !== loaderGeneration) return;
      console.error(`[rigplane] presentation "${id}" failed to load:`, err);
      // Last-known-good survives a failed switch; only an initial load has
      // nothing to keep, and then the surface is inert — no retry, no
      // runtime teardown.
      presentationFailed = presentation === null;
      return;
    } finally {
      releaseSwapBridge(bridge);
    }
  }

  /**
   * Bridge leases for the incoming presentation's planned resources, limited
   * to those already demanded: a presentation choice must not manufacture a
   * live service (v3 ADR invariant 12).
   */
  function acquireSwapBridge(id: PresentationId, resources: readonly AppResource[]): ResourceLease[] {
    const leases: ResourceLease[] = [];
    for (const resource of resources) {
      try {
        if (presentationResources.snapshot(resource).demand <= 0) continue;
        // Every acquisition returns its own distinct lease object, so a
        // release can only ever cancel the binding it created.
        leases.push(presentationResources.acquire(resource, `App:${id}`));
      } catch {
        // The App resource session is torn down — there is nothing to bridge.
      }
    }
    return leases;
  }

  function releaseSwapBridge(leases: ResourceLease[]): void {
    while (leases.length > 0) presentationResources.release(leases.pop()!);
  }

  const txHost = provideManagedAppTxHost({
    registerPreDisconnectBarrier: (barrier) =>
      systemController.registerPreDisconnectBarrier(barrier),
    lifecycleReleaseSource: (release) => {
      if (typeof window === 'undefined' || typeof document === 'undefined') {
        return () => {};
      }
      const onPageHide = () => release();
      const onBlur = () => release();
      const onVisibilityLoss = () => {
        if (document.visibilityState === 'hidden') release();
      };
      window.addEventListener('pagehide', onPageHide);
      window.addEventListener('blur', onBlur);
      document.addEventListener('visibilitychange', onVisibilityLoss);
      return () => {
        window.removeEventListener('pagehide', onPageHide);
        window.removeEventListener('blur', onBlur);
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
      return () => {
        presentationActive = false;
        txHost.dispose();
      };
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
    let retryTimer: ReturnType<typeof setTimeout> | null = null;
    let mounted = true;
    const bootstrapAbort = new AbortController();
    const handleResize = () => {
      windowWidth = window.innerWidth;
      windowHeight = window.innerHeight;
    };
    window.addEventListener('resize', handleResize);

    (async () => {
      try {
        cleanupBootstrap = await runtime.bootstrap(bootstrapAbort.signal);
        if (!mounted) return cleanupBootstrap();
        txAuthorityReady = true;
        backendError = null;
      } catch (err) {
        console.error('init error:', err);
        if (!mounted) return;
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
      bootstrapAbort.abort();
      presentationActive = false;
      txAuthorityReady = false;
      txHost.dispose();
      destroyMediaSession();
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
{:else if HostedPresentation || ExternalPresentation}
  <!-- One unkeyed host outlives replacement of either hosted presentation.
       The child places radio instruments; SRS retains bindings and authority. -->
  <SemanticRadioSurfaces externalPresentation={ExternalPresentation}>
    {#snippet children(instruments)}
      {#if HostedPresentation}<HostedPresentation {instruments} />{/if}
    {/snippet}
  </SemanticRadioSurfaces>
{:else if SelfContainedComponent}
  <SelfContainedComponent />
{:else if presentationFailed}
  <!-- Initial presentation load failed: an inert App-owned surface. No retry
       and no runtime teardown — control transport, audio and TX authority all
       live above this boundary and stay up. -->
  <div class="error-overlay" role="alert" aria-live="assertive">
    <div class="error-box">
      <div class="error-icon">⚠</div>
      <p class="error-msg" data-testid="presentation-load-error">{t('core.app.presentationError')}</p>
    </div>
  </div>
{/if}

{#if demoMode !== 'control-buttons' && !backendError}
  <!-- Global feedback / power-health / authoritative TX indication live here,
       as siblings of the presentation, so switching layout or skin never
       recreates or duplicates them (MOR-1059). -->
  <AppGlobalHost />
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
