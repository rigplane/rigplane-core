/**
 * MOR-1080a — the workspace selection surface: layout / design language /
 * theme pickers enumerate only the ids the workspace validator itself pins.
 * The discard/repair notice and recoverable reset are pinned in the follow-up
 * MOR-1080c's extension of this file; import/export gets its own test file
 * in MOR-1080b.
 *
 * Storage is injected (`initWorkspaceStore(fake)`), no module is
 * `vi.mock`-ed, and no global is stubbed, so this file stays in the fast
 * pool per the MOR-1272 doctrine (see `store.test.ts`).
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync, tick } from 'svelte';
import WorkspaceSettingsPanel from '../WorkspaceSettingsPanel.svelte';
import { getWorkspace, initWorkspaceStore, setDesignLanguage, setLayout, setTheme } from '../../../presentation/workspace/store.svelte';
import { WORKSPACE_DESIGN_LANGUAGE_IDS, WORKSPACE_LAYOUT_IDS, WORKSPACE_THEME_IDS } from '../../../presentation/workspace/contract';
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

describe('WorkspaceSettingsPanel (MOR-1080a)', () => {
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

  it('catalog source ships every key the component reads', () => {
    const required = [
      'core.settings.workspace.layoutLabel', 'core.settings.workspace.designLanguageLabel',
      'core.settings.workspace.themeLabel',
    ];
    const catalog = enUS as Record<string, unknown>;
    for (const key of required) expect(catalog[key], `en-US missing ${key}`).toBeTypeOf('string');
  });
});
