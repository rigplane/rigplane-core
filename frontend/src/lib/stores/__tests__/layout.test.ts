import { beforeEach, describe, expect, it, vi } from 'vitest';

const LAYOUT_KEY = 'rigplane-layout';
const LEGACY_SKIN_KEY = 'rigplane-skin';

async function loadStore() {
  vi.resetModules();
  return import('../layout.svelte');
}

describe('layout preference normalization', () => {
  beforeEach(() => {
    localStorage.clear();
  });

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

  it('restores the primary layout preference before the legacy skin fallback', async () => {
    localStorage.setItem(LAYOUT_KEY, 'lcd-scope');
    localStorage.setItem(LEGACY_SKIN_KEY, 'amber-lcd');

    const { getLayoutMode } = await loadStore();

    expect(getLayoutMode()).toBe('lcd-scope');
  });

  it('restores a migrated legacy skin when no layout preference exists', async () => {
    localStorage.setItem(LEGACY_SKIN_KEY, 'amber-lcd');

    const { getLayoutMode } = await loadStore();

    expect(getLayoutMode()).toBe('lcd-cockpit');
  });

  it('persists canonical values for legacy callers', async () => {
    const { getLayoutMode, setLayoutMode } = await loadStore();

    setLayoutMode('lcd');

    expect(getLayoutMode()).toBe('lcd-cockpit');
    expect(localStorage.getItem(LAYOUT_KEY)).toBe('lcd-cockpit');
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
});
