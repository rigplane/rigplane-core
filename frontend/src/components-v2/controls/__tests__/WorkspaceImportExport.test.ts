/**
 * MOR-1080 — workspace import/export. Import goes through
 * `readWorkspaceJson` only (never a hand-rolled check); a rejected import
 * commits nothing and surfaces the validator's typed field + reason. Export
 * downloads `serializeWorkspace` verbatim.
 *
 * Storage is injected, no module is `vi.mock`-ed; `URL.createObjectURL` is
 * spied (not stubbed) and restored per test, the same shape as
 * `SendReportDialog.isolated.test.ts`'s save-locally test — so this file stays in
 * the fast pool per MOR-1272.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import { mount, unmount, flushSync, tick } from 'svelte';
import WorkspaceImportExport from '../WorkspaceImportExport.svelte';
import { getWorkspace, initWorkspaceStore, setTheme } from '../../../presentation/workspace/store.svelte';
import { DEFAULT_WORKSPACE } from '../../../presentation/workspace/contract';
import enUS from '$lib/i18n/locales/en-US.json' with { type: 'json' };

class FakeStorage {
  readonly map = new Map<string, string>();
  getItem(key: string): string | null {
    return this.map.has(key) ? this.map.get(key)! : null;
  }
  setItem(key: string, value: string): void {
    this.map.set(key, value);
  }
}

function setup() {
  const target = document.createElement('div');
  document.body.appendChild(target);
  const component = mount(WorkspaceImportExport, { target });
  flushSync();
  return { target, component };
}

async function submitPaste(target: HTMLElement, text: string): Promise<void> {
  const textarea = target.querySelector('[data-testid="workspace-import-textarea"]') as HTMLTextAreaElement;
  textarea.value = text;
  textarea.dispatchEvent(new Event('input', { bubbles: true }));
  flushSync();
  (target.querySelector('[data-testid="workspace-import-button"]') as HTMLButtonElement).click();
  flushSync();
  await tick();
}

beforeEach(() => {
  initWorkspaceStore(new FakeStorage());
});

afterEach(() => {
  document.body.innerHTML = '';
});

describe('WorkspaceImportExport (MOR-1080)', () => {
  it('imports a valid pasted document and clears the textarea', async () => {
    const { target, component } = setup();

    await submitPaste(target, JSON.stringify({ ...DEFAULT_WORKSPACE, theme: 'nord' }));

    expect(getWorkspace().theme).toBe('nord');
    expect(target.querySelector('[data-testid="workspace-import-rejected"]')).toBeNull();
    expect((target.querySelector('[data-testid="workspace-import-textarea"]') as HTMLTextAreaElement).value).toBe('');
    unmount(component);
  });

  it('rejects an invalid document, commits nothing, and shows the field + reason (not a generic message)', async () => {
    setTheme('gruvbox-dark');
    const { target, component } = setup();

    await submitPaste(target, JSON.stringify({ ...DEFAULT_WORKSPACE, theme: 'not-a-real-theme' }));

    expect(getWorkspace().theme).toBe('gruvbox-dark');
    const rejected = target.querySelector('[data-testid="workspace-import-rejected"]');
    expect(rejected).not.toBeNull();
    expect(rejected!.getAttribute('role')).toBe('alert');
    expect(rejected!.textContent).toContain('theme');
    expect(rejected!.textContent).toContain('unknown-id');
    unmount(component);
  });

  it('rejects malformed JSON text without touching the store', async () => {
    setTheme('nord');
    const { target, component } = setup();

    await submitPaste(target, 'not json{{{');

    expect(getWorkspace().theme).toBe('nord');
    expect(target.querySelector('[data-testid="workspace-import-rejected"]')).not.toBeNull();
    unmount(component);
  });

  it('imports from a file, same validation path as paste', async () => {
    const { target, component } = setup();
    const input = target.querySelector('[data-testid="workspace-import-file"]') as HTMLInputElement;
    const file = new File([JSON.stringify({ ...DEFAULT_WORKSPACE, layout: 'lcd-cockpit' })], 'w.json', {
      type: 'application/json',
    });
    Object.defineProperty(input, 'files', { value: [file], configurable: true });

    input.dispatchEvent(new Event('change', { bubbles: true }));
    await vi.waitFor(() => {
      flushSync();
      expect(getWorkspace().layout).toBe('lcd-cockpit');
    });
    unmount(component);
  });

  it('exports the current workspace verbatim via a download', async () => {
    setTheme('nord', true);
    const createUrlSpy = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob://test');
    const revokeUrlSpy = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => {});

    const { target, component } = setup();
    (target.querySelector('[data-testid="workspace-export-button"]') as HTMLButtonElement).click();

    expect(createUrlSpy).toHaveBeenCalledTimes(1);
    const blob = createUrlSpy.mock.calls[0][0] as Blob;
    expect(blob.type).toBe('application/json');
    const text = await blob.text();
    expect(JSON.parse(text)).toEqual({ ...DEFAULT_WORKSPACE, theme: 'nord' });

    createUrlSpy.mockRestore();
    revokeUrlSpy.mockRestore();
    unmount(component);
  });

  it('catalog source ships every key the component reads', () => {
    const required = [
      'core.settings.workspace.exportButton', 'core.settings.workspace.importFileLabel',
      'core.settings.workspace.importPasteLabel', 'core.settings.workspace.importButton',
      'core.settings.workspace.importRejectedTitle',
    ];
    const catalog = enUS as Record<string, unknown>;
    for (const key of required) expect(catalog[key], `en-US missing ${key}`).toBeTypeOf('string');
  });
});
