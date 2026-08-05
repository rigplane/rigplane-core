/**
 * MOR-1078 — legacy workspace reader behaviour: routing-table completeness,
 * the fixture matrix (full/legacy-alias/partial/corrupt/empty/mixed-
 * manufacturer snapshots), determinism, and idempotence. Fast pool: no
 * globals stubbed, no modules mocked (MOR-1272) — the read-only/purity pins
 * live in `legacy-readers-purity.isolated.test.ts` instead.
 */
import { describe, it, expect } from 'vitest';
import {
  LEGACY_KEY_ROUTING, LEGACY_MIGRATE_KEYS, buildWorkspaceInput, classifyLegacyKey,
  readLegacyWorkspace, readLegacyWorkspaceFromStorage, snapshotLegacyStorage,
  type LegacyStorageSnapshot,
} from '../legacy-readers';
import { DEFAULT_WORKSPACE } from '../contract';

const MANUFACTURER_PREFIXED = /^(icom|yaesu|kenwood|elecraft|xiegu|alinco)[.:-]/i;

function snap(overrides: Record<string, string | null>): LegacyStorageSnapshot {
  const base: Record<string, string | null> = Object.fromEntries(LEGACY_MIGRATE_KEYS.map((k) => [k, null]));
  return { ...base, ...overrides };
}

describe('routing table — all 29 MOR-1076 evidence keys accounted for', () => {
  it('has no duplicate keys and a disposition for every row', () => {
    const keys = LEGACY_KEY_ROUTING.map((r) => r.key);
    expect(new Set(keys).size).toBe(keys.length);
    expect(keys.length).toBe(29);
  });

  it('classifies each routed key and returns undefined for an unknown one', () => {
    for (const route of LEGACY_KEY_ROUTING) expect(classifyLegacyKey(route.key)).toBe(route.disposition);
    expect(classifyLegacyKey('not-a-real-key')).toBeUndefined();
  });

  it('the migrate set is exactly the theme + layout sources, all classified migrate', () => {
    expect([...LEGACY_MIGRATE_KEYS].sort()).toEqual(
      ['rigplane-layout', 'rigplane:theme', 'rigplane:theme-user-choice'].sort(),
    );
    for (const key of LEGACY_MIGRATE_KEYS) expect(classifyLegacyKey(key)).toBe('migrate');
  });

  it('no manufacturer-prefixed key is ever in the migrate set (carry-forward guarantee)', () => {
    expect(LEGACY_MIGRATE_KEYS.some((k) => MANUFACTURER_PREFIXED.test(k))).toBe(false);
    const manufacturerKeys = LEGACY_KEY_ROUTING.filter((r) => MANUFACTURER_PREFIXED.test(r.key));
    expect(manufacturerKeys.length).toBeGreaterThan(0);
    for (const route of manufacturerKeys) expect(route.disposition).toBe('retain-outside');
  });

  it('exactly one key is forbidden and it is the auth token', () => {
    const forbidden = LEGACY_KEY_ROUTING.filter((r) => r.disposition === 'forbidden');
    expect(forbidden.map((r) => r.key)).toEqual(['rigplane-auth-token']);
  });
});

describe('fixture matrix — deterministic mapping to workspace v1', () => {
  it('full modern set: explicit user theme choice wins over the applied-theme fallback', () => {
    const result = readLegacyWorkspace(snap({
      'rigplane:theme-user-choice': 'dracula', 'rigplane:theme': 'default', 'rigplane-layout': 'lcd-cockpit',
    }));
    expect(result.outcome).toBe('ok');
    expect(result.workspace.theme).toBe('dracula');
    expect(result.workspace.layout).toBe('lcd-cockpit');
  });

  it('legacy-alias set: MOR-1042 layout alias normalizes, theme falls back to the applied key', () => {
    const result = readLegacyWorkspace(snap({ 'rigplane:theme': 'nord', 'rigplane-layout': 'amber-lcd' }));
    expect(result.outcome).toBe('ok');
    expect(result.workspace.theme).toBe('nord');
    expect(result.workspace.layout).toBe('lcd-cockpit');
  });

  it('partial set: only layout present, theme defaults', () => {
    const result = readLegacyWorkspace(snap({ 'rigplane-layout': 'sdr-test' }));
    expect(result.outcome).toBe('ok');
    expect(result.workspace.layout).toBe('sdr-test');
    expect(result.workspace.theme).toBe(DEFAULT_WORKSPACE.theme);
  });

  it('corrupt values: an unrecognized theme/layout id falls back per-field, never throws', () => {
    const result = readLegacyWorkspace(snap({ 'rigplane:theme': 'not-a-real-theme', 'rigplane-layout': 'not-a-real-layout' }));
    expect(result.outcome).toBe('repaired');
    expect(result.workspace).toEqual(DEFAULT_WORKSPACE);
    expect(result.rejections).toContainEqual({ field: 'theme', reason: 'unknown-id' });
    expect(result.rejections).toContainEqual({ field: 'layout', reason: 'unknown-id' });
  });

  it('empty snapshot: every migrate key absent yields the untouched default workspace', () => {
    const result = readLegacyWorkspace(snap({}));
    expect(result.outcome).toBe('ok');
    expect(result.workspace).toEqual(DEFAULT_WORKSPACE);
    expect(result.rejections).toEqual([]);
  });

  it('a corrupt/throwing storage degrades to "absent" instead of propagating', () => {
    const throwing: Pick<Storage, 'getItem'> = {
      getItem() { throw new Error('sandboxed storage'); },
    };
    expect(() => snapshotLegacyStorage(throwing)).not.toThrow();
    const result = readLegacyWorkspaceFromStorage(throwing);
    expect(result.outcome).toBe('ok');
    expect(result.workspace).toEqual(DEFAULT_WORKSPACE);
  });

  it('mixed manufacturer keys in the snapshot never influence the result or reach the validator', () => {
    const withExtras = {
      ...snap({ 'rigplane:theme': 'nord', 'rigplane-layout': 'lcd-scope' }),
      'icom.audio.focus': 'main',
      'icom.audio.main_gain_db': '-6',
    };
    const result = readLegacyWorkspace(withExtras);
    expect(result.workspace.theme).toBe('nord');
    expect(result.workspace.layout).toBe('lcd-scope');
    expect(result.preserved).toEqual({});
    expect(result.rejections).toEqual([]);
    expect(buildWorkspaceInput(withExtras)).not.toHaveProperty('icom.audio.focus');
    expect(buildWorkspaceInput(withExtras)).not.toHaveProperty('icom.audio.main_gain_db');
  });
});

describe('determinism and idempotence', () => {
  const SNAPSHOT = snap({ 'rigplane:theme-user-choice': 'gruvbox-dark', 'rigplane-layout': 'lcd-scope' });

  it('the same snapshot in yields a byte-stable workspace out, repeatedly', () => {
    const results = Array.from({ length: 5 }, () => JSON.stringify(readLegacyWorkspace(SNAPSHOT)));
    expect(new Set(results).size).toBe(1);
  });

  it('reading twice never mutates the input snapshot', () => {
    const before = { ...SNAPSHOT };
    readLegacyWorkspace(SNAPSHOT);
    readLegacyWorkspace(SNAPSHOT);
    expect(SNAPSHOT).toEqual(before);
  });

  it('re-reading an already-migrated state (legacy keys untouched — read-only) is a fixed point', () => {
    const first = readLegacyWorkspace(SNAPSHOT);
    // The reader never deletes/writes the legacy keys (MOR-1078 constraint), so a
    // second boot sees the identical snapshot and must reproduce the identical result.
    const second = readLegacyWorkspace(SNAPSHOT);
    expect(second).toEqual(first);
  });

  it('storage is read through getItem only, exactly the enumerated migrate-key closure', () => {
    const calls: string[] = [];
    const storage: Pick<Storage, 'getItem'> = {
      getItem(key: string) { calls.push(key); return null; },
    };
    snapshotLegacyStorage(storage);
    expect([...calls].sort()).toEqual([...LEGACY_MIGRATE_KEYS].sort());
  });

  // F1 (MOR-1078 verify report, mutant D1): theme precedence must be fixed by
  // THEME_SOURCE_KEYS, never by the snapshot object's own key insertion order.
  // readLegacyWorkspace is a public export MOR-1079 will call with a snapshot
  // it constructs itself, so this cannot be left to accident.
  it('resolves the same theme regardless of the snapshot object\'s own key order', () => {
    const userChoiceFirst = { 'rigplane:theme-user-choice': 'nord', 'rigplane:theme': 'dracula', 'rigplane-layout': null };
    const appliedThemeFirst = { 'rigplane-layout': null, 'rigplane:theme': 'dracula', 'rigplane:theme-user-choice': 'nord' };
    // Sanity: the two literals really do have different key insertion order —
    // otherwise this test would pass for the wrong reason.
    expect(Object.keys(userChoiceFirst)).not.toEqual(Object.keys(appliedThemeFirst));

    expect(readLegacyWorkspace(userChoiceFirst).workspace.theme).toBe('nord');
    expect(readLegacyWorkspace(appliedThemeFirst).workspace.theme).toBe('nord');
  });

  it('two key orders of the same content produce an identical workspace, including under theme conflict', () => {
    const orderA = { 'rigplane:theme-user-choice': 'nord', 'rigplane:theme': 'dracula', 'rigplane-layout': 'lcd-scope' };
    const orderB = { 'rigplane-layout': 'lcd-scope', 'rigplane:theme': 'dracula', 'rigplane:theme-user-choice': 'nord' };
    expect(Object.keys(orderA)).not.toEqual(Object.keys(orderB));
    expect(readLegacyWorkspace(orderA)).toEqual(readLegacyWorkspace(orderB));
  });
});
