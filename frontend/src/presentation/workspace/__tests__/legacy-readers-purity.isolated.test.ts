/**
 * MOR-1078 read-only purity, mirroring the MOR-1077 idiom in
 * `contract-purity.test.ts` (formerly `purity.test.ts`): a module-LOAD spy
 * pin (covers the whole transitive closure, catches an import-time side
 * effect no call-time assertion would see) plus a CALL-time spy pin scoped
 * to the one storage-touching function, `snapshotLegacyStorage`. Actual
 * persistence is MOR-1079's boundary — this module must never write or
 * delete a key, only read the enumerated migrate-key closure.
 *
 * Pool: `isolated` (MOR-1272) — `vi.stubGlobal` + `vi.resetModules()` are the
 * shared-state shapes that are order-dependent under the fast pool's
 * `isolate: false`; registered in `vite.config.ts` alongside the sibling
 * `contract-purity.test.ts` entry.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { dirname, join, normalize } from 'node:path';

const ENTRY = 'src/presentation/workspace/legacy-readers.ts';

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

describe('legacy workspace readers are read-only (MOR-1078)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  // ── Pin 1: load-time, closure-wide ─────────────────────────────────────
  it('importing the module touches no storage, network or socket', async () => {
    vi.stubGlobal('localStorage', storage);
    vi.stubGlobal('sessionStorage', storage);
    vi.stubGlobal('fetch', fetchSpy);
    vi.stubGlobal('WebSocket', SpyWebSocket);
    for (const spy of [storage.getItem, storage.setItem, storage.removeItem, fetchSpy, wsCtor]) spy.mockClear();

    vi.resetModules();
    const module = await import('../legacy-readers');

    expect(module.LEGACY_MIGRATE_KEYS.length).toBeGreaterThan(0);
    expect(storage.getItem).not.toHaveBeenCalled();
    expect(storage.setItem).not.toHaveBeenCalled();
    expect(storage.removeItem).not.toHaveBeenCalled();
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(wsCtor).not.toHaveBeenCalled();
  });

  // ── Pin 2: call-time, scoped to the one storage-touching function ──────
  it('snapshotLegacyStorage / readLegacyWorkspaceFromStorage never write or delete', async () => {
    const { snapshotLegacyStorage, readLegacyWorkspaceFromStorage, LEGACY_MIGRATE_KEYS } = await import('../legacy-readers');
    const spySetItem = vi.fn();
    const spyRemoveItem = vi.fn();
    const spyClear = vi.fn();
    const spyStorage = {
      getItem: vi.fn((key: string) => (key === 'rigplane:theme' ? 'nord' : null)),
      setItem: spySetItem,
      removeItem: spyRemoveItem,
      clear: spyClear,
    };

    snapshotLegacyStorage(spyStorage);
    readLegacyWorkspaceFromStorage(spyStorage);

    expect(spySetItem).not.toHaveBeenCalled();
    expect(spyRemoveItem).not.toHaveBeenCalled();
    expect(spyClear).not.toHaveBeenCalled();
    // Enumerated-closure pin: getItem is called for exactly the migrate-key
    // set, twice each (once per call above) — never a key outside the set.
    const readKeys = new Set(spyStorage.getItem.mock.calls.map((c) => c[0] as string));
    expect([...readKeys].sort()).toEqual([...LEGACY_MIGRATE_KEYS].sort());
  });

  // ── Pin 3: structural, transitive over the enumerated closure ───────────
  it('the closure is exactly the reader plus the two pure presentation contracts', () => {
    expect(closure(ENTRY).sort()).toEqual([
      'src/presentation/languages/contract.ts',
      'src/presentation/layouts/contract.ts',
      'src/presentation/workspace/contract.ts',
      ENTRY,
    ].sort());
  });

  it.each(closure(ENTRY))('%s imports no store, transport, audio, skin or component module', (path) => {
    const source = code(path);
    expect(source).not.toMatch(/from\s+['"]\$lib\//);
    expect(source).not.toMatch(/from\s+['"][^'"]*(stores|transport|audio|skins|components-v2|semantic)\//);
    expect(source).not.toMatch(/\bdeclarations['"]/);
  });

  it('the reader itself references no browser or runtime global at module scope', () => {
    const source = code(ENTRY);
    for (const banned of [/\blocalStorage\b/, /\bsessionStorage\b/, /\bdocument\b/, /\bwindow\b/, /\bfetch\(/, /\bWebSocket\b/, /\bAudioContext\b/]) {
      expect(source).not.toMatch(banned);
    }
  });
});
