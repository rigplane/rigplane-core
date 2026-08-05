/**
 * MOR-1081 rewrote this store into a façade over the single workspace store,
 * so the persistence assertions moved with it: the layout selection is read
 * from and written to `rigplane:workspace`, never to `rigplane-layout` or
 * `rigplane-skin`. The pure `normalizeLayoutMode` contract (MOR-1042 aliases,
 * MOR-1257's QA-only exclusion) is unchanged and still pinned here.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';

const LAYOUT_KEY = 'rigplane-layout';
const LEGACY_SKIN_KEY = 'rigplane-skin';
const WORKSPACE_KEY = 'rigplane:workspace';
const MIGRATED_KEY = 'rigplane:workspace-migrated:v1';

function fakeStorage(seed: Record<string, string> = {}) {
  const data = new Map(Object.entries(seed));
  return {
    data,
    storage: {
      getItem: (k: string) => data.get(k) ?? null,
      setItem: (k: string, v: string) => { data.set(k, v); },
    },
  };
}

async function loadStore(seed: Record<string, string> = {}) {
  vi.resetModules();
  const store = await import('../layout.svelte');
  const workspace = await import('../../../presentation/workspace/store.svelte');
  const backing = fakeStorage(seed);
  workspace.initWorkspaceStore(backing.storage);
  return { ...store, ...backing };
}

/** The layout id as it is actually stored, not as the store reports it. */
function persistedLayout(data: Map<string, string>): unknown {
  return JSON.parse(data.get(WORKSPACE_KEY) ?? '{}').layout;
}

afterEach(() => {
  vi.resetModules();
});

describe('layout preference normalization', () => {
  it.each([
    ['lcd', 'lcd-cockpit'],
    ['amber-lcd', 'lcd-cockpit'],
    ['spectrum', 'standard'],
    ['desktop-v2', 'standard'],
    ['auto', 'auto'],
    ['lcd-cockpit', 'lcd-cockpit'],
    ['lcd-scope', 'lcd-scope'],
    ['standard', 'standard'],
    ['sdr-test', 'sdr-test'],
    ['unknown', 'auto'],
    ['toString', 'auto'],
    [null, 'auto'],
  ] as const)('normalizes %s to %s', async (input, expected) => {
    const { normalizeLayoutMode } = await loadStore();
    expect(normalizeLayoutMode(input)).toBe(expected);
  });

  it('restores the layout preference from the workspace object', async () => {
    const { getLayoutMode } = await loadStore({
      [WORKSPACE_KEY]: JSON.stringify({ version: 1, layout: 'lcd-scope' }),
      [MIGRATED_KEY]: '1',
      [LAYOUT_KEY]: 'standard',
    });

    expect(getLayoutMode()).toBe('lcd-scope');
  });

  it('folds a legacy layout key in on the one-time migration, aliases included', async () => {
    const { getLayoutMode } = await loadStore({ [LAYOUT_KEY]: 'amber-lcd' });

    expect(getLayoutMode()).toBe('lcd-cockpit');
  });

  // MOR-1078 routed `rigplane-skin` to `retire` (dead write path), so adoption
  // deliberately drops the pre-#889 fallback rather than migrating it. Kill-test:
  // adding the key back to the migrate set would make this resolve to lcd-cockpit.
  it('does not resurrect the retired rigplane-skin fallback', async () => {
    const { getLayoutMode } = await loadStore({ [LEGACY_SKIN_KEY]: 'amber-lcd' });

    expect(getLayoutMode()).toBe('auto');
  });

  it('persists canonical values to the workspace, never to the legacy key', async () => {
    const { getLayoutMode, setLayoutMode, data } = await loadStore({
      [WORKSPACE_KEY]: JSON.stringify({ version: 1 }),
      [MIGRATED_KEY]: '1',
      [LAYOUT_KEY]: 'auto',
    });

    setLayoutMode('lcd');

    expect(getLayoutMode()).toBe('lcd-cockpit');
    expect(persistedLayout(data)).toBe('lcd-cockpit');
    expect(data.get(LAYOUT_KEY)).toBe('auto');
  });

  it('preserves the visible cycle and no-scope fallback using canonical values', async () => {
    const { cycleLayoutMode, getLayoutMode } = await loadStore();

    cycleLayoutMode(true);
    expect(getLayoutMode()).toBe('lcd-cockpit');
    cycleLayoutMode(true);
    expect(getLayoutMode()).toBe('standard');
    cycleLayoutMode(true);
    expect(getLayoutMode()).toBe('auto');

    cycleLayoutMode(false);
    expect(getLayoutMode()).toBe('lcd-cockpit');
  });

  // MOR-1257: the QA-only dual-receiver-cockpit value must never become a
  // normal, persisted layout preference — the only legitimate way to reach
  // it is `readQaCockpitLayoutOverride()` (lib/stores/qa-cockpit-override.ts)
  // reading the exact query param. Kill-test: dropping the
  // `CanonicalLayoutMode` exclusion (or adding the value to
  // `CANONICAL_LAYOUT_MODES`) would make this normalize to itself instead of
  // falling through to 'auto', and it would then survive setLayoutMode /
  // the workspace like any other forced preference.
  it('does not let the QA-only dual-receiver-cockpit value normalize to itself', async () => {
    const { normalizeLayoutMode } = await loadStore();
    expect(normalizeLayoutMode('dual-receiver-cockpit')).toBe('auto');
  });

  it('does not let setLayoutMode persist the QA-only dual-receiver-cockpit value', async () => {
    const { getLayoutMode, setLayoutMode, data } = await loadStore({
      [WORKSPACE_KEY]: JSON.stringify({ version: 1 }),
      [MIGRATED_KEY]: '1',
    });

    setLayoutMode('dual-receiver-cockpit');

    expect(getLayoutMode()).toBe('auto');
    expect(persistedLayout(data)).toBe('auto');
  });
});
