/**
 * MOR-1079 — the pure persistence layer. Everything here runs against an
 * injected fake `Storage`; no global is stubbed, so this file stays in the
 * fast pool (MOR-1272).
 *
 * Pins, in ticket order: atomic write / failure fallback, reload, invalid
 * stored state, migration idempotency, unknown future version — plus the two
 * carry-forwards this layer owns outright (legacy keys are never written or
 * deleted; the workspace key has exactly one writer in `src/`).
 */
import { describe, expect, it } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import {
  WORKSPACE_MIGRATION_SENTINEL_KEY, WORKSPACE_STORAGE_KEY, applyWorkspacePatch,
  canPersistWorkspace, loadWorkspace, markWorkspaceMigrated, persistWorkspace,
} from '../repository';
import { LEGACY_KEY_ROUTING } from '../legacy-readers';
import { DEFAULT_WORKSPACE, serializeWorkspace } from '../contract';

class FakeStorage {
  readonly map = new Map<string, string>();
  readonly writes: string[] = [];
  readonly deletes: string[] = [];
  throwOnSet = false;

  getItem(key: string): string | null {
    return this.map.has(key) ? this.map.get(key)! : null;
  }

  setItem(key: string, value: string): void {
    if (this.throwOnSet) throw new Error('QuotaExceededError');
    this.writes.push(key);
    this.map.set(key, value);
  }

  removeItem(key: string): void {
    this.deletes.push(key);
    this.map.delete(key);
  }

  clear(): void {
    this.deletes.push('*');
    this.map.clear();
  }
}

/** Every legacy key the MOR-1076 inventory knows about, given a value. */
function seedLegacy(storage: FakeStorage): void {
  for (const route of LEGACY_KEY_ROUTING) storage.map.set(route.key, `legacy:${route.key}`);
  storage.map.set('rigplane:theme-user-choice', 'nord');
  storage.map.set('rigplane:theme', 'dracula');
  storage.map.set('rigplane-layout', 'amber-lcd');
}

function store(storage: FakeStorage, value: unknown): void {
  storage.map.set(WORKSPACE_STORAGE_KEY, JSON.stringify(value));
}

const V1 = { ...DEFAULT_WORKSPACE, theme: 'nord', layout: 'lcd-scope' };

describe('workspace load (MOR-1079)', () => {
  it('reads validated v1 from the stored key', () => {
    const storage = new FakeStorage();
    store(storage, V1);

    const { result, source } = loadWorkspace(storage);

    expect(source).toBe('stored');
    expect(result.outcome).toBe('ok');
    expect(result.workspace.theme).toBe('nord');
    expect(result.workspace.layout).toBe('lcd-scope');
  });

  it('runs the legacy migration once, through readLegacyWorkspace', () => {
    const storage = new FakeStorage();
    seedLegacy(storage);

    const { result, source } = loadWorkspace(storage);

    expect(source).toBe('migrated');
    // theme-user-choice wins over theme; `amber-lcd` is the MOR-1042 alias.
    expect(result.workspace.theme).toBe('nord');
    expect(result.workspace.layout).toBe('lcd-cockpit');
  });

  it('does NOT re-migrate once the sentinel is set, even with the workspace key cleared', () => {
    const storage = new FakeStorage();
    seedLegacy(storage);
    storage.map.set(WORKSPACE_MIGRATION_SENTINEL_KEY, '1');

    const { result, source } = loadWorkspace(storage);

    expect(source).toBe('absent');
    expect(result.outcome).toBe('ok');
    expect(result.workspace).toEqual(DEFAULT_WORKSPACE);
  });

  it('migration is idempotent: a persisted change survives a second load', () => {
    const storage = new FakeStorage();
    seedLegacy(storage);

    const first = loadWorkspace(storage);
    expect(persistWorkspace(storage, first.result)).toBe(true);
    expect(markWorkspaceMigrated(storage)).toBe(true);
    const changed = applyWorkspacePatch(first.result, { theme: 'gruvbox-dark' });
    persistWorkspace(storage, changed);

    const second = loadWorkspace(storage);
    expect(second.source).toBe('stored');
    expect(second.result.workspace.theme).toBe('gruvbox-dark');
  });

  it.each([
    ['unparsable JSON', '{{{'],
    ['a JSON scalar', '"nope"'],
    ['a JSON array', '[1,2,3]'],
    ['empty bytes', ''],
  ])('resets on invalid stored state: %s', (_label, raw) => {
    const storage = new FakeStorage();
    storage.map.set(WORKSPACE_STORAGE_KEY, raw);

    const { result } = loadWorkspace(storage);

    expect(result.outcome).toBe('reset');
    expect(result.workspace).toEqual(DEFAULT_WORKSPACE);
  });

  it('degrades to defaults when getItem itself throws', () => {
    const hostile = { getItem() { throw new Error('SecurityError'); }, setItem() {} };

    const { result } = loadWorkspace(hostile);

    // No stored key, no sentinel → the legacy reader path, which is itself
    // throw-tolerant, so this lands on defaults rather than blocking boot.
    expect(result.workspace).toEqual(DEFAULT_WORKSPACE);
  });
});

describe('workspace unknown future versions (MOR-1079)', () => {
  it('forward-reads inside the N=2 window and never downgrades on writeback', () => {
    const storage = new FakeStorage();
    store(storage, { ...DEFAULT_WORKSPACE, version: 3, theme: 'nord', futureField: { a: 1 } });

    const { result } = loadWorkspace(storage);
    expect(result.outcome).toBe('forward-read');
    expect(result.workspace.version).toBe(3);
    expect(result.preserved).toEqual({ futureField: { a: 1 } });

    const updated = applyWorkspacePatch(result, { density: 'compact' });
    expect(persistWorkspace(storage, updated)).toBe(true);

    const written = JSON.parse(storage.map.get(WORKSPACE_STORAGE_KEY)!) as Record<string, unknown>;
    expect(written.version).toBe(3);
    expect(written.futureField).toEqual({ a: 1 });
    expect(written.density).toBe('compact');
  });

  it('refuses to write over a newer object it could not fully represent', () => {
    const storage = new FakeStorage();
    store(storage, { ...DEFAULT_WORKSPACE, version: 3, theme: 'v3-only-theme' });
    const before = storage.map.get(WORKSPACE_STORAGE_KEY)!;

    const { result, writable } = loadWorkspace(storage);
    expect(result.outcome).toBe('forward-read');
    expect(result.rejections).toContainEqual({ field: 'theme', reason: 'unknown-id' });
    expect(canPersistWorkspace(result)).toBe(false);
    expect(writable).toBe(false);

    expect(persistWorkspace(storage, result)).toBe(false);
    expect(storage.map.get(WORKSPACE_STORAGE_KEY)).toBe(before);
  });

  it('the unrepresentable verdict belongs to the LOAD — a patched result looks clean', () => {
    const storage = new FakeStorage();
    store(storage, { ...DEFAULT_WORKSPACE, version: 3, theme: 'v3-only-theme' });

    const { result, writable } = loadWorkspace(storage);
    const patched = applyWorkspacePatch(result, { density: 'compact' });

    // The offending value was already repaired away, so re-deriving the verdict
    // per update would re-enable the write and destroy the newer theme. This is
    // exactly why `writable` is latched from the load by the store.
    expect(canPersistWorkspace(patched)).toBe(true);
    expect(writable).toBe(false);
  });

  it('a lossless forward read stays writable', () => {
    const storage = new FakeStorage();
    store(storage, { ...DEFAULT_WORKSPACE, version: 2, futureField: 7 });

    const { result, writable } = loadWorkspace(storage);

    expect(result.outcome).toBe('forward-read');
    expect(writable).toBe(true);
  });

  it.each([0, 4, 99, 1.5, 'two', null])('discards an unreadable version: %s', (version) => {
    const storage = new FakeStorage();
    store(storage, { ...DEFAULT_WORKSPACE, version, theme: 'nord' });

    const { result } = loadWorkspace(storage);

    expect(result.outcome).toBe('version-discarded');
    expect(result.workspace).toEqual(DEFAULT_WORKSPACE);
    if (result.outcome === 'version-discarded') expect(result.discardedVersion).toBe(version);
  });
});

describe('workspace write is atomic and single-key (MOR-1079)', () => {
  it('serializes first, then writes exactly one key', () => {
    const storage = new FakeStorage();

    expect(persistWorkspace(storage, applyWorkspacePatch(loadWorkspace(storage).result, { theme: 'nord' }))).toBe(true);

    expect(storage.writes).toEqual([WORKSPACE_STORAGE_KEY]);
  });

  it('a throwing setItem leaves the previous stored state byte-identical', () => {
    const storage = new FakeStorage();
    store(storage, V1);
    const before = storage.map.get(WORKSPACE_STORAGE_KEY)!;
    const { result } = loadWorkspace(storage);

    storage.throwOnSet = true;
    const next = applyWorkspacePatch(result, { theme: 'crt-green' });
    expect(persistWorkspace(storage, next)).toBe(false);

    expect(storage.map.get(WORKSPACE_STORAGE_KEY)).toBe(before);
    // The in-memory result is still a usable, fully validated workspace.
    expect(next.workspace.theme).toBe('crt-green');
  });

  it('the migration sentinel is only written after a successful workspace write', () => {
    const storage = new FakeStorage();
    seedLegacy(storage);
    storage.throwOnSet = true;

    const { result, source } = loadWorkspace(storage);
    expect(source).toBe('migrated');
    const wrote = persistWorkspace(storage, result);
    if (wrote) markWorkspaceMigrated(storage);

    expect(wrote).toBe(false);
    expect(storage.getItem(WORKSPACE_MIGRATION_SENTINEL_KEY)).toBeNull();
  });
});

describe('legacy data survives the compatibility window (MOR-1079)', () => {
  it('migration + updates never delete or rewrite a legacy key', () => {
    const storage = new FakeStorage();
    seedLegacy(storage);
    const legacyBefore = new Map(storage.map);

    const { result } = loadWorkspace(storage);
    persistWorkspace(storage, result);
    markWorkspaceMigrated(storage);
    let state = result;
    for (const theme of ['nord', 'dracula', 'crt-green'] as const) {
      state = applyWorkspacePatch(state, { theme });
      persistWorkspace(storage, state);
    }

    expect(storage.deletes).toEqual([]);
    expect(new Set(storage.writes)).toEqual(new Set([WORKSPACE_STORAGE_KEY, WORKSPACE_MIGRATION_SENTINEL_KEY]));
    for (const [key, value] of legacyBefore) expect(storage.getItem(key)).toBe(value);
  });
});

describe('every mutation goes through the validator (MOR-1079)', () => {
  it('an invalid patch value falls back instead of being stored', () => {
    const base = loadWorkspace(new FakeStorage()).result;

    const patched = applyWorkspacePatch(base, { theme: 'not-a-theme', layout: 'nonsense' });

    expect(patched.workspace.theme).toBe('default');
    expect(patched.workspace.layout).toBe('auto');
    expect(patched.rejections.map((r) => r.field).sort()).toEqual(['layout', 'theme']);
  });

  it('serializeWorkspace round-trips through readWorkspace unchanged', () => {
    const base = applyWorkspacePatch(loadWorkspace(new FakeStorage()).result, { theme: 'nord', density: 'compact' });
    const storage = new FakeStorage();
    persistWorkspace(storage, base);

    expect(loadWorkspace(storage).result.workspace).toEqual(base.workspace);
    expect(JSON.parse(storage.map.get(WORKSPACE_STORAGE_KEY)!)).toEqual(serializeWorkspace(base));
  });
});

// ── Source-scan gate: exactly one writer of the workspace key ───────────────
// Textual tripwire in the MOR-1247 `stage-sizing-boundary` idiom, not a
// security boundary: it catches the ordinary case (a module naming the key or
// calling setItem with it), not a deliberately obfuscated one.
const SRC_ROOT = 'src';
const SOURCE_EXTENSIONS = new Set(['.ts', '.svelte']);

function collectSourceFiles(dir: string): string[] {
  const out: string[] = [];
  for (const entry of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...collectSourceFiles(full));
    else if (SOURCE_EXTENSIONS.has(path.extname(entry.name))) out.push(full);
  }
  return out;
}

describe('the workspace key has exactly one writer in src/ (MOR-1079)', () => {
  const production = collectSourceFiles(SRC_ROOT).filter((f) => !f.includes('__tests__') && !f.endsWith('.test.ts'));

  it('no module outside repository.ts names the workspace storage keys', () => {
    const offenders = production
      .filter((file) => file !== path.join(SRC_ROOT, 'presentation', 'workspace', 'repository.ts'))
      .filter((file) => {
        const source = readFileSync(file, 'utf8');
        return source.includes(WORKSPACE_STORAGE_KEY) || source.includes(WORKSPACE_MIGRATION_SENTINEL_KEY);
      });

    expect(offenders).toEqual([]);
  });

  it('no module outside presentation/workspace/ calls setItem on a workspace key', () => {
    const zone = path.join(SRC_ROOT, 'presentation', 'workspace') + path.sep;
    const offenders = production
      .filter((file) => !file.startsWith(zone))
      .filter((file) => /setItem\s*\(\s*WORKSPACE_/.test(readFileSync(file, 'utf8')));

    expect(offenders).toEqual([]);
  });
});
