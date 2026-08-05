/**
 * MOR-1079 module-load purity, in the MOR-1077 / MOR-1078 idiom: a load-time
 * spy pin over the whole transitive closure (catches an import-time side
 * effect no call-time assertion would see), plus the boundary pin that the
 * store is the only module in the closure that touches storage at all.
 *
 * Unlike its two predecessors this module DOES write — it is the persistence
 * boundary — so the pin is "not at import, only on an explicit init/action",
 * not "never".
 *
 * Pool: `isolated` (MOR-1272) — `vi.stubGlobal` + `vi.resetModules()` are the
 * order-dependent shapes under the fast pool's `isolate: false`; registered in
 * `vite.config.ts` alongside the sibling workspace purity entries.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { dirname, join, normalize } from 'node:path';

const ENTRY = 'src/presentation/workspace/store.svelte.ts';

function code(path: string): string {
  return readFileSync(path, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');
}

function relativeImports(path: string): string[] {
  const source = code(path);
  return [...source.matchAll(/(?:from|import)\s*['"](\.[^'"]+)['"]/g)]
    .map((m) => normalize(join(dirname(path), m[1].endsWith('.ts') ? m[1] : `${m[1]}.ts`)));
}

function closure(entry: string): string[] {
  const seen = new Set<string>();
  const queue = [entry];
  while (queue.length > 0) {
    const path = queue.shift()!;
    if (seen.has(path)) continue;
    seen.add(path);
    queue.push(...relativeImports(path));
  }
  return [...seen];
}

const storage = { getItem: vi.fn(() => null), setItem: vi.fn(), removeItem: vi.fn(), clear: vi.fn(), key: vi.fn(() => null), length: 0 };
const fetchSpy = vi.fn();
const wsCtor = vi.fn();
class SpyWebSocket { constructor(...args: unknown[]) { wsCtor(...args); } }

function stubAll(): void {
  vi.stubGlobal('localStorage', storage);
  vi.stubGlobal('sessionStorage', storage);
  vi.stubGlobal('fetch', fetchSpy);
  vi.stubGlobal('WebSocket', SpyWebSocket);
  for (const spy of [storage.getItem, storage.setItem, storage.removeItem, storage.clear, fetchSpy, wsCtor]) spy.mockClear();
}

describe('the workspace store is inert until it is initialised (MOR-1079)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  it('importing the module touches no storage, network or socket', async () => {
    stubAll();
    vi.resetModules();

    const module = await import('../store.svelte');

    expect(typeof module.initWorkspaceStore).toBe('function');
    expect(storage.getItem).not.toHaveBeenCalled();
    expect(storage.setItem).not.toHaveBeenCalled();
    expect(storage.removeItem).not.toHaveBeenCalled();
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(wsCtor).not.toHaveBeenCalled();
  });

  it('reading state before init still touches no storage', async () => {
    stubAll();
    vi.resetModules();
    const { getWorkspace, getWorkspaceNotice } = await import('../store.svelte');

    expect(getWorkspace().layout).toBe('auto');
    expect(getWorkspaceNotice()).toBeNull();
    expect(storage.getItem).not.toHaveBeenCalled();
    expect(storage.setItem).not.toHaveBeenCalled();
  });

  it('an explicit init is what reaches the ambient localStorage, and it never deletes', async () => {
    stubAll();
    vi.resetModules();
    const { initWorkspaceStore, setTheme } = await import('../store.svelte');

    initWorkspaceStore();
    setTheme('nord');

    expect(storage.getItem).toHaveBeenCalled();
    expect(storage.setItem).toHaveBeenCalled();
    expect(storage.removeItem).not.toHaveBeenCalled();
    expect(storage.clear).not.toHaveBeenCalled();
    // Single-key writes only: the workspace key and its migration sentinel.
    const keys = new Set(storage.setItem.mock.calls.map((c) => c[0] as string));
    expect([...keys].sort()).toEqual(['rigplane:workspace', 'rigplane:workspace-migrated:v1']);
  });

  it('the closure is the store, the repository and the three pure contracts', () => {
    expect(closure(ENTRY).sort()).toEqual([
      'src/presentation/languages/contract.ts',
      'src/presentation/layouts/contract.ts',
      'src/presentation/workspace/contract.ts',
      'src/presentation/workspace/legacy-readers.ts',
      'src/presentation/workspace/repository.ts',
      ENTRY,
    ].sort());
  });

  it.each(closure(ENTRY))('%s imports no store, transport, audio, skin or component module', (path) => {
    const source = code(path);
    expect(source).not.toMatch(/from\s+['"]\$lib\//);
    expect(source).not.toMatch(/from\s+['"][^'"]*(stores|transport|audio|skins|components-v2|semantic)\//);
  });

  it.each(closure(ENTRY).filter((p) => p !== ENTRY))('%s never names a browser storage global', (path) => {
    expect(code(path)).not.toMatch(/\b(localStorage|sessionStorage|document|window)\b/);
  });
});
