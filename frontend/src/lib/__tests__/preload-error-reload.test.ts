/**
 * MOR-2680 — the guard for the page reload after a failed asset preload.
 * Times are literal milliseconds, so a change to the 60-second window fails
 * here rather than being read back from the module under test.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  claimPreloadErrorReload,
  clearPreloadErrorReload,
  shouldReloadAfterPreloadError,
} from '../preload-error-reload';

const MARKER = 'rigplane:preload-error-reload-at';

/** Runs `body` while reading the `sessionStorage` global throws. */
function withUnreachableSessionStorage(body: () => void): void {
  const original = Object.getOwnPropertyDescriptor(globalThis, 'sessionStorage')!;
  Object.defineProperty(globalThis, 'sessionStorage', {
    configurable: true,
    get() {
      throw new DOMException('The operation is insecure.', 'SecurityError');
    },
  });
  try {
    body();
  } finally {
    Object.defineProperty(globalThis, 'sessionStorage', original);
  }
}

afterEach(() => {
  vi.restoreAllMocks();
  sessionStorage.removeItem(MARKER);
});

describe('shouldReloadAfterPreloadError', () => {
  it('reloads on the first failure before any layout has mounted', () => {
    expect(shouldReloadAfterPreloadError(1_000_000, null, false)).toBe(true);
  });

  it('does not reload again within 60 s of the recorded reload', () => {
    expect(shouldReloadAfterPreloadError(1_000_000, '1000000', false)).toBe(false);
    expect(shouldReloadAfterPreloadError(1_059_999, '1000000', false)).toBe(false);
  });

  it('reloads again once 60 s have passed since the recorded reload', () => {
    expect(shouldReloadAfterPreloadError(1_060_000, '1000000', false)).toBe(true);
  });

  it('never reloads once a layout has mounted', () => {
    expect(shouldReloadAfterPreloadError(1_000_000, null, true)).toBe(false);
    expect(shouldReloadAfterPreloadError(1_060_000, '1000000', true)).toBe(false);
  });
});

describe('claimPreloadErrorReload', () => {
  it('records the reload time, then refuses a second reload within 60 s', () => {
    expect(claimPreloadErrorReload(1_000_000, false)).toBe(true);
    expect(sessionStorage.getItem(MARKER)).toBe('1000000');

    expect(claimPreloadErrorReload(1_059_999, false)).toBe(false);
    expect(sessionStorage.getItem(MARKER)).toBe('1000000');
  });

  it('records nothing once a layout has mounted', () => {
    expect(claimPreloadErrorReload(1_000_000, true)).toBe(false);
    expect(sessionStorage.getItem(MARKER)).toBeNull();
  });

  it('does not reload when sessionStorage cannot be reached', () => {
    withUnreachableSessionStorage(() => {
      expect(claimPreloadErrorReload(1_000_000, false)).toBe(false);
    });
  });

  it('does not reload when the reload cannot be recorded', () => {
    vi.spyOn(sessionStorage, 'setItem').mockImplementation(() => {
      throw new DOMException('The quota has been exceeded.', 'QuotaExceededError');
    });
    expect(claimPreloadErrorReload(1_000_000, false)).toBe(false);
  });
});

describe('clearPreloadErrorReload', () => {
  it('removes the recorded reload', () => {
    sessionStorage.setItem(MARKER, '1000000');
    clearPreloadErrorReload();
    expect(sessionStorage.getItem(MARKER)).toBeNull();
  });

  it('does not throw when sessionStorage cannot be reached', () => {
    withUnreachableSessionStorage(() => {
      expect(() => clearPreloadErrorReload()).not.toThrow();
    });
  });
});
