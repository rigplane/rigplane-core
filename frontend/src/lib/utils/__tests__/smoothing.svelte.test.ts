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

// MOR-1251 (findings from the MOR-1233 verification, 2026-08-04): three
// one-liner hardenings pinned individually below.

describe('createSmoother — matchMedia without addEventListener (MOR-1251 F11)', () => {
  it('KILL F11: does not throw when matchMedia returns a shallow {matches} mock (no addEventListener) — mirrors InstallPrompt.test.ts:176', () => {
    const original = window.matchMedia;
    // Exactly the shallow mock InstallPrompt.test.ts installs: no
    // addEventListener/removeEventListener/addListener at all.
    window.matchMedia = vi.fn().mockReturnValue({ matches: false }) as unknown as typeof window.matchMedia;
    try {
      const smoother = createSmoother(0.12, 0.32, 0);
      expect(() => {
        smoother.update(10);
        smoother.start();
        smoother.stop();
      }).not.toThrow();
    } finally {
      window.matchMedia = original;
    }
  });

  it('KILL F11 (fallback path): subscribes and unsubscribes via addListener/removeListener when addEventListener is absent (pre-Safari14 MediaQueryList)', () => {
    const original = window.matchMedia;
    let matches = false;
    const legacyListeners = new Set<(mql: MediaQueryListEvent) => void>();
    const mql = {
      get matches() { return matches; },
      addListener: (fn: (mql: MediaQueryListEvent) => void) => { legacyListeners.add(fn); },
      removeListener: (fn: (mql: MediaQueryListEvent) => void) => { legacyListeners.delete(fn); },
    } as unknown as MediaQueryList;
    window.matchMedia = vi.fn().mockReturnValue(mql) as unknown as typeof window.matchMedia;
    try {
      const smoother = createSmoother(0.12, 0.32, 0);
      smoother.update(10);
      smoother.start();
      expect(legacyListeners.size).toBe(1); // subscribed via the legacy fallback

      matches = true;
      legacyListeners.forEach((fn) => fn({} as MediaQueryListEvent));
      expect(smoother.value).toBe(10); // handler fired through the fallback

      smoother.stop();
      expect(legacyListeners.size).toBe(0); // unsubscribed via the legacy fallback
    } finally {
      window.matchMedia = original;
    }
  });
});

describe('createSmoother — double start() without stop() (MOR-1251 F9)', () => {
  it('KILL F9: unsubscribes the first change listener before a second start() — no orphaned listener', () => {
    const { listenerCount, restore } = mockReducedMotion(false);
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation(() => 1);
    vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {});
    try {
      const smoother = createSmoother(0.12, 0.32, 0);
      smoother.update(10);

      smoother.start();
      expect(listenerCount()).toBe(1);

      smoother.start(); // second start() without an intervening stop()
      expect(listenerCount()).toBe(1); // must not orphan the first listener

      smoother.stop();
      expect(listenerCount()).toBe(0);
    } finally {
      restore();
    }
  });
});

// Verifier follow-up (2026-08-04, H1): F9 closed the orphaned-*listener* half
// of a double start() without stop(), but start() still called loop()
// unconditionally, overwriting frameId without cancelling the previous
// pending frame — an orphaned rAF *chain* that outlives stop(). Verifier
// probe W6: rafScheduled=2, cafTotal=1 -> orphanedLoops=1 on the pre-H1 code.
// Fix: start() cancels any pending frame before scheduling a new one, making
// it idempotent. This test reproduces the W6 shape directly: distinct
// non-zero rAF ids so cancellation is observable per call.
describe('createSmoother — double start() cancels the pending rAF chain (MOR-1251 H1)', () => {
  it('KILL H1: a second start() cancels the first pending frame — every scheduled frame is eventually cancelled (rafScheduled - cafTotal === 0)', () => {
    const { restore } = mockReducedMotion(false);
    let nextId = 1;
    const rafSpy = vi.spyOn(window, 'requestAnimationFrame').mockImplementation(() => nextId++);
    const cafSpy = vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {});
    try {
      const smoother = createSmoother(0.12, 0.32, 0);
      smoother.update(10);

      smoother.start();
      expect(rafSpy).toHaveBeenCalledTimes(1);
      expect(cafSpy).toHaveBeenCalledTimes(0);

      smoother.start(); // second start() without an intervening stop()
      expect(rafSpy).toHaveBeenCalledTimes(2);
      expect(cafSpy).toHaveBeenCalledTimes(1); // the first pending frame was cancelled first

      smoother.stop();
      expect(cafSpy).toHaveBeenCalledTimes(2); // the second (surviving) frame is cancelled too

      // W6 shape: no orphaned loop survives stop().
      expect(rafSpy.mock.calls.length - cafSpy.mock.calls.length).toBe(0);
    } finally {
      restore();
    }
  });
});

describe('createSmoother — target initial value (MOR-1251 F4)', () => {
  it('KILL F4: start() before the first update(), under reduced motion, shows initialValue — not 0', () => {
    const { restore } = mockReducedMotion(true);
    try {
      const smoother = createSmoother(0.12, 0.32, 25);
      expect(smoother.value).toBe(25);

      smoother.start(); // no update() has been called yet

      expect(smoother.value).toBe(25); // must stay at initialValue, not snap to 0
      smoother.stop();
    } finally {
      restore();
    }
  });

  // Verifier follow-up (H3): F4 has two independent trigger paths — start()
  // under reduce (covered above) and the ON-flip change handler's
  // `current = target` (verifier probe W8). Both were fixed by the same
  // `target = initialValue` one-liner, but only the first was pinned.
  it('KILL H3 (F4, second trigger): OS flips reduced motion ON before the first update() — shows initialValue, not 0', () => {
    const { setMatches, restore } = mockReducedMotion(false);
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation(() => 1);
    vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {});
    try {
      const smoother = createSmoother(0.12, 0.32, 25);
      smoother.start(); // not reduced at start(); no update() has been called yet

      setMatches(true); // OS flips ON mid-session, still before any update()

      expect(smoother.value).toBe(25); // must snap to initialValue, not 0
      smoother.stop();
    } finally {
      restore();
    }
  });
});
