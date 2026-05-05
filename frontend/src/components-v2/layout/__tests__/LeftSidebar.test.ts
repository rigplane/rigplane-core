import { describe, it, expect, beforeEach } from 'vitest';

/**
 * Tests for LeftSidebar panel order logic.
 *
 * We test the pure functions / localStorage contract directly rather than
 * mounting the full component (which pulls in radio stores, command-bus, etc.).
 */

const PANEL_ORDER_KEY = 'rigplane:panel-order';
const DEFAULT_ORDER = ['rf-front-end', 'mode', 'filter', 'agc', 'rit-xit', 'band', 'antenna'];

// Mirror the loadPanelOrder logic from LeftSidebar.svelte
function loadPanelOrder(): string[] {
  try {
    const stored = localStorage.getItem(PANEL_ORDER_KEY);
    if (stored) {
      const parsed = JSON.parse(stored);
      if (
        Array.isArray(parsed) &&
        parsed.length === DEFAULT_ORDER.length &&
        DEFAULT_ORDER.every((id) => parsed.includes(id))
      ) {
        return parsed;
      }
    }
  } catch {
    /* ignore */
  }
  return [...DEFAULT_ORDER];
}

// Mock localStorage
const localStorageMock = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: (key: string) => store[key] ?? null,
    setItem: (key: string, value: string) => {
      store[key] = value;
    },
    removeItem: (key: string) => {
      delete store[key];
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

describe('Panel order — localStorage', () => {
  it('returns default order when nothing stored', () => {
    expect(loadPanelOrder()).toEqual(DEFAULT_ORDER);
  });

  it('returns stored order when valid', () => {
    const custom = ['band', 'mode', 'filter', 'agc', 'rit-xit', 'rf-front-end', 'antenna'];
    localStorage.setItem(PANEL_ORDER_KEY, JSON.stringify(custom));
    expect(loadPanelOrder()).toEqual(custom);
  });

  it('falls back to default when stored JSON is not an array', () => {
    localStorage.setItem(PANEL_ORDER_KEY, JSON.stringify({ bad: true }));
    expect(loadPanelOrder()).toEqual(DEFAULT_ORDER);
  });

  it('falls back to default when stored array has wrong length', () => {
    localStorage.setItem(PANEL_ORDER_KEY, JSON.stringify(['mode', 'filter']));
    expect(loadPanelOrder()).toEqual(DEFAULT_ORDER);
  });

  it('falls back to default when stored array has unknown panel ids', () => {
    const bad = ['rf-front-end', 'mode', 'filter', 'agc', 'rit-xit', 'band', 'UNKNOWN'];
    localStorage.setItem(PANEL_ORDER_KEY, JSON.stringify(bad));
    expect(loadPanelOrder()).toEqual(DEFAULT_ORDER);
  });

  it('falls back to default when stored value is invalid JSON', () => {
    localStorage.setItem(PANEL_ORDER_KEY, 'not-json!!!');
    expect(loadPanelOrder()).toEqual(DEFAULT_ORDER);
  });

  it('persists order via setItem', () => {
    const custom = ['antenna', 'band', 'rit-xit', 'agc', 'filter', 'mode', 'rf-front-end'];
    localStorage.setItem(PANEL_ORDER_KEY, JSON.stringify(custom));
    expect(loadPanelOrder()).toEqual(custom);
  });

  it('reset clears localStorage and restores default', () => {
    const custom = ['band', 'mode', 'filter', 'agc', 'rit-xit', 'rf-front-end', 'antenna'];
    localStorage.setItem(PANEL_ORDER_KEY, JSON.stringify(custom));
    expect(loadPanelOrder()).toEqual(custom);

    // Simulate reset
    localStorage.removeItem(PANEL_ORDER_KEY);
    expect(loadPanelOrder()).toEqual(DEFAULT_ORDER);
  });
});

describe('Panel order — reorder logic', () => {
  function reorder(order: string[], fromId: string, toIndex: number): string[] {
    const fromIndex = order.indexOf(fromId);
    if (fromIndex === toIndex) return order;
    const newOrder = [...order];
    const [moved] = newOrder.splice(fromIndex, 1);
    newOrder.splice(toIndex, 0, moved);
    return newOrder;
  }

  it('moves panel forward', () => {
    const result = reorder(DEFAULT_ORDER, 'rf-front-end', 3);
    expect(result[3]).toBe('rf-front-end');
    expect(result.length).toBe(DEFAULT_ORDER.length);
  });

  it('moves panel backward', () => {
    const result = reorder(DEFAULT_ORDER, 'band', 1);
    expect(result[1]).toBe('band');
    expect(result.length).toBe(DEFAULT_ORDER.length);
  });

  it('no-op when target equals source', () => {
    const result = reorder(DEFAULT_ORDER, 'mode', 1);
    expect(result).toEqual(DEFAULT_ORDER);
  });

  it('preserves all panel ids after reorder', () => {
    const result = reorder(DEFAULT_ORDER, 'agc', 0);
    expect([...result].sort()).toEqual([...DEFAULT_ORDER].sort());
  });
});
