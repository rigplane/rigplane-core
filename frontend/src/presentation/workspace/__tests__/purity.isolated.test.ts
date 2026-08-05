/**
 * MOR-1077 load-time purity. The workspace contract is a PURE module: reading
 * it must never touch storage, the DOM, transport or audio — persistence is
 * MOR-1079's boundary, and the 3A lesson (`lib/runtime/adapters/__tests__/
 * rx-audio-purity.isolated.test.ts`) is that a side effect fired at IMPORT is invisible
 * to every behavioural assertion made afterwards.
 *
 * Two pins with different, non-interchangeable coverage:
 *  1. LOAD-TIME SPY — globals are stubbed, the module registry is reset, and
 *     the contract is imported fresh. Covers its whole transitive closure and
 *     is the only pin that sees an effect fired at import rather than at call.
 *  2. STRUCTURAL CLOSURE — the import graph is walked from the contract's own
 *     source and every file in it is checked for banned specifiers and banned
 *     globals. Source-text pins are normally non-transitive; this one is not,
 *     because it enumerates the closure rather than reading a single file.
 *
 * Pool: `isolated` (MOR-1272). `vi.stubGlobal` + `vi.resetModules()` are
 * exactly the shared-state shapes that are order-dependent under the fast
 * pool's `isolate: false` — registered in `vite.config.ts`.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { dirname, join, normalize } from 'node:path';

const ENTRY = 'src/presentation/workspace/contract.ts';

/** Comments are stripped first: this file's subjects DOCUMENT the prohibition
 *  in prose ("nothing here reads or writes storage"), and a naive text search
 *  would match the doctrine instead of the code. */
function code(path: string): string {
  return readFileSync(path, 'utf8').replace(/\/\*[\s\S]*?\*\//g, '').replace(/(^|[^:])\/\/.*$/gm, '$1');
}

/** Every relative specifier a file imports or re-exports, `.ts`-resolved. */
function relativeImports(path: string): string[] {
  const source = code(path);
  return [...source.matchAll(/(?:from|import)\s*['"](\.[^'"]+)['"]/g)]
    .map((m) => normalize(join(dirname(path), m[1].endsWith('.ts') ? m[1] : `${m[1]}.ts`)));
}

/** The contract's full transitive closure of first-party modules. */
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

describe('the workspace contract is pure at load (MOR-1077)', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.resetModules();
  });

  // ── Pin 1: load-time, closure-wide ─────────────────────────────────────
  it('importing the contract touches no storage, network or socket', async () => {
    vi.stubGlobal('localStorage', storage);
    vi.stubGlobal('sessionStorage', storage);
    vi.stubGlobal('fetch', fetchSpy);
    vi.stubGlobal('WebSocket', SpyWebSocket);
    for (const spy of [storage.getItem, storage.setItem, storage.removeItem, fetchSpy, wsCtor]) spy.mockClear();

    vi.resetModules();
    const module = await import('../contract');

    // The module really did evaluate — otherwise "zero calls" proves nothing.
    expect(module.DEFAULT_WORKSPACE.layout).toBe('auto');
    expect(storage.getItem).not.toHaveBeenCalled();
    expect(storage.setItem).not.toHaveBeenCalled();
    expect(storage.removeItem).not.toHaveBeenCalled();
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(wsCtor).not.toHaveBeenCalled();
  });

  it('reading a workspace mutates neither its input nor the shared defaults', async () => {
    const { readWorkspace, DEFAULT_WORKSPACE } = await import('../contract');
    const input = { version: 1, layout: 'lcd', pinnedCommands: ['set_compressor'], future: { a: 1 } };
    const snapshot = structuredClone(input);
    const first = readWorkspace(input);
    const second = readWorkspace(input);

    expect(input).toEqual(snapshot);
    expect(second).toEqual(first);
    expect(DEFAULT_WORKSPACE.visibleSurfaces).toEqual({});
    expect(DEFAULT_WORKSPACE.pinnedCommands).toEqual([]);
  });

  // ── Pin 2: structural, transitive over the enumerated closure ───────────
  it('the closure is exactly the contract plus the two pure presentation contracts', () => {
    expect(closure(ENTRY).sort()).toEqual([
      'src/presentation/languages/contract.ts',
      'src/presentation/layouts/contract.ts',
      ENTRY,
    ].sort());
  });

  it.each(closure(ENTRY))('%s imports no store, transport, audio, skin or component module', (path) => {
    const source = code(path);
    expect(source).not.toMatch(/from\s+['"]\$lib\//);
    expect(source).not.toMatch(/from\s+['"][^'"]*(stores|transport|audio|skins|components-v2|semantic)\//);
    expect(source).not.toMatch(/\bdeclarations['"]/);
  });

  it.each(closure(ENTRY))('%s references no browser or runtime global', (path) => {
    const source = code(path);
    for (const banned of [/\blocalStorage\b/, /\bsessionStorage\b/, /\bdocument\b/, /\bwindow\b/, /\bfetch\(/, /\bWebSocket\b/, /\bAudioContext\b/]) {
      expect(source).not.toMatch(banned);
    }
  });

  it('the contract persists no component module path — ids only (v3 ADR invariant 6)', () => {
    const source = code(ENTRY);
    expect(source).not.toMatch(/import\s*\(/);
    expect(source).not.toMatch(/\.svelte['"]/);
  });
});
