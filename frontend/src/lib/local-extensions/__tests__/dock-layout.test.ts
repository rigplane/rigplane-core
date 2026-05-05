import { describe, expect, it, vi } from 'vitest';

import {
  LOCAL_EXTENSION_DOCK_LAYOUT_STORAGE_KEY,
  getLocalExtensionDockMode,
  loadLocalExtensionDockLayout,
  normalizeLocalExtensionDockLayout,
  saveLocalExtensionDockLayout,
  setLocalExtensionDockMode,
  type LocalExtensionDockStorage,
} from '../dock-layout';

function memoryStorage(initial = new Map<string, string>()): LocalExtensionDockStorage {
  return {
    getItem: (key) => initial.get(key) ?? null,
    setItem: (key, value) => {
      initial.set(key, value);
    },
  };
}

describe('local extension dock layout', () => {
  it('loads a default floating layout when storage is empty or invalid', () => {
    expect(loadLocalExtensionDockLayout(null)).toEqual({
      version: 1,
      extensions: {},
    });
    expect(normalizeLocalExtensionDockLayout({ version: 2, extensions: {} })).toEqual({
      version: 1,
      extensions: {},
    });
    expect(getLocalExtensionDockMode(loadLocalExtensionDockLayout(null), 'meter')).toBe('floating');
  });

  it('validates persisted extension placement state', () => {
    const state = normalizeLocalExtensionDockLayout({
      version: 1,
      extensions: {
        meter: { mode: 'dock-right' },
        '': { mode: 'dock-left' },
        badMode: { mode: 'popup' },
        badShape: 'dock-bottom',
        keyer: { mode: 'collapsed' },
      },
    });

    expect(state).toEqual({
      version: 1,
      extensions: {
        meter: { mode: 'dock-right' },
        keyer: { mode: 'collapsed' },
      },
    });
  });

  it('persists validated layout under the rigplane storage key', () => {
    const backing = new Map<string, string>();
    const storage = memoryStorage(backing);
    const state = setLocalExtensionDockMode(loadLocalExtensionDockLayout(storage), 'meter', 'dock-bottom');

    saveLocalExtensionDockLayout(state, storage);

    expect(JSON.parse(backing.get(LOCAL_EXTENSION_DOCK_LAYOUT_STORAGE_KEY) ?? '')).toEqual({
      version: 1,
      extensions: {
        meter: { mode: 'dock-bottom' },
      },
    });
    expect(loadLocalExtensionDockLayout(storage)).toEqual(state);
  });

  it('treats storage failures as best-effort persistence', () => {
    const storage: LocalExtensionDockStorage = {
      getItem: vi.fn(() => {
        throw new Error('blocked');
      }),
      setItem: vi.fn(() => {
        throw new Error('quota');
      }),
    };

    expect(loadLocalExtensionDockLayout(storage)).toEqual({
      version: 1,
      extensions: {},
    });
    expect(() => saveLocalExtensionDockLayout({
      version: 1,
      extensions: { meter: { mode: 'dock-left' } },
    }, storage)).not.toThrow();
  });
});
