/**
 * MOR-1079 — the Svelte-facing store: semantic actions, the visible-discard
 * notice signal, and the storage behaviours as observed through the store
 * rather than the repository.
 *
 * Storage is INJECTED (`initWorkspaceStore(fake)`), so no global is stubbed
 * and this file stays in the fast pool. The module-load purity spy pin lives
 * in the sibling `store-purity.test.ts`, which is registered in the isolated
 * pool (MOR-1272).
 */
import { beforeEach, describe, expect, it } from 'vitest';
import {
  WORKSPACE_MIGRATION_SENTINEL_KEY, WORKSPACE_STORAGE_KEY,
} from '../repository';
import { DEFAULT_WORKSPACE } from '../contract';
import {
  dismissWorkspaceNotice, getWorkspace, getWorkspaceNotice, initWorkspaceStore,
  setDensity, setDesignLanguage, setLayout, setPinnedCommands, setTheme,
  setZoneOrder, setZoneVisibleSurfaces,
} from '../store.svelte';

class FakeStorage {
  readonly map = new Map<string, string>();
  readonly deletes: string[] = [];
  throwOnSet = false;

  getItem(key: string): string | null {
    return this.map.has(key) ? this.map.get(key)! : null;
  }

  setItem(key: string, value: string): void {
    if (this.throwOnSet) throw new Error('QuotaExceededError');
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

function stored(storage: FakeStorage): Record<string, unknown> {
  return JSON.parse(storage.map.get(WORKSPACE_STORAGE_KEY)!) as Record<string, unknown>;
}

let storage: FakeStorage;

beforeEach(() => {
  storage = new FakeStorage();
});

describe('workspace store boot (MOR-1079)', () => {
  it('with no storage it stays on defaults and raises no notice', () => {
    initWorkspaceStore(null);

    expect(getWorkspace()).toEqual(DEFAULT_WORKSPACE);
    expect(getWorkspaceNotice()).toBeNull();
  });

  it('migrates legacy keys once and marks the sentinel', () => {
    storage.map.set('rigplane:theme-user-choice', 'tokyo-night');
    storage.map.set('rigplane-layout', 'desktop-v2');

    initWorkspaceStore(storage);

    expect(getWorkspace().theme).toBe('tokyo-night');
    expect(getWorkspace().layout).toBe('standard');
    expect(storage.getItem(WORKSPACE_MIGRATION_SENTINEL_KEY)).toBe('1');
    expect(stored(storage).theme).toBe('tokyo-night');
  });

  it('re-running init does not re-apply the migration over a later change', () => {
    storage.map.set('rigplane:theme-user-choice', 'tokyo-night');
    initWorkspaceStore(storage);
    setTheme('nord');

    initWorkspaceStore(storage);

    expect(getWorkspace().theme).toBe('nord');
    // The legacy key is untouched — still readable for a rollback.
    expect(storage.getItem('rigplane:theme-user-choice')).toBe('tokyo-night');
  });

  it('reloads persisted state across a fresh init', () => {
    initWorkspaceStore(storage);
    setTheme('gruvbox-dark');
    setLayout('lcd-scope');
    setPinnedCommands(['set_compressor', 'set_compressor', 'toggle_vox']);

    initWorkspaceStore(new FakeStorageFrom(storage));

    expect(getWorkspace().theme).toBe('gruvbox-dark');
    expect(getWorkspace().layout).toBe('lcd-scope');
    expect(getWorkspace().pinnedCommands).toEqual(['set_compressor', 'toggle_vox']);
  });

  /**
   * The sentinel-ordering pin, at STORE level — it drives the real
   * `initWorkspaceStore`, because the ordering being pinned is a production
   * line and a test that re-implements `if (wrote) markWorkspaceMigrated(...)`
   * in its own body only verifies itself.
   *
   * It also needs a SIZE-SENSITIVE quota: a storage that throws on every
   * `setItem` hides the bug, because the sentinel write fails too. Here the
   * 1-byte sentinel fits and the larger workspace payload does not, which is
   * the realistic quota shape. Write the sentinel first and run 2 comes back
   * `auto`/`default` with the legacy keys still in storage but never read
   * again — permanent, silent loss of the operator's layout and theme.
   */
  it('a failed migration write leaves no sentinel, so the next boot still recovers the legacy data', () => {
    const quota = new LengthLimitedStorage(8);
    quota.map.set('rigplane:theme-user-choice', 'nord');
    quota.map.set('rigplane-layout', 'amber-lcd');

    initWorkspaceStore(quota);

    expect(quota.getItem(WORKSPACE_STORAGE_KEY)).toBeNull();
    expect(quota.getItem(WORKSPACE_MIGRATION_SENTINEL_KEY)).toBeNull();

    // Same storage, next boot: the migration must run again, not be skipped.
    quota.limit = Number.MAX_SAFE_INTEGER;
    initWorkspaceStore(quota);

    expect(getWorkspace().layout).toBe('lcd-cockpit');
    expect(getWorkspace().theme).toBe('nord');
    expect(quota.getItem(WORKSPACE_MIGRATION_SENTINEL_KEY)).toBe('1');
  });
});

/** A second storage instance holding the same bytes — a page reload, not a mutation. */
class FakeStorageFrom extends FakeStorage {
  constructor(source: FakeStorage) {
    super();
    for (const [k, v] of source.map) this.map.set(k, v);
  }
}

/** Quota that depends on VALUE SIZE, not on the call: the short sentinel gets
 *  through where the workspace payload does not. */
class LengthLimitedStorage extends FakeStorage {
  constructor(public limit: number) {
    super();
  }

  override setItem(key: string, value: string): void {
    if (value.length > this.limit) throw new Error('QuotaExceededError');
    super.setItem(key, value);
  }
}

describe('the discard signal is surfaced, never swallowed (MOR-1079)', () => {
  it('publishes version-discarded with the offending version', () => {
    storage.map.set(WORKSPACE_STORAGE_KEY, JSON.stringify({ ...DEFAULT_WORKSPACE, version: 9, theme: 'nord' }));

    initWorkspaceStore(storage);

    expect(getWorkspace()).toEqual(DEFAULT_WORKSPACE);
    expect(getWorkspaceNotice()).toEqual({
      kind: 'version-discarded', discardedVersion: 9, rejections: [{ field: 'version', reason: 'malformed' }],
    });
  });

  it('does not overwrite the discarded bytes until the operator changes something', () => {
    const raw = JSON.stringify({ ...DEFAULT_WORKSPACE, version: 9, theme: 'nord' });
    storage.map.set(WORKSPACE_STORAGE_KEY, raw);

    initWorkspaceStore(storage);

    expect(storage.map.get(WORKSPACE_STORAGE_KEY)).toBe(raw);
  });

  it('publishes reset for unusable bytes and repaired for a partial recovery', () => {
    storage.map.set(WORKSPACE_STORAGE_KEY, '{{{');
    initWorkspaceStore(storage);
    expect(getWorkspaceNotice()?.kind).toBe('reset');

    const other = new FakeStorage();
    other.map.set(WORKSPACE_STORAGE_KEY, JSON.stringify({ version: 1, theme: 'no-such-theme' }));
    initWorkspaceStore(other);
    expect(getWorkspaceNotice()?.kind).toBe('repaired');
    expect(getWorkspaceNotice()?.rejections).toContainEqual({ field: 'theme', reason: 'unknown-id' });
  });

  it('is dismissable and does not come back on a clean update', () => {
    storage.map.set(WORKSPACE_STORAGE_KEY, '{{{');
    initWorkspaceStore(storage);
    dismissWorkspaceNotice();

    setTheme('nord');

    expect(getWorkspaceNotice()).toBeNull();
    expect(getWorkspace().theme).toBe('nord');
  });

  it('publishes persist-failed and keeps the in-memory state functional', () => {
    initWorkspaceStore(storage);
    setTheme('nord');
    const before = storage.map.get(WORKSPACE_STORAGE_KEY)!;
    storage.throwOnSet = true;

    setTheme('crt-green');

    expect(getWorkspaceNotice()?.kind).toBe('persist-failed');
    expect(getWorkspace().theme).toBe('crt-green');
    expect(storage.map.get(WORKSPACE_STORAGE_KEY)).toBe(before);
    // Still usable after the failure.
    storage.throwOnSet = false;
    setLayout('sdr-test');
    expect(getWorkspace().layout).toBe('sdr-test');
    expect(stored(storage).layout).toBe('sdr-test');
  });
});

describe('forward-read objects are never downgraded (MOR-1079)', () => {
  it('keeps the newer version and its unknown fields across an unrelated update', () => {
    storage.map.set(WORKSPACE_STORAGE_KEY, JSON.stringify({
      ...DEFAULT_WORKSPACE, version: 3, theme: 'nord', futurePanelBudget: { rows: 4 },
    }));
    initWorkspaceStore(storage);

    setDensity('compact');

    expect(stored(storage).version).toBe(3);
    expect(stored(storage).futurePanelBudget).toEqual({ rows: 4 });
    expect(stored(storage).density).toBe('compact');
    expect(stored(storage).theme).toBe('nord');
  });

  it('refuses to write at all when the newer object was only partly representable', () => {
    const raw = JSON.stringify({ ...DEFAULT_WORKSPACE, version: 3, theme: 'v3-only-theme' });
    storage.map.set(WORKSPACE_STORAGE_KEY, raw);
    initWorkspaceStore(storage);
    expect(getWorkspaceNotice()?.kind).toBe('forward-read-only');

    setDensity('compact');

    expect(storage.map.get(WORKSPACE_STORAGE_KEY)).toBe(raw);
    expect(getWorkspaceNotice()?.kind).toBe('forward-read-only');
  });
});

describe('semantic actions go through the validator (MOR-1079)', () => {
  beforeEach(() => {
    initWorkspaceStore(storage);
  });

  it('applies each typed field', () => {
    setLayout('lcd-cockpit');
    setDesignLanguage('fieldline');
    setTheme('lcd-warm');
    setZoneVisibleSurfaces('rx-tx', ['rxTx']);
    setZoneOrder('main', ['vfo', 'meters']);
    setPinnedCommands(['set_compressor']);

    expect(getWorkspace()).toMatchObject({
      layout: 'lcd-cockpit', designLanguage: 'fieldline', theme: 'lcd-warm',
      visibleSurfaces: { 'rx-tx': ['rxTx'] }, zoneOrder: { main: ['vfo', 'meters'] },
      pinnedCommands: ['set_compressor'],
    });
    expect(stored(storage).theme).toBe('lcd-warm');
  });

  it('re-clamps density when the design language changes under it', () => {
    setDensity('dense');
    expect(getWorkspace().density).toBe('dense');

    setDesignLanguage('fieldline');

    expect(getWorkspace().density).toBe('comfortable');
    expect(getWorkspaceNotice()).toEqual({
      kind: 'repaired', rejections: [{ field: 'density', reason: 'out-of-clamp' }],
    });
    expect(stored(storage).density).toBe('comfortable');
  });

  it('rejects a cross-zone duplicate rather than persisting it', () => {
    setZoneOrder('main', ['vfo']);
    setZoneOrder('rx-tx', ['vfo']);

    expect(getWorkspace().zoneOrder).toEqual({ main: ['vfo'], 'rx-tx': [] });
    expect(getWorkspaceNotice()?.rejections).toContainEqual({ field: 'zoneOrder.rx-tx', reason: 'cross-zone' });
  });

  it('drops a forbidden pinned command instead of storing it', () => {
    setPinnedCommands(['set_compressor', 'tx_power_limit']);

    expect(getWorkspace().pinnedCommands).toEqual(['set_compressor']);
    expect(stored(storage).pinnedCommands).toEqual(['set_compressor']);
  });

  it('never deletes a legacy key across a long update sequence', () => {
    storage.map.set('rigplane:theme', 'dracula');
    storage.map.set('rigplane-layout', 'amber-lcd');
    for (const theme of ['nord', 'ayu-dark', 'crt-green'] as const) setTheme(theme);

    expect(storage.deletes).toEqual([]);
    expect(storage.getItem('rigplane:theme')).toBe('dracula');
    expect(storage.getItem('rigplane-layout')).toBe('amber-lcd');
  });
});
