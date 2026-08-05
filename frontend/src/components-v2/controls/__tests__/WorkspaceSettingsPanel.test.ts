/**
 * MOR-1080 — the workspace selection surface: layout / design language /
 * theme pickers enumerate only the ids the workspace validator itself pins,
 * the discard/repair notice is surfaced with an override where safe, and
 * reset is recoverable via an in-session undo.
 *
 * Storage is injected (`initWorkspaceStore(fake)`), no module is
 * `vi.mock`-ed, and no global is stubbed, so this file stays in the fast
 * pool per the MOR-1272 doctrine (see `store.test.ts`).
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync, tick } from 'svelte';
import WorkspaceSettingsPanel from '../WorkspaceSettingsPanel.svelte';
import {
  getWorkspace, initWorkspaceStore, setDesignLanguage, setLayout, setPinnedCommands, setTheme,
} from '../../../presentation/workspace/store.svelte';
import {
  DEFAULT_WORKSPACE, WORKSPACE_DESIGN_LANGUAGE_IDS, WORKSPACE_LAYOUT_IDS, WORKSPACE_THEME_IDS,
} from '../../../presentation/workspace/contract';
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

let storage: FakeStorage;

function setup() {
  const target = document.createElement('div');
  document.body.appendChild(target);
  const component = mount(WorkspaceSettingsPanel, { target });
  flushSync();
  return { target, component };
}

beforeEach(() => {
  storage = new FakeStorage();
  initWorkspaceStore(storage);
});

afterEach(() => {
  document.body.innerHTML = '';
  delete document.documentElement.dataset.theme;
});

describe('WorkspaceSettingsPanel (MOR-1080)', () => {
  it('enumerates exactly the registered layout ids, in order', () => {
    const { target, component } = setup();
    const sel = target.querySelector('[data-testid="workspace-layout-select"]') as HTMLSelectElement;
    expect(Array.from(sel.options).map((o) => o.value)).toEqual([...WORKSPACE_LAYOUT_IDS]);
    unmount(component);
  });

  it('enumerates exactly the registered design-language ids, in order', () => {
    const { target, component } = setup();
    const sel = target.querySelector('[data-testid="workspace-language-select"]') as HTMLSelectElement;
    expect(Array.from(sel.options).map((o) => o.value)).toEqual([...WORKSPACE_DESIGN_LANGUAGE_IDS]);
    unmount(component);
  });

  it('enumerates exactly the registered theme ids, in order', () => {
    const { target, component } = setup();
    const sel = target.querySelector('[data-testid="workspace-theme-select"]') as HTMLSelectElement;
    expect(Array.from(sel.options).map((o) => o.value)).toEqual([...WORKSPACE_THEME_IDS]);
    unmount(component);
  });

  it('reflects the current workspace values in each select', () => {
    setLayout('lcd-scope');
    setDesignLanguage('fieldline');
    setTheme('nord', true);

    const { target, component } = setup();

    expect((target.querySelector('[data-testid="workspace-layout-select"]') as HTMLSelectElement).value).toBe('lcd-scope');
    expect((target.querySelector('[data-testid="workspace-language-select"]') as HTMLSelectElement).value).toBe('fieldline');
    expect((target.querySelector('[data-testid="workspace-theme-select"]') as HTMLSelectElement).value).toBe('nord');
    unmount(component);
  });

  it('every select is wrapped in a <label> with visible text', () => {
    const { target, component } = setup();
    for (const testid of ['workspace-layout-select', 'workspace-language-select', 'workspace-theme-select']) {
      const sel = target.querySelector(`[data-testid="${testid}"]`) as HTMLSelectElement;
      const label = sel.closest('label');
      expect(label, `${testid} must be inside a <label>`).not.toBeNull();
      expect(label!.textContent?.trim().length).toBeGreaterThan(0);
    }
    unmount(component);
  });

  it('changing the layout select writes through the store', async () => {
    const { target, component } = setup();
    const sel = target.querySelector('[data-testid="workspace-layout-select"]') as HTMLSelectElement;

    sel.value = 'standard';
    sel.dispatchEvent(new Event('change', { bubbles: true }));
    flushSync();
    await tick();

    expect(getWorkspace().layout).toBe('standard');
    unmount(component);
  });

  it('changing the theme select writes through the store AND applies the DOM attribute', async () => {
    const { target, component } = setup();
    const sel = target.querySelector('[data-testid="workspace-theme-select"]') as HTMLSelectElement;

    sel.value = 'crt-green';
    sel.dispatchEvent(new Event('change', { bubbles: true }));
    flushSync();
    await tick();

    expect(getWorkspace().theme).toBe('crt-green');
    expect(document.documentElement.dataset.theme).toBe('crt-green');
    unmount(component);
  });

  it('reset restores the frozen defaults and offers an undo that restores the prior values', async () => {
    setLayout('lcd-scope');
    setTheme('nord', true);
    const { target, component } = setup();

    (target.querySelector('[data-testid="workspace-reset-button"]') as HTMLButtonElement).click();
    flushSync();
    await tick();

    expect(getWorkspace()).toEqual(DEFAULT_WORKSPACE);
    const undoBtn = target.querySelector('[data-testid="workspace-undo-button"]') as HTMLButtonElement;
    expect(undoBtn).not.toBeNull();

    undoBtn.click();
    flushSync();
    await tick();

    expect(getWorkspace().layout).toBe('lcd-scope');
    expect(getWorkspace().theme).toBe('nord');
    // Undo is a one-shot: the affordance disappears once used.
    expect(target.querySelector('[data-testid="workspace-undo-button"]')).toBeNull();
    unmount(component);
  });

  it('F2 — undo does not restore pinnedCommands: MOR-1076 decision 7 keeps it a field with no UI consumer', async () => {
    setPinnedCommands(['set_compressor']);
    const { target, component } = setup();

    (target.querySelector('[data-testid="workspace-reset-button"]') as HTMLButtonElement).click();
    flushSync();
    await tick();
    expect(getWorkspace().pinnedCommands).toEqual([]);

    (target.querySelector('[data-testid="workspace-undo-button"]') as HTMLButtonElement).click();
    flushSync();
    await tick();

    // If `handleUndo` ever grew a `setPinnedCommands(prior.pinnedCommands)` call
    // (it looks like a natural bug fix), this value would flip back to
    // `['set_compressor']`. It must stay exactly what the reset left it at.
    expect(getWorkspace().pinnedCommands).toEqual([]);
    unmount(component);
  });

  it('shows no notice and no undo affordance on a clean mount', () => {
    const { target, component } = setup();
    expect(target.querySelector('[data-testid="workspace-notice"]')).toBeNull();
    expect(target.querySelector('[data-testid="workspace-undo-button"]')).toBeNull();
    unmount(component);
  });

  it('a repaired notice is shown as a status region and can be dismissed without resetting anything', async () => {
    storage.map.set('rigplane:workspace', JSON.stringify({ version: 1, theme: 'no-such-theme' }));
    initWorkspaceStore(storage);
    const { target, component } = setup();

    const notice = target.querySelector('[data-testid="workspace-notice"]');
    expect(notice).not.toBeNull();
    expect(notice!.getAttribute('role')).toBe('status');
    const rejections = target.querySelector('[data-testid="workspace-notice-rejections"]');
    expect(rejections!.textContent).toContain('theme');
    expect(rejections!.textContent).toContain('unknown-id');
    // Repaired is not the latched forward-read-only case: no override button.
    expect(target.querySelector('[data-testid="workspace-override-reset"]')).toBeNull();

    (target.querySelector('[data-testid="workspace-notice-dismiss"]') as HTMLButtonElement).click();
    flushSync();
    await tick();

    expect(target.querySelector('[data-testid="workspace-notice"]')).toBeNull();
    unmount(component);
  });

  it('a forward-read-only notice offers exactly the safe override: reset to defaults', async () => {
    storage.map.set('rigplane:workspace', JSON.stringify({
      ...DEFAULT_WORKSPACE, version: 3, theme: 'v3-only-theme',
    }));
    initWorkspaceStore(storage);
    const { target, component } = setup();

    const overrideBtn = target.querySelector('[data-testid="workspace-override-reset"]') as HTMLButtonElement;
    expect(overrideBtn).not.toBeNull();

    overrideBtn.click();
    flushSync();
    await tick();

    expect(target.querySelector('[data-testid="workspace-notice"]')).toBeNull();
    expect(getWorkspace()).toEqual(DEFAULT_WORKSPACE);
    expect(target.querySelector('[data-testid="workspace-undo-button"]')).not.toBeNull();
    unmount(component);
  });

  it('catalog source ships every key the component reads', () => {
    const required = [
      'core.settings.workspace.layoutLabel', 'core.settings.workspace.designLanguageLabel',
      'core.settings.workspace.themeLabel', 'core.settings.workspace.resetButton',
      'core.settings.workspace.resetHint', 'core.settings.workspace.undoButton',
      'core.settings.workspace.dismissButton', 'core.settings.workspace.overrideButton',
      'core.settings.workspace.noticeForwardReadOnly', 'core.settings.workspace.noticeRepaired',
      'core.settings.workspace.noticeReset', 'core.settings.workspace.noticeVersionDiscarded',
      'core.settings.workspace.noticePersistFailed',
    ];
    const catalog = enUS as Record<string, unknown>;
    for (const key of required) expect(catalog[key], `en-US missing ${key}`).toBeTypeOf('string');
  });
});
