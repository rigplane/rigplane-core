import { describe, it, expect, vi, afterEach } from 'vitest';
import { createSmoother } from '../smoothing.svelte';

// MOR-1233: the rAF ballistics loop in createSmoother previously ran
// unconditionally, ignoring `prefers-reduced-motion`. These tests pin the
// fix: under reduced motion the value must snap directly to target instead
// of animating, and no requestAnimationFrame loop may be scheduled at all.
//
// Fix cycle 1 (verifier F1/F2): the reduced-motion decision must not be
// latched at mount. A `MediaQueryList` mock with working
// addEventListener/removeEventListener + a `setMatches()` trigger lets these
// tests simulate the OS flipping the preference *while a smoother is live*,
// in both directions, and prove the change listener is detached on stop()
// (no leak).

interface MatchMediaMock {
  mql: MediaQueryList;
  setMatches: (matches: boolean) => void;
  listenerCount: () => number;
}

function createMatchMediaMock(initialMatches: boolean): MatchMediaMock {
  let matches = initialMatches;
  const listeners = new Set<() => void>();
  const mql = {
    get matches() { return matches; },
    addEventListener: (_type: string, fn: () => void) => { listeners.add(fn); },
    removeEventListener: (_type: string, fn: () => void) => { listeners.delete(fn); },
  } as unknown as MediaQueryList;
  return {
    mql,
    setMatches: (next: boolean) => {
      matches = next;
      listeners.forEach((fn) => fn());
    },
    listenerCount: () => listeners.size,
  };
}

function mockReducedMotion(matches: boolean) {
  const original = window.matchMedia;
  const { mql, setMatches, listenerCount } = createMatchMediaMock(matches);
  window.matchMedia = vi.fn().mockReturnValue(mql) as unknown as typeof window.matchMedia;
  return {
    setMatches,
    listenerCount,
    restore: () => { window.matchMedia = original; },
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('createSmoother — prefers-reduced-motion (MOR-1233)', () => {
  it('snaps value directly to target on update() when reduced motion is preferred', () => {
    const { restore } = mockReducedMotion(true);
    try {
      const smoother = createSmoother(0.12, 0.32, 0);
      expect(smoother.value).toBe(0);

      smoother.update(42);

      // No rAF tick has run — under reduced motion the snap happens inline.
      expect(smoother.value).toBe(42);
    } finally {
      restore();
    }
  });

  it('does not schedule requestAnimationFrame from start() when reduced motion is preferred', () => {
    const { restore } = mockReducedMotion(true);
    const rafSpy = vi.spyOn(window, 'requestAnimationFrame');
    try {
      const smoother = createSmoother(0.12, 0.32, 0);
      smoother.update(10);
      smoother.start();

      expect(rafSpy).not.toHaveBeenCalled();
      expect(smoother.value).toBe(10);

      smoother.stop();
    } finally {
      restore();
    }
  });

  it('still schedules requestAnimationFrame from start() when reduced motion is NOT preferred (baseline)', () => {
    const { restore } = mockReducedMotion(false);
    const rafSpy = vi.spyOn(window, 'requestAnimationFrame');
    try {
      const smoother = createSmoother(0.12, 0.32, 0);
      smoother.update(10);
      smoother.start();

      expect(rafSpy).toHaveBeenCalled();
      // Ballistics haven't converged yet — a real animation is in flight.
      expect(smoother.value).toBe(0);

      smoother.stop();
    } finally {
      restore();
    }
  });
});

describe('createSmoother — runtime prefers-reduced-motion flips (MOR-1233 fix cycle 1, F1/F2)', () => {
  it('KILL F1: stops the live rAF loop and snaps to target when reduced motion turns ON mid-session', () => {
    const { setMatches, restore } = mockReducedMotion(false);
    const rafSpy = vi.spyOn(window, 'requestAnimationFrame').mockImplementation(() => 1);
    const cafSpy = vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {});
    try {
      const smoother = createSmoother(0.12, 0.32, 0);
      smoother.update(50);
      smoother.start();
      expect(rafSpy).toHaveBeenCalledTimes(1);

      // OS preference flips to "reduce" while the loop is already live.
      setMatches(true);

      expect(smoother.value).toBe(50); // snapped immediately
      expect(cafSpy).toHaveBeenCalled(); // the live loop was stopped

      rafSpy.mockClear();
      smoother.update(80); // further updates keep snapping, no new frame
      expect(smoother.value).toBe(80);
      expect(rafSpy).not.toHaveBeenCalled();

      smoother.stop();
    } finally {
      restore();
    }
  });

  it('KILL F2: resumes the rAF loop and stops instant-snapping when reduced motion turns OFF mid-session', () => {
    const { setMatches, restore } = mockReducedMotion(true);
    const rafSpy = vi.spyOn(window, 'requestAnimationFrame').mockImplementation(() => 1);
    try {
      const smoother = createSmoother(0.12, 0.32, 0);
      smoother.update(50);
      smoother.start();
      expect(smoother.value).toBe(50); // snapped at mount (reduce was ON)
      expect(rafSpy).not.toHaveBeenCalled(); // no loop yet

      // OS preference clears while mounted.
      setMatches(false);
      expect(rafSpy).toHaveBeenCalledTimes(1); // the loop resumed

      rafSpy.mockClear();
      smoother.update(90);
      // A currently-snapped meter must animate again, not instant-snap: the
      // value stays at the pre-flip 50 (the mocked rAF never actually fires
      // the tick callback here), proving update() no longer force-snaps.
      expect(smoother.value).toBe(50);

      smoother.stop();
    } finally {
      restore();
    }
  });

  it('detaches the reduced-motion change listener on stop() — no leak', () => {
    const { listenerCount, restore } = mockReducedMotion(false);
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation(() => 1);
    vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {});
    try {
      const smoother = createSmoother(0.12, 0.32, 0);
      smoother.update(10);
      smoother.start();
      expect(listenerCount()).toBeGreaterThan(0);

      smoother.stop();
      expect(listenerCount()).toBe(0);
    } finally {
      restore();
    }
  });
});
