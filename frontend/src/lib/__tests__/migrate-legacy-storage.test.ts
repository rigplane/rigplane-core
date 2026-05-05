import { describe, it, expect, beforeEach } from 'vitest';

import {
  __resetMigrationStateForTests,
  migrateLegacyStorage,
} from '../migrate-legacy-storage';

const SENTINEL_KEY = 'rigplane:storage-migrated-from-icom-lan';

const localStorageMock = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: (k: string): string | null => (k in store ? store[k] : null),
    setItem: (k: string, v: string): void => {
      store[k] = v;
    },
    removeItem: (k: string): void => {
      delete store[k];
    },
    clear: (): void => {
      store = {};
    },
    key: (i: number): string | null => Object.keys(store)[i] ?? null,
    get length(): number {
      return Object.keys(store).length;
    },
    _dump: (): Record<string, string> => ({ ...store }),
  };
})();

Object.defineProperty(globalThis, 'localStorage', {
  value: localStorageMock,
  writable: true,
});

describe('migrateLegacyStorage', () => {
  beforeEach(() => {
    localStorageMock.clear();
    __resetMigrationStateForTests();
  });

  it('copies a hyphenated legacy key to its new key and removes the legacy entry', () => {
    localStorageMock.setItem('icom-lan-auth-token', 'tok-abc');

    migrateLegacyStorage();

    expect(localStorageMock.getItem('rigplane-auth-token')).toBe('tok-abc');
    expect(localStorageMock.getItem('icom-lan-auth-token')).toBeNull();
    expect(localStorageMock.getItem(SENTINEL_KEY)).toBe('1');
  });

  it('copies a colon-namespaced legacy key to its new key', () => {
    localStorageMock.setItem('icom-lan:theme', 'amber-lcd');
    localStorageMock.setItem('icom-lan:theme-user-choice', 'amber-lcd');
    localStorageMock.setItem('icom-lan:vfo-theme', 'green');

    migrateLegacyStorage();

    expect(localStorageMock.getItem('rigplane:theme')).toBe('amber-lcd');
    expect(localStorageMock.getItem('rigplane:theme-user-choice')).toBe('amber-lcd');
    expect(localStorageMock.getItem('rigplane:vfo-theme')).toBe('green');
    expect(localStorageMock.getItem('icom-lan:theme')).toBeNull();
    expect(localStorageMock.getItem('icom-lan:theme-user-choice')).toBeNull();
    expect(localStorageMock.getItem('icom-lan:vfo-theme')).toBeNull();
  });

  it('migrates dot-namespaced tuning-step keys to their rigplane counterparts', () => {
    localStorageMock.setItem('icom-lan.tuning-step-hz', '5000');
    localStorageMock.setItem('icom-lan.tuning-step-auto', 'false');

    migrateLegacyStorage();

    expect(localStorageMock.getItem('rigplane.tuning-step-hz')).toBe('5000');
    expect(localStorageMock.getItem('rigplane.tuning-step-auto')).toBe('false');
    expect(localStorageMock.getItem('icom-lan.tuning-step-hz')).toBeNull();
    expect(localStorageMock.getItem('icom-lan.tuning-step-auto')).toBeNull();
  });

  it('migrates the dock-layout key with the v1 suffix preserved', () => {
    const payload = JSON.stringify({ version: 1, extensions: {} });
    localStorageMock.setItem('icom-lan:local-extension-dock-layout:v1', payload);

    migrateLegacyStorage();

    expect(localStorageMock.getItem('rigplane:local-extension-dock-layout:v1')).toBe(payload);
    expect(localStorageMock.getItem('icom-lan:local-extension-dock-layout:v1')).toBeNull();
  });

  it('is a no-op when sentinel is already set', () => {
    localStorageMock.setItem(SENTINEL_KEY, '1');
    localStorageMock.setItem('icom-lan-auth-token', 'should-not-migrate');

    migrateLegacyStorage();

    // Already-migrated sentinel means we don't touch the legacy key.
    expect(localStorageMock.getItem('icom-lan-auth-token')).toBe('should-not-migrate');
    expect(localStorageMock.getItem('rigplane-auth-token')).toBeNull();
  });

  it('does not overwrite existing new-key data when both legacy and new are present', () => {
    localStorageMock.setItem('icom-lan-auth-token', 'old-token');
    localStorageMock.setItem('rigplane-auth-token', 'new-token-already-set');

    migrateLegacyStorage();

    expect(localStorageMock.getItem('rigplane-auth-token')).toBe('new-token-already-set');
    // Legacy still removed so it can't drift.
    expect(localStorageMock.getItem('icom-lan-auth-token')).toBeNull();
  });

  it('repeated calls in the same session do not re-do work', () => {
    localStorageMock.setItem('icom-lan-layout', 'standard');

    migrateLegacyStorage();
    expect(localStorageMock.getItem('rigplane-layout')).toBe('standard');

    // Simulate user setting the new key, then calling migrate again.
    localStorageMock.setItem('rigplane-layout', 'lcd');
    migrateLegacyStorage();

    expect(localStorageMock.getItem('rigplane-layout')).toBe('lcd');
  });

  it('handles missing legacy keys gracefully (no errors, sentinel still set)', () => {
    migrateLegacyStorage();

    expect(localStorageMock.getItem(SENTINEL_KEY)).toBe('1');
  });

  it('migrates a representative sample of all key categories at once', () => {
    localStorageMock.setItem('icom-lan-auth-token', 'tok');
    localStorageMock.setItem('icom-lan-layout', 'lcd');
    localStorageMock.setItem('icom-lan-lcd-display-mode', 'dim');
    localStorageMock.setItem('icom-lan-hidden-layers', '[]');
    localStorageMock.setItem('icom-lan:panel-collapsed', '{}');
    localStorageMock.setItem('icom-lan:panel-order', '[]');
    localStorageMock.setItem('icom-lan:right-panel-order', '[]');
    localStorageMock.setItem('icom-lan:install-dismissed', 'true');
    localStorageMock.setItem('icom-lan:memory-channels', '[]');
    localStorageMock.setItem('icom-lan:lcd-contrast', '0.5');

    migrateLegacyStorage();

    expect(localStorageMock.getItem('rigplane-auth-token')).toBe('tok');
    expect(localStorageMock.getItem('rigplane-layout')).toBe('lcd');
    expect(localStorageMock.getItem('rigplane-lcd-display-mode')).toBe('dim');
    expect(localStorageMock.getItem('rigplane-hidden-layers')).toBe('[]');
    expect(localStorageMock.getItem('rigplane:panel-collapsed')).toBe('{}');
    expect(localStorageMock.getItem('rigplane:panel-order')).toBe('[]');
    expect(localStorageMock.getItem('rigplane:right-panel-order')).toBe('[]');
    expect(localStorageMock.getItem('rigplane:install-dismissed')).toBe('true');
    expect(localStorageMock.getItem('rigplane:memory-channels')).toBe('[]');
    expect(localStorageMock.getItem('rigplane:lcd-contrast')).toBe('0.5');

    // All legacy keys cleared.
    const dump = localStorageMock._dump();
    for (const k of Object.keys(dump)) {
      expect(k.startsWith('icom-lan')).toBe(false);
    }
  });
});
