<script lang="ts">
  import { DEFAULT_TAB, getVisibleTabs, type TabId } from './mobile-nav-utils';
  import { hasTx } from '$lib/stores/capabilities.svelte';
  import { t } from '$lib/i18n';

  let { activeTab = $bindable(DEFAULT_TAB) }: { activeTab?: TabId } = $props();

  let visibleTabs = $derived(getVisibleTabs({ hasTx: hasTx() }));

  /**
   * Resolve a tab label through the i18n catalog. The English label in
   * `mobile-nav-utils.ts` remains the authoritative source string (it is also
   * what the catalog values mirror); we just thread it through `t()` so the
   * label re-renders on locale change. Note: `VFO` and `TX` are
   * glossary-stable per strategy glossary §2.B, and the catalog values
   * preserve them verbatim — this is enforced by the RP-ML-013A lint.
   */
  function tabLabel(id: TabId): string {
    switch (id) {
      case 'vfo':      return t('core.mobile.nav.tab.vfo');
      case 'spectrum': return t('core.mobile.nav.tab.spectrum');
      case 'controls': return t('core.mobile.nav.tab.controls');
      case 'tx':       return t('core.mobile.nav.tab.tx');
      case 'meters':   return t('core.mobile.nav.tab.meters');
    }
  }
</script>

<nav class="mobile-nav" aria-label={t('core.mobile.nav.label')}>
  {#each visibleTabs as tab (tab.id)}
    <button
      aria-current={activeTab === tab.id ? 'page' : undefined}
      class="nav-tab"
      class:active={activeTab === tab.id}
      onclick={() => { activeTab = tab.id; }}
    >
      <span class="tab-icon" aria-hidden="true">{tab.icon}</span>
      <span class="tab-label">{tabLabel(tab.id)}</span>
    </button>
  {/each}
</nav>

<style>
  .mobile-nav {
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    display: flex;
    align-items: stretch;
    height: 56px;
    background: var(--v2-bg-card, var(--v2-bg-darker));
    border-top: 1px solid var(--v2-border, var(--v2-border-dark));
    z-index: 100;
  }

  .nav-tab {
    flex: 1;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 2px;
    background: none;
    border: none;
    border-bottom: 2px solid transparent;
    color: var(--v2-text-muted, var(--v2-text-disabled));
    cursor: pointer;
    padding: 0;
    font-family: var(--v2-font-mono, 'Roboto Mono', monospace);
    transition: color 0.15s, border-color 0.15s;
  }

  .nav-tab:hover {
    color: var(--v2-text-secondary, var(--v2-text-subdued));
  }

  .nav-tab.active {
    color: var(--v2-accent-cyan, var(--v2-accent-cyan));
    border-bottom-color: var(--v2-accent-cyan, var(--v2-accent-cyan));
  }

  .tab-icon {
    font-size: 16px;
    line-height: 1;
  }

  .tab-label {
    font-size: var(--v2-font-size-sm, 9px);
    font-weight: 700;
    letter-spacing: 0.5px;
    text-transform: uppercase;
  }
</style>
