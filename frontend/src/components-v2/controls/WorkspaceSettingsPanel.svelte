<script lang="ts">
  /**
   * MOR-1080a — workspace selection UI: layout / design language / theme.
   *
   * Enumerates ONLY the ids the workspace validator itself pins
   * (`WORKSPACE_LAYOUT_IDS` / `WORKSPACE_DESIGN_LANGUAGE_IDS` /
   * `WORKSPACE_THEME_IDS` from `contract.ts`, the MOR-1077 precedent) — never
   * a hand-rolled list, so a selectable id can never drift from what the
   * store accepts. Theme writes route through `theme-switcher.ts` (not the
   * store directly) so the DOM `data-theme` attribute stays in sync, exactly
   * like the existing `ThemePicker`.
   *
   * The discard/repair notice and recoverable reset land in the follow-up
   * MOR-1080c; import/export is the sibling `WorkspaceImportExport.svelte`
   * landing in MOR-1080b — both split out to keep this file inside the
   * frozen strict-LOC guardrail (owner ruling on MOR-1080's F1 finding).
   */
  import { getWorkspace, setDesignLanguage, setLayout } from '../../presentation/workspace/store.svelte';
  import {
    WORKSPACE_DESIGN_LANGUAGE_IDS, WORKSPACE_LAYOUT_IDS, WORKSPACE_THEME_IDS,
    type WorkspaceDesignLanguageId, type WorkspaceLayoutId, type WorkspaceThemeId,
  } from '../../presentation/workspace/contract';
  import { getAvailableThemes, setThemeUserChoice } from '../theme/theme-switcher';
  import { t } from '$lib/i18n';

  const workspace = $derived(getWorkspace());
  const themeNames = new Map(getAvailableThemes().map((th) => [th.id, th.name]));

  function humanize(id: string): string {
    return id.replace(/-/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
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
</script>

<div class="ws-panel" data-testid="workspace-settings-panel">
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
  .ws-row select:focus-visible {
    outline: var(--v2-focus-ring);
    outline-offset: 2px;
  }
</style>
