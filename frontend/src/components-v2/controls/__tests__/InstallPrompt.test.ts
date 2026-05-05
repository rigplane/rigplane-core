import { describe, it, expect, beforeEach, vi } from 'vitest';
import {
  detectPlatform,
  isDismissed,
  setDismissed,
  getInstruction,
  hasInstallButton,
  isStandalone,
} from '../install-prompt-utils';

// jsdom localStorage may be incomplete; provide a working mock
const storage = new Map<string, string>();
const mockLocalStorage = {
  getItem: (key: string) => storage.get(key) ?? null,
  setItem: (key: string, value: string) => storage.set(key, value),
  removeItem: (key: string) => storage.delete(key),
  clear: () => storage.clear(),
  get length() { return storage.size; },
  key: (_i: number) => null,
};
Object.defineProperty(globalThis, 'localStorage', {
  value: mockLocalStorage,
  configurable: true,
});

// ── Platform detection ────────────────────────────────────────────────────

describe('detectPlatform', () => {
  it('returns ios for iPhone Safari', () => {
    const ua =
      'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1';
    expect(detectPlatform(ua)).toBe('ios');
  });

  it('returns ios for iPad Safari', () => {
    const ua =
      'Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/604.1';
    expect(detectPlatform(ua)).toBe('ios');
  });

  it('returns desktop for iPhone Chrome (CriOS)', () => {
    const ua =
      'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/117.0 Mobile/15E148 Safari/604.1';
    expect(detectPlatform(ua)).toBe('desktop');
  });

  it('returns desktop for iPhone Firefox (FxiOS)', () => {
    const ua =
      'Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) FxiOS/117.0 Mobile/15E148 Safari/604.1';
    expect(detectPlatform(ua)).toBe('desktop');
  });

  it('returns android for Android Chrome', () => {
    const ua =
      'Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0 Mobile Safari/537.36';
    expect(detectPlatform(ua)).toBe('android');
  });

  it('returns desktop for Windows Chrome', () => {
    const ua =
      'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0 Safari/537.36';
    expect(detectPlatform(ua)).toBe('desktop');
  });

  it('returns desktop for macOS Safari', () => {
    const ua =
      'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15';
    expect(detectPlatform(ua)).toBe('desktop');
  });
});

// ── Instructions ──────────────────────────────────────────────────────────

describe('getInstruction', () => {
  it('returns share icon instruction for iOS', () => {
    expect(getInstruction('ios', false)).toContain('Add to Home Screen');
  });

  it('returns fallback menu instruction for Android without prompt', () => {
    expect(getInstruction('android', false)).toContain('Install app');
  });

  it('returns empty when Android has prompt (button shown instead)', () => {
    expect(getInstruction('android', true)).toBe('');
  });

  it('returns browser menu instruction for desktop without prompt', () => {
    expect(getInstruction('desktop', false)).toContain('browser menu');
  });

  it('returns empty when desktop has prompt (button shown instead)', () => {
    expect(getInstruction('desktop', true)).toBe('');
  });
});

// ── Install button visibility ─────────────────────────────────────────────

describe('hasInstallButton', () => {
  it('shows button for Android with prompt', () => {
    expect(hasInstallButton('android', true)).toBe(true);
  });

  it('hides button for Android without prompt', () => {
    expect(hasInstallButton('android', false)).toBe(false);
  });

  it('shows button for desktop with prompt', () => {
    expect(hasInstallButton('desktop', true)).toBe(true);
  });

  it('hides button for desktop without prompt', () => {
    expect(hasInstallButton('desktop', false)).toBe(false);
  });

  it('never shows button for iOS', () => {
    expect(hasInstallButton('ios', true)).toBe(false);
    expect(hasInstallButton('ios', false)).toBe(false);
  });
});

// ── Dismissal persistence ─────────────────────────────────────────────────

describe('dismissal', () => {
  beforeEach(() => {
    storage.clear();
  });

  it('is not dismissed initially', () => {
    expect(isDismissed()).toBe(false);
  });

  it('is dismissed after setDismissed()', () => {
    setDismissed();
    expect(isDismissed()).toBe(true);
  });

  it('uses correct localStorage key', () => {
    setDismissed();
    expect(localStorage.getItem('rigplane:install-dismissed')).toBe('true');
  });
});

// ── Standalone detection ──────────────────────────────────────────────────

describe('isStandalone', () => {
  it('returns false in normal browser mode', () => {
    // jsdom defaults: matchMedia not matching, navigator.standalone undefined
    expect(isStandalone()).toBe(false);
  });

  it('returns true when navigator.standalone is true (iOS)', () => {
    Object.defineProperty(navigator, 'standalone', {
      value: true,
      configurable: true,
    });
    expect(isStandalone()).toBe(true);
    Object.defineProperty(navigator, 'standalone', {
      value: undefined,
      configurable: true,
    });
  });

  it('returns true when display-mode is standalone', () => {
    const original = window.matchMedia;
    window.matchMedia = vi.fn().mockReturnValue({ matches: true }) as any;
    expect(isStandalone()).toBe(true);
    window.matchMedia = original;
  });
});
