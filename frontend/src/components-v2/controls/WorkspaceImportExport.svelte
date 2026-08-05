<script lang="ts">
  /**
   * MOR-1080 — workspace import/export, split out of `WorkspaceSettingsPanel`
   * to keep each file focused (design constraint: split import/export from
   * selection when necessary).
   *
   * Import goes through `importWorkspace` (contract.ts's `readWorkspaceJson`
   * under the hood) ONLY — never a hand-rolled check (carry-forward 4). Any
   * rejection, including a lossy forward-read, commits nothing and is shown
   * with its typed field + reason, not a generic "invalid file". Export uses
   * `exportWorkspace` (`serializeWorkspace` verbatim) — versioned by
   * construction, no extra fields (carry-forward 5).
   */
  import { exportWorkspace, importWorkspace } from '../../presentation/workspace/store.svelte';
  import type { WorkspaceRejection } from '../../presentation/workspace/contract';
  import { t } from '$lib/i18n';

  let pasteText = $state('');
  let rejections = $state<readonly WorkspaceRejection[] | null>(null);

  function runImport(text: string): void {
    const result = importWorkspace(text);
    const rejected = result.outcome === 'version-discarded' || result.rejections.length > 0;
    rejections = rejected ? result.rejections : null;
    if (!rejected) pasteText = '';
  }

  function handlePasteImport(): void {
    if (pasteText.trim() === '') return;
    runImport(pasteText);
  }

  function handleFileChange(ev: Event): void {
    const input = ev.currentTarget as HTMLInputElement;
    const file = input.files?.[0];
    input.value = '';
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => runImport(String(reader.result ?? ''));
    reader.readAsText(file);
  }

  function handleExport(): void {
    const blob = new Blob([JSON.stringify(exportWorkspace(), null, 2)], { type: 'application/json' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = 'rigplane-workspace.json';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
  }
</script>

<div class="ws-io" data-testid="workspace-import-export">
  <button type="button" onclick={handleExport} data-testid="workspace-export-button">
    {t('core.settings.workspace.exportButton')}
  </button>

  <label class="ws-io-field">
    <span>{t('core.settings.workspace.importFileLabel')}</span>
    <input
      type="file"
      accept="application/json,.json"
      onchange={handleFileChange}
      data-testid="workspace-import-file"
    />
  </label>

  <label class="ws-io-field">
    <span>{t('core.settings.workspace.importPasteLabel')}</span>
    <textarea rows="4" bind:value={pasteText} data-testid="workspace-import-textarea"></textarea>
  </label>
  <button type="button" onclick={handlePasteImport} data-testid="workspace-import-button">
    {t('core.settings.workspace.importButton')}
  </button>

  {#if rejections}
    <div class="ws-io-rejected" role="alert" data-testid="workspace-import-rejected">
      <p>{t('core.settings.workspace.importRejectedTitle')}</p>
      <ul>
        {#each rejections as r (r.field + r.reason)}
          <li>{r.field || '(document)'}: {r.reason}</li>
        {/each}
      </ul>
    </div>
  {/if}
</div>

<style>
  .ws-io {
    display: flex;
    flex-direction: column;
    gap: 8px;
    padding: 8px 4px;
    font-family: 'Roboto Mono', monospace;
    font-size: 11px;
    color: var(--v2-text-secondary, #aaa);
  }
  .ws-io-field {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }
  .ws-io textarea,
  .ws-io input[type='file'] {
    background: var(--v2-bg-input, #1a1a2e);
    border: 1px solid var(--v2-border, #2a2a3e);
    border-radius: 3px;
    color: var(--v2-text-primary, #fff);
    font-family: inherit;
    font-size: 11px;
    padding: 6px;
  }
  button {
    align-self: flex-start;
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
  button:focus-visible,
  textarea:focus-visible,
  input:focus-visible {
    outline: var(--v2-focus-ring);
    outline-offset: 2px;
  }
  .ws-io-rejected {
    border: 1px solid var(--v2-accent-red, #ef4444);
    border-radius: 4px;
    padding: 8px;
  }
  .ws-io-rejected p {
    margin: 0 0 6px 0;
  }
  .ws-io-rejected ul {
    margin: 0;
    padding-left: 16px;
    font-size: 10px;
    color: var(--v2-text-dim, #888);
  }
</style>
