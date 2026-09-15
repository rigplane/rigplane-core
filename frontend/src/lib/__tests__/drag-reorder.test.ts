import { describe, it, expect, beforeEach } from 'vitest';
import {
  KNOWN_DEFAULTS_SUFFIX,
  loadPanelOrder,
  reorderPanels,
} from '../drag-reorder.svelte';

const KEY = 'test:panel-order';
const KNOWN_KEY = KEY + KNOWN_DEFAULTS_SUFFIX;
const DEFAULTS = ['rx-audio', 'audio-scope', 'dsp', 'tx', 'cw', 'memory'];

const localStorageMock = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: (k: string) => store[k] ?? null,
    setItem: (k: string, v: string) => {
      store[k] = v;
    },
    removeItem: (k: string) => {
      delete store[k];
    },
    clear: () => {
      store = {};
    },
  };
})();

Object.defineProperty(globalThis, 'localStorage', {
  value: localStorageMock,
  writable: true,
});

beforeEach(() => {
  localStorageMock.clear();
});

describe('loadPanelOrder', () => {
  it('returns defaults when nothing stored', () => {
    expect(loadPanelOrder(KEY, DEFAULTS)).toEqual(DEFAULTS);
  });

  it('returns stored order when it fully matches defaults', () => {
    const custom = ['dsp', 'rx-audio', 'audio-scope', 'tx', 'cw', 'memory'];
    localStorage.setItem(KEY, JSON.stringify(custom));
    expect(loadPanelOrder(KEY, DEFAULTS)).toEqual(custom);
  });

  it('appends missing defaults so newly-introduced panels become visible (regression #821)', () => {
    // Simulates a user whose localStorage was written before `audio-scope`
    // was added to defaults. Without the merge, `audio-scope` never rendered.
    const pre821 = ['rx-audio', 'dsp', 'tx', 'cw', 'memory'];
    localStorage.setItem(KEY, JSON.stringify(pre821));
    const result = loadPanelOrder(KEY, DEFAULTS);
    expect(result).toContain('audio-scope');
    // Existing order is preserved; new id is appended.
    expect(result.slice(0, pre821.length)).toEqual(pre821);
    expect(result[result.length - 1]).toBe('audio-scope');
  });

  it('preserves unknown (peer-owned) ids in stored order', () => {
    const withForeign = ['rx-audio', 'audio-scope', 'dsp', 'tx', 'cw', 'memory', 'foreign-panel'];
    localStorage.setItem(KEY, JSON.stringify(withForeign));
    const result = loadPanelOrder(KEY, DEFAULTS);
    expect(result).toContain('foreign-panel');
  });

  it('deduplicates ids in stored order', () => {
    localStorage.setItem(KEY, JSON.stringify(['rx-audio', 'rx-audio', 'dsp']));
    const result = loadPanelOrder(KEY, DEFAULTS);
    expect(result.filter((id) => id === 'rx-audio')).toHaveLength(1);
  });

  it('falls back to defaults on invalid JSON', () => {
    localStorage.setItem(KEY, '{not-json');
    expect(loadPanelOrder(KEY, DEFAULTS)).toEqual(DEFAULTS);
  });

  it('falls back to defaults when stored value is not an array', () => {
    localStorage.setItem(KEY, JSON.stringify({ foo: 1 }));
    expect(loadPanelOrder(KEY, DEFAULTS)).toEqual(DEFAULTS);
  });

  it('does NOT re-add a default that was deliberately removed (cross-sidebar drag)', () => {
    // Simulates the aftermath of a cross-sidebar drag: the user moved
    // `audio-scope` from this sidebar to its peer, so this sidebar's stored
    // order no longer contains it, but `audio-scope` is in the known-defaults
    // set because the app has previously presented it here. Re-adding would
    // duplicate the panel across both sidebars.
    const afterDrag = ['rx-audio', 'dsp', 'tx', 'cw', 'memory'];
    localStorage.setItem(KEY, JSON.stringify(afterDrag));
    localStorage.setItem(KNOWN_KEY, JSON.stringify(DEFAULTS));
    const result = loadPanelOrder(KEY, DEFAULTS);
    expect(result).toEqual(afterDrag);
    expect(result).not.toContain('audio-scope');
  });

  it('appends a brand-new default not yet in known-defaults set', () => {
    // User's known-defaults set predates a newly-added panel id.
    const oldKnown = ['rx-audio', 'dsp', 'tx', 'cw', 'memory'];
    const stored = ['rx-audio', 'dsp', 'tx', 'cw', 'memory'];
    localStorage.setItem(KEY, JSON.stringify(stored));
    localStorage.setItem(KNOWN_KEY, JSON.stringify(oldKnown));
    const result = loadPanelOrder(KEY, DEFAULTS);
    expect(result).toContain('audio-scope');
    expect(result[result.length - 1]).toBe('audio-scope');
  });

  it('persists known-defaults set after load', () => {
    loadPanelOrder(KEY, DEFAULTS);
    const stored = localStorage.getItem(KNOWN_KEY);
    expect(stored).not.toBeNull();
    const parsed = JSON.parse(stored!);
    expect(new Set(parsed)).toEqual(new Set(DEFAULTS));
  });

  it('round-trips: save then load the same order is stable', () => {
    const custom = ['dsp', 'rx-audio', 'audio-scope', 'tx', 'cw', 'memory'];
    localStorage.setItem(KEY, JSON.stringify(custom));
    const first = loadPanelOrder(KEY, DEFAULTS);
    // Simulate a subsequent save + load cycle.
    localStorage.setItem(KEY, JSON.stringify(first));
    const second = loadPanelOrder(KEY, DEFAULTS);
    expect(second).toEqual(first);
  });

  it('round-trips after cross-sidebar removal: panel stays gone', () => {
    // First load establishes known-defaults.
    loadPanelOrder(KEY, DEFAULTS);
    // User drags `audio-scope` away — stored order shrinks.
    const afterDrag = ['rx-audio', 'dsp', 'tx', 'cw', 'memory'];
    localStorage.setItem(KEY, JSON.stringify(afterDrag));
    // Reload twice.
    const r1 = loadPanelOrder(KEY, DEFAULTS);
    localStorage.setItem(KEY, JSON.stringify(r1));
    const r2 = loadPanelOrder(KEY, DEFAULTS);
    expect(r1).toEqual(afterDrag);
    expect(r2).toEqual(afterDrag);
  });

  it('keeps a known-default zone empty after its final panel moves away', () => {
    localStorage.setItem(KEY, JSON.stringify([]));
    localStorage.setItem(KNOWN_KEY, JSON.stringify(DEFAULTS));

    expect(loadPanelOrder(KEY, DEFAULTS)).toEqual([]);
  });
});

describe('reorderPanels', () => {
  it('moves a panel forward', () => {
    const r = reorderPanels(DEFAULTS, 'rx-audio', 3);
    expect(r[3]).toBe('rx-audio');
    expect(r).toHaveLength(DEFAULTS.length);
  });

  it('is a no-op when source equals target index', () => {
    const r = reorderPanels(DEFAULTS, 'dsp', 2);
    expect(r).toBe(DEFAULTS);
  });

  it('returns input unchanged when id is not in the order', () => {
    const r = reorderPanels(DEFAULTS, 'missing', 0);
    expect(r).toBe(DEFAULTS);
  });
});
