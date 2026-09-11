import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { readQaCockpitLayoutOverride } from '../qa-cockpit-override';

describe('readQaCockpitLayoutOverride (MOR-1257)', () => {
  it('returns the cockpit override when the exact query param is present', () => {
    expect(readQaCockpitLayoutOverride('?layout=dual-receiver-cockpit')).toBe('dual-receiver-cockpit');
  });

  // T160 PR-1: the flagship geometry probe rides the same param. Kill-test:
  // dropping it from the module's value list returns null here, which
  // resolves to the normal preference and leaves the probe unreachable.
  it('returns the flagship-probe override when the exact query param is present', () => {
    expect(readQaCockpitLayoutOverride('?layout=flagship-probe')).toBe('flagship-probe');
  });

  it('returns null for an empty search string', () => {
    expect(readQaCockpitLayoutOverride('')).toBeNull();
  });

  it('returns null when the param is absent entirely', () => {
    expect(readQaCockpitLayoutOverride('?foo=bar')).toBeNull();
  });

  // Kill-test: without this exact-value check, any `?layout=...` (e.g. a
  // persisted-style value a user might guess) would wrongly activate the
  // QA-only cockpit.
  it('returns null for any other layout value, including near-misses', () => {
    for (const value of ['standard', 'lcd-cockpit', 'dual-receiver', 'Dual-Receiver-Cockpit', '',
      'flagship', 'Flagship-Probe', 'flagship-probe-x']) {
      expect(readQaCockpitLayoutOverride(`?layout=${value}`)).toBeNull();
    }
  });

  it('finds the param alongside unrelated query params, in either position', () => {
    expect(readQaCockpitLayoutOverride('?ui=v2&layout=dual-receiver-cockpit')).toBe('dual-receiver-cockpit');
    expect(readQaCockpitLayoutOverride('?layout=dual-receiver-cockpit&demo=1')).toBe('dual-receiver-cockpit');
  });

  it('accepts a leading-?-less search string, matching URLSearchParams semantics', () => {
    expect(readQaCockpitLayoutOverride('layout=dual-receiver-cockpit')).toBe('dual-receiver-cockpit');
  });

  describe('falling back to window.location.search when no argument is given', () => {
    it('reads the live URL', () => {
      const originalSearch = window.location.search;
      window.history.replaceState({}, '', '/?layout=dual-receiver-cockpit');
      try {
        expect(readQaCockpitLayoutOverride()).toBe('dual-receiver-cockpit');
      } finally {
        window.history.replaceState({}, '', `/${originalSearch}`);
      }
    });

    it('returns null for the default (param-less) URL — the default-path pin', () => {
      const originalSearch = window.location.search;
      window.history.replaceState({}, '', '/');
      try {
        expect(readQaCockpitLayoutOverride()).toBeNull();
      } finally {
        window.history.replaceState({}, '', `/${originalSearch}`);
      }
    });
  });

  describe('mobile-suppression warning (MOR-1257 D2)', () => {
    let warnSpy: ReturnType<typeof vi.spyOn>;
    let originalWidth: number;
    let originalHeight: number;
    let originalTouchPoints: PropertyDescriptor | undefined;

    beforeEach(() => {
      warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {});
      originalWidth = window.innerWidth;
      originalHeight = window.innerHeight;
      originalTouchPoints = Object.getOwnPropertyDescriptor(navigator, 'maxTouchPoints');
      Object.defineProperty(navigator, 'maxTouchPoints', { configurable: true, value: 0 });
    });

    afterEach(() => {
      warnSpy.mockRestore();
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: originalWidth });
      Object.defineProperty(window, 'innerHeight', { configurable: true, value: originalHeight });
      if (originalTouchPoints) Object.defineProperty(navigator, 'maxTouchPoints', originalTouchPoints);
      else Reflect.deleteProperty(navigator, 'maxTouchPoints');
    });

    it('warns once when the param matches but the viewport is under 640px', () => {
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 });
      Object.defineProperty(window, 'innerHeight', { configurable: true, value: 844 });

      expect(readQaCockpitLayoutOverride('?layout=dual-receiver-cockpit')).toBe('dual-receiver-cockpit');

      expect(warnSpy).toHaveBeenCalledTimes(1);
      expect(warnSpy.mock.calls[0][0]).toMatch(/dual-receiver-cockpit/);
      expect(warnSpy.mock.calls[0][0]).toMatch(/mobile/);
    });

    it('does not warn when the param matches and the viewport is desktop-sized', () => {
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1280 });
      Object.defineProperty(window, 'innerHeight', { configurable: true, value: 800 });

      expect(readQaCockpitLayoutOverride('?layout=dual-receiver-cockpit')).toBe('dual-receiver-cockpit');

      expect(warnSpy).not.toHaveBeenCalled();
    });

    it.each([
      [1136, 600, 0, false],
      [1440, 450, 0, false],
      [1024, 600, 5, false],
      [844, 390, 5, true],
      [844, 500, 5, false],
      [639, 900, 0, true],
    ])('classifies %ix%i with %i touch points consistently', (width, height, touchPoints, mobile) => {
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: width });
      Object.defineProperty(window, 'innerHeight', { configurable: true, value: height });
      Object.defineProperty(navigator, 'maxTouchPoints', { configurable: true, value: touchPoints });
      expect(readQaCockpitLayoutOverride('?layout=flagship-probe')).toBe('flagship-probe');
      expect(warnSpy).toHaveBeenCalledTimes(mobile ? 1 : 0);
    });

    it('does not warn when the param is absent, regardless of viewport size', () => {
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 });
      Object.defineProperty(window, 'innerHeight', { configurable: true, value: 844 });

      expect(readQaCockpitLayoutOverride('')).toBeNull();

      expect(warnSpy).not.toHaveBeenCalled();
    });

    it('treats exactly 640px as desktop-sized (boundary is exclusive)', () => {
      Object.defineProperty(window, 'innerWidth', { configurable: true, value: 640 });
      Object.defineProperty(window, 'innerHeight', { configurable: true, value: 640 });

      expect(readQaCockpitLayoutOverride('?layout=dual-receiver-cockpit')).toBe('dual-receiver-cockpit');

      expect(warnSpy).not.toHaveBeenCalled();
    });
  });
});
