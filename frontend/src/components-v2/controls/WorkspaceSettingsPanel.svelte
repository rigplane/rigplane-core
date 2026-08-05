<script lang="ts">
  /**
   * MOR-1080 — workspace selection UI: layout / design language / theme,
   * the discard/repair notice, and a recoverable reset. Import/export is the
   * sibling `WorkspaceImportExport.svelte`, split out to keep this file
   * focused (design constraint: split import/export from selection).
   *
   * Enumerates ONLY the ids the workspace validator itself pins
   * (`WORKSPACE_LAYOUT_IDS` / `WORKSPACE_DESIGN_LANGUAGE_IDS` /
   * `WORKSPACE_THEME_IDS` from `contract.ts`, the MOR-1077 precedent) — never
   * a hand-rolled list, so a selectable id can never drift from what the
   * store accepts. Theme writes route through `theme-switcher.ts` (not the
   * store directly) so the DOM `data-theme` attribute stays in sync, exactly
   * like the existing `ThemePicker`.
   *
   * Reset is the safe override binding carry-forward 1 calls for: the ONLY
   * override offered for a latched forward-read-only notice is a full reset,
   * never a partial merge. It is recoverable (carry-forward 3) via an
   * in-memory snapshot restored through the store's existing typed setters —
   * no new persistence path.
   */
  import {
    dismissWorkspaceNotice, getWorkspace, getWorkspaceNotice, resetWorkspace, setDensity,
    setDesignLanguage, setLayout, setZoneOrder, setZoneVisibleSurfaces,
    type WorkspaceNoticeKind,
  } from '../../presentation/workspace/store.svelte';
  import {
    WORKSPACE_DESIGN_LANGUAGE_IDS, WORKSPACE_LAYOUT_IDS, WORKSPACE_THEME_IDS,
    type WorkspaceDesignLanguageId, type WorkspaceLayoutId, type WorkspaceThemeId,
    type WorkspaceV1, type WorkspaceZoneId,
  } from '../../presentation/workspace/contract';
  import type { SemanticSurfaceName } from '../../presentation/layouts/contract';
  import { getAvailableThemes, setThemeUserChoice } from '../theme/theme-switcher';
  import { t } from '$lib/i18n';

  const workspace = $derived(getWorkspace());
  const notice = $derived(getWorkspaceNotice());
  const themeNames = new Map(getAvailableThemes().map((th) => [th.id, th.name]));

  let previous = $state<WorkspaceV1 | null>(null);

  function humanize(id: string): string {
    return id.replace(/-/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
  }

  function noticeText(kind: WorkspaceNoticeKind): string {
    const key = {
      'forward-read-only': 'core.settings.workspace.noticeForwardReadOnly',
      repaired: 'core.settings.workspace.noticeRepaired',
      reset: 'core.settings.workspace.noticeReset',
      'version-discarded': 'core.settings.workspace.noticeVersionDiscarded',
      'persist-failed': 'core.settings.workspace.noticePersistFailed',
    } as const satisfies Record<WorkspaceNoticeKind, string>;
    return t(key[kind]);
  }

  function handleLayoutChange(ev: Event): void {
    setLayout((ev.currentTarget as HTMLSelectElement).value as WorkspaceLayoutId);
  }
  function handleLanguageChange(ev: Event): void {
    setDesignLanguage((ev.currentTarget as HTMLSelectElement).value as WorkspaceDesignLanguageId);
  }
  function handleThemeChange(ev: Event): void {
    setThemeUserChoice((ev.currentTarget as HTMLSelectElement).value as WorkspaceThemeId);
  }

  function handleReset(): void {
    previous = resetWorkspace();
  }

  /**
   * Undo, through the store's existing typed setters only — no raw-object
   * restore, so an undone value is re-validated exactly like any other edit.
   * Restores every field this surface exposes (layout/language/theme/density/
   * zones). `pinnedCommands` is deliberately untouched: MOR-1076 decision 7
   * keeps it a validated field with no UI consumer yet, and this surface
   * must stay one of the modules that pin does not name.
   */
  function handleUndo(): void {
    if (!previous) return;
    const prior = previous;
    setLayout(prior.layout);
    setDesignLanguage(prior.designLanguage);
    setThemeUserChoice(prior.theme);
    setDensity(prior.density);
    for (const [zone, surfaces] of Object.entries(prior.visibleSurfaces) as
      [WorkspaceZoneId, readonly SemanticSurfaceName[]][]) {
      setZoneVisibleSurfaces(zone, surfaces);
    }
    for (const [zone, surfaces] of Object.entries(prior.zoneOrder) as
      [WorkspaceZoneId, readonly SemanticSurfaceName[]][]) {
      setZoneOrder(zone, surfaces);
    }
    previous = null;
  }
</script>

<div class="ws-panel" data-testid="workspace-settings-panel">
  {#if notice}
    <div class="ws-notice" role="status" data-testid="workspace-notice">
      <p>{noticeText(notice.kind)}</p>
      {#if notice.rejections.length > 0}
        <ul class="ws-rejections" data-testid="workspace-notice-rejections">
          {#each notice.rejections as r (r.field + r.reason)}
            <li>{r.field || '(document)'}: {r.reason}</li>
          {/each}
        </ul>
      {/if}
      <div class="ws-notice-actions">
        {#if notice.kind === 'forward-read-only'}
          <button type="button" onclick={handleReset} data-testid="workspace-override-reset">
            {t('core.settings.workspace.overrideButton')}
          </button>
        {/if}
        <button type="button" onclick={dismissWorkspaceNotice} data-testid="workspace-notice-dismiss">
          {t('core.settings.workspace.dismissButton')}
        </button>
      </div>
    </div>
  {/if}

  <label class="ws-row">
    <span>{t('core.settings.workspace.layoutLabel')}</span>
    <select data-testid="workspace-layout-select" value={workspace.layout} onchange={handleLayoutChange}>
      {#each WORKSPACE_LAYOUT_IDS as id (id)}
        <option value={id}>{humanize(id)}</option>
      {/each}
    </select>
  </label>

  <label class="ws-row">
    <span>{t('core.settings.workspace.designLanguageLabel')}</span>
    <select data-testid="workspace-language-select" value={workspace.designLanguage} onchange={handleLanguageChange}>
      {#each WORKSPACE_DESIGN_LANGUAGE_IDS as id (id)}
        <option value={id}>{humanize(id)}</option>
      {/each}
    </select>
  </label>

  <label class="ws-row">
    <span>{t('core.settings.workspace.themeLabel')}</span>
    <select data-testid="workspace-theme-select" value={workspace.theme} onchange={handleThemeChange}>
      {#each WORKSPACE_THEME_IDS as id (id)}
        <option value={id}>{themeNames.get(id) ?? humanize(id)}</option>
      {/each}
    </select>
  </label>

  <div class="ws-reset-row">
    <button type="button" onclick={handleReset} data-testid="workspace-reset-button">
      {t('core.settings.workspace.resetButton')}
    </button>
    {#if previous}
      <button type="button" onclick={handleUndo} data-testid="workspace-undo-button">
        {t('core.settings.workspace.undoButton')}
      </button>
    {/if}
  </div>
  <p class="ws-hint">{t('core.settings.workspace.resetHint')}</p>
</div>

<style>
  .ws-panel {
    display: flex;
    flex-direction: column;
    gap: 10px;
    padding: 8px 4px;
    font-family: 'Roboto Mono', monospace;
  }
  .ws-row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    font-size: 11px;
    color: var(--v2-text-secondary, #aaa);
  }
  .ws-row select {
    min-width: 180px;
    padding: 5px 8px;
    background: var(--v2-bg-input, #1a1a2e);
    border: 1px solid var(--v2-border, #2a2a3e);
    border-radius: 3px;
    color: var(--v2-text-primary, #fff);
    font-family: inherit;
    font-size: 12px;
  }
  .ws-row select:focus-visible,
  button:focus-visible {
    outline: var(--v2-focus-ring);
    outline-offset: 2px;
  }
  .ws-notice {
    border: 1px solid var(--v2-accent-yellow, #eab308);
    border-radius: 4px;
    padding: 8px;
    font-size: 11px;
    color: var(--v2-text-primary, #fff);
  }
  .ws-notice p {
    margin: 0 0 6px 0;
  }
  .ws-rejections {
    margin: 0 0 6px 0;
    padding-left: 16px;
    font-size: 10px;
    color: var(--v2-text-dim, #888);
  }
  .ws-notice-actions,
  .ws-reset-row {
    display: flex;
    gap: 8px;
  }
  button {
    background: var(--v2-bg-input, #1a1a2e);
    border: 1px solid var(--v2-border, #2a2a3e);
    border-radius: 3px;
    color: var(--v2-text-primary, #fff);
    padding: 5px 10px;
    font-family: inherit;
    font-size: 11px;
    cursor: pointer;
  }
  button:hover {
    border-color: var(--v2-accent-cyan, #06b6d4);
  }
  .ws-hint {
    margin: 0;
    font-size: 10px;
    color: var(--v2-text-dim, #888);
  }
</style>
