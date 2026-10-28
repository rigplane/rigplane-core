<script lang="ts">
  import { onMount, tick } from 'svelte';
  import {
    Minimize2,
    Move,
    PanelBottom,
    PanelLeft,
    PanelRight,
  } from 'lucide-svelte';
  import {
    loadLocalExtensionManifest,
    type LocalExtensionDescriptor,
  } from './manifest';
  import {
    createDefaultLocalExtensionHostApi,
    installLocalExtensionHostApi,
    type LocalExtensionRegistration,
  } from './host-api';
  import {
    DEFAULT_LOCAL_EXTENSION_DOCK_MODE,
    getLocalExtensionDockMode,
    loadLocalExtensionDockLayout,
    saveLocalExtensionDockLayout,
    setLocalExtensionDockMode,
    type LocalExtensionDockMode,
    type LocalExtensionDockLayoutState,
  } from './dock-layout';

  let extensions = $state<LocalExtensionDescriptor[]>([]);
  let renderedExtensions = $state<LocalExtensionDescriptor[]>([]);
  let dockLayout = $state<LocalExtensionDockLayoutState>({
    version: 1,
    extensions: {},
  });
  const disposers = new Map<string, () => void>();
  const registrations = new Map<string, LocalExtensionRegistration>();
  const dockModes: LocalExtensionDockMode[] = [
    'floating',
    'dock-right',
    'dock-left',
    'dock-bottom',
    'collapsed',
  ];

  function escapeCssIdentifier(value: string): string {
    if (typeof CSS !== 'undefined' && typeof CSS.escape === 'function') {
      return CSS.escape(value);
    }
    return value.replace(/["\\]/g, '\\$&');
  }

  function disposeExtension(id: string): void {
    const dispose = disposers.get(id);
    if (dispose) {
      dispose();
      disposers.delete(id);
    }
  }

  function describeRegisteredExtensions(): LocalExtensionDescriptor[] {
    return extensions
      .filter((extension) => registrations.has(extension.id))
      .map((extension) => {
        const registration = registrations.get(extension.id);
        return {
          ...extension,
          title: registration?.title ?? extension.title,
        };
      });
  }

  async function renderExtension(extension: LocalExtensionRegistration): Promise<void> {
    await tick();
    if (modeFor(extension.id) === 'collapsed') {
      disposeExtension(extension.id);
      return;
    }

    const container = document.querySelector<HTMLElement>(
      `[data-local-extension-container="${escapeCssIdentifier(extension.id)}"]`,
    );
    if (!container) {
      return;
    }

    disposeExtension(extension.id);
    container.replaceChildren();
    const dispose = extension.render(container, api);
    if (typeof dispose === 'function') {
      disposers.set(extension.id, dispose);
    }
  }

  function registerExtension(extension: LocalExtensionRegistration): void {
    if (typeof extension.id !== 'string' || extension.id.trim() === '') {
      return;
    }

    if (typeof extension.render !== 'function') {
      return;
    }

    if (!extensions.some((candidate) => candidate.id === extension.id)) {
      return;
    }

    registrations.set(extension.id, extension);
    renderedExtensions = describeRegisteredExtensions();
    void renderExtension(extension);
  }

  function modeFor(id: string): LocalExtensionDockMode {
    return getLocalExtensionDockMode(dockLayout, id);
  }

  function extensionsForMode(mode: LocalExtensionDockMode): LocalExtensionDescriptor[] {
    return renderedExtensions.filter((extension) => modeFor(extension.id) === mode);
  }

  function setDockMode(id: string, mode: LocalExtensionDockMode): void {
    dockLayout = setLocalExtensionDockMode(dockLayout, id, mode);
    saveLocalExtensionDockLayout(dockLayout);
    if (mode === 'collapsed') {
      api.setKeyboardScope(null);
    }
    const registration = registrations.get(id);
    if (registration) {
      void renderExtension(registration);
    }
  }

  const api = createDefaultLocalExtensionHostApi(registerExtension);

  async function loadExtensionAssets(list: LocalExtensionDescriptor[]): Promise<void> {
    await tick();
    for (const extension of list) {
      if (extension.style) {
        const link = document.createElement('link');
        link.rel = 'stylesheet';
        link.href = extension.style;
        link.dataset.localExtensionId = extension.id;
        document.head.appendChild(link);
      }

      const script = document.createElement('script');
      script.src = extension.entry;
      script.async = true;
      script.dataset.localExtensionId = extension.id;
      document.body.appendChild(script);
    }
  }

  function unloadExtensionAssets(): void {
    for (const extension of extensions) {
      disposeExtension(extension.id);
    }
    document
      .querySelectorAll('[data-local-extension-id]')
      .forEach((node) => node.parentNode?.removeChild(node));
  }

  onMount(() => {
    let disposed = false;
    const uninstallApi = installLocalExtensionHostApi(window, api);
    dockLayout = loadLocalExtensionDockLayout();

    // Public host boundary: this component only mounts generic same-origin
    // extension URLs from the local manifest. Extension implementations stay
    // behind the local endpoint and are never imported into the open frontend.
    void loadLocalExtensionManifest().then((manifest) => {
      if (!disposed) {
        extensions = manifest?.extensions ?? [];
        void loadExtensionAssets(extensions);
      }
    });

    return () => {
      disposed = true;
      unloadExtensionAssets();
      extensions = [];
      renderedExtensions = [];
      registrations.clear();
      uninstallApi();
    };
  });
</script>

{#if renderedExtensions.length > 0}
  <aside class="local-extension-host" aria-label="Local extensions">
    {#each dockModes as dockMode}
      {@const dockedExtensions = extensionsForMode(dockMode)}
      {#if dockedExtensions.length > 0}
        <div class={`local-extension-stack local-extension-stack--${dockMode}`}>
          {#each dockedExtensions as extension (extension.id)}
            {@const currentMode = modeFor(extension.id)}
            {@const title = extension.title ?? 'Local extension'}
            <section
              class="local-extension-panel"
              class:local-extension-panel--collapsed={currentMode === 'collapsed'}
              data-local-extension-mount={extension.mount}
              data-local-extension-dock-mode={currentMode}
            >
              {#if currentMode === 'collapsed'}
                <button
                  class="local-extension-pill"
                  type="button"
                  aria-label={`Restore ${title}`}
                  title={`Restore ${title}`}
                  onclick={() => setDockMode(extension.id, DEFAULT_LOCAL_EXTENSION_DOCK_MODE)}
                >
                  <Move size={14} aria-hidden="true" />
                  <span>{title}</span>
                </button>
              {:else}
                <header class="local-extension-header">
                  <div class="local-extension-title">{title}</div>
                  <div class="local-extension-tools" aria-label={`${title} dock placement`}>
                    <button
                      type="button"
                      aria-label={`Float ${title}`}
                      aria-pressed={currentMode === 'floating'}
                      title="Float"
                      onclick={() => setDockMode(extension.id, 'floating')}
                    >
                      <Move size={14} aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      aria-label={`Dock ${title} left`}
                      aria-pressed={currentMode === 'dock-left'}
                      title="Dock left"
                      onclick={() => setDockMode(extension.id, 'dock-left')}
                    >
                      <PanelLeft size={14} aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      aria-label={`Dock ${title} right`}
                      aria-pressed={currentMode === 'dock-right'}
                      title="Dock right"
                      onclick={() => setDockMode(extension.id, 'dock-right')}
                    >
                      <PanelRight size={14} aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      aria-label={`Dock ${title} bottom`}
                      aria-pressed={currentMode === 'dock-bottom'}
                      title="Dock bottom"
                      onclick={() => setDockMode(extension.id, 'dock-bottom')}
                    >
                      <PanelBottom size={14} aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      aria-label={`Collapse ${title}`}
                      title="Collapse"
                      onclick={() => setDockMode(extension.id, 'collapsed')}
                    >
                      <Minimize2 size={14} aria-hidden="true" />
                    </button>
                  </div>
                </header>
              {/if}
              <div
                class="local-extension-content"
                class:local-extension-content--collapsed={currentMode === 'collapsed'}
                data-local-extension-container={extension.id}
              ></div>
            </section>
          {/each}
        </div>
      {/if}
    {/each}
  </aside>
{/if}

<style>
  .local-extension-host {
    position: fixed;
    inset: 0;
    z-index: 1250;
    pointer-events: none;
  }

  .local-extension-stack {
    position: fixed;
    display: flex;
    gap: 8px;
    pointer-events: none;
  }

  .local-extension-stack--floating {
    right: 12px;
    bottom: 12px;
    flex-direction: column;
    width: min(420px, calc(100vw - 24px));
    max-height: calc(100dvh - 24px);
  }

  .local-extension-stack--dock-right,
  .local-extension-stack--dock-left {
    top: 12px;
    bottom: 12px;
    flex-direction: column;
    width: min(440px, 34vw, calc(100vw - 24px));
  }

  .local-extension-stack--dock-right {
    right: 12px;
  }

  .local-extension-stack--dock-left {
    left: 12px;
  }

  .local-extension-stack--dock-bottom {
    right: 12px;
    bottom: 12px;
    left: 12px;
    flex-direction: row;
    align-items: flex-end;
    max-height: min(360px, 45dvh);
  }

  .local-extension-stack--collapsed {
    right: 12px;
    bottom: 12px;
    flex-flow: row-reverse wrap;
    max-width: calc(100vw - 24px);
  }

  .local-extension-panel {
    display: flex;
    min-height: 220px;
    max-height: min(560px, calc(100dvh - 24px));
    flex: 1 1 auto;
    flex-direction: column;
    overflow: hidden;
    border: 1px solid var(--v2-border-panel, var(--panel-border));
    border-radius: 4px;
    background: var(--v2-bg-card, var(--panel));
    box-shadow: var(--v2-shadow-md, 0 12px 30px rgba(0, 0, 0, 0.35));
    pointer-events: auto;
  }

  .local-extension-stack--dock-right .local-extension-panel,
  .local-extension-stack--dock-left .local-extension-panel {
    min-height: 0;
    max-height: none;
  }

  .local-extension-stack--dock-bottom .local-extension-panel {
    min-width: min(360px, calc(100vw - 24px));
    min-height: 180px;
    max-height: min(360px, 45dvh);
  }

  .local-extension-panel--collapsed {
    min-height: 0;
    border-radius: 999px;
    background: var(--v2-bg-elevated, var(--panel));
  }

  .local-extension-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    border-bottom: 1px solid var(--v2-border-panel, var(--panel-border));
  }

  .local-extension-title {
    min-width: 0;
    padding: 6px 8px;
    overflow: hidden;
    color: var(--v2-text-secondary, var(--text-muted));
    font: 600 11px/1.2 var(--v2-font-mono, var(--font-mono));
    text-overflow: ellipsis;
    text-transform: uppercase;
    white-space: nowrap;
  }

  .local-extension-tools {
    display: flex;
    flex: 0 0 auto;
    align-items: center;
    gap: 2px;
    padding: 4px;
  }

  .local-extension-tools button,
  .local-extension-pill {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    border: 1px solid transparent;
    color: var(--v2-text-secondary, var(--text-muted));
    background: transparent;
    cursor: pointer;
  }

  .local-extension-tools button {
    width: 28px;
    height: 28px;
    border-radius: 4px;
  }

  .local-extension-tools button:hover,
  .local-extension-tools button[aria-pressed='true'] {
    border-color: var(--v2-border-control, var(--panel-border));
    color: var(--v2-text-primary, var(--text));
    background: var(--v2-bg-control, rgba(255, 255, 255, 0.08));
  }

  .local-extension-pill {
    min-height: 32px;
    gap: 6px;
    padding: 0 10px;
    border-radius: 999px;
    border-color: var(--v2-border-panel, var(--panel-border));
    box-shadow: var(--v2-shadow-sm, 0 8px 18px rgba(0, 0, 0, 0.28));
    font: 600 11px/1.2 var(--v2-font-mono, var(--font-mono));
    text-transform: uppercase;
  }

  .local-extension-pill span {
    max-width: min(220px, calc(100vw - 96px));
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  .local-extension-content {
    display: block;
    width: 100%;
    min-height: 0;
    flex: 1 1 auto;
    overflow: auto;
    background: transparent;
  }

  .local-extension-content--collapsed {
    display: none;
  }

  @media (max-width: 760px) {
    .local-extension-stack--floating,
    .local-extension-stack--dock-right,
    .local-extension-stack--dock-left,
    .local-extension-stack--dock-bottom {
      right: 8px;
      bottom: 8px;
      left: 8px;
      top: auto;
      width: auto;
      max-height: min(52dvh, 420px);
      flex-direction: column;
    }

    .local-extension-stack--collapsed {
      right: 8px;
      bottom: 8px;
      left: 8px;
      justify-content: flex-end;
    }

    .local-extension-panel,
    .local-extension-stack--dock-bottom .local-extension-panel {
      min-width: 0;
      min-height: 180px;
      max-height: min(52dvh, 420px);
    }
  }
</style>
