import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { ComponentProps } from 'svelte';
import LinearSMeter from '../LinearSMeter.svelte';

// MOR-1233: LinearSMeter runs two independent requestAnimationFrame loops —
// the ballistics smoother (via createSmoother) and its own peak-hold
// hold-then-decay loop — neither of which previously checked
// `prefers-reduced-motion`. These tests pin the fix: under reduced motion,
// mounting the meter must not schedule any animation frame, and the peak
// indicator must track the bar directly (no stale hold) rather than
// lingering after a value drop.
//
// requestAnimationFrame is mocked to a no-op (never actually fires) so these
// tests assert on *scheduling* deterministically, without a real ~16ms timer
// callback bleeding into a later test after its component is unmounted.
//
// Peak-line presence is asserted via querySelectorAll(...).length rather
// than querySelector(...) + toBeNull(): comparing a live DOM Element with
// toBeNull() drives vitest's failure-message pretty-printer through the
// element's own properties, which on a Svelte-mounted node trips Svelte's
// dev-mode "rune used outside .svelte" guard as a red herring unrelated to
// this fix. Asserting on the NodeList length sidesteps that entirely.
//
// Fix cycle 1 (verifier F1/F2): mounting under a *fixed* preference was
// proven, but the decision was latched at mount — a runtime OS flip in
// either direction left the peak-hold loop (like the ballistics loop in
// smoothing.svelte.ts) either frozen forever or gliding forever. The mock
// below gets working addEventListener/removeEventListener + a setMatches()
// trigger so these tests can simulate a live flip.

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

let rafSpy: ReturnType<typeof vi.spyOn>;
let cafSpy: ReturnType<typeof vi.spyOn>;

let components: ReturnType<typeof mount>[] = [];
let roots: HTMLElement[] = [];

function mountReactive(props: ComponentProps<typeof LinearSMeter>) {
  const state = $state(props);
  const target = document.createElement('div');
  document.body.appendChild(target);
  roots.push(target);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const component = mount(LinearSMeter as any, { target, props: state });
  flushSync();
  components.push(component);
  return { target, state, component };
}

beforeEach(() => {
  // Never let a real frame fire — tests only care whether scheduling was
  // attempted, not about animating an actual frame forward. The fake id
  // must be non-zero: production code treats a falsy frameId (its initial
  // value) as "nothing scheduled" — an id of 0 would make a real scheduled
  // frame indistinguishable from none, silently defeating the
  // cancelAnimationFrame assertions below.
  rafSpy = vi.spyOn(window, 'requestAnimationFrame').mockImplementation(() => 1);
  cafSpy = vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => {});
});

afterEach(() => {
  components.forEach((component) => unmount(component));
  roots.forEach((root) => root.remove());
  components = [];
  roots = [];
  vi.restoreAllMocks();
});

function peakLineCount(target: HTMLElement): number {
  return target.querySelectorAll('line[stroke-width="2"]').length;
}

describe('LinearSMeter — prefers-reduced-motion (MOR-1233)', () => {
  it('schedules no animation frame on mount when reduced motion is preferred', () => {
    const { restore } = mockReducedMotion(true);
    try {
      mountReactive({ value: 20 });
      expect(rafSpy).not.toHaveBeenCalled();
    } finally {
      restore();
    }
  });

  it('still schedules animation frames on mount when reduced motion is NOT preferred (baseline)', () => {
    const { restore } = mockReducedMotion(false);
    try {
      mountReactive({ value: 20 });
      // Ballistics smoother + peak-hold decay loop each schedule a frame.
      expect(rafSpy).toHaveBeenCalled();
    } finally {
      restore();
    }
  });

  it('does not leave a stale peak indicator after a value drop when reduced motion is preferred', () => {
    const { restore } = mockReducedMotion(true);
    try {
      const { target, state } = mountReactive({ value: 20 });
      flushSync();
      // Even at a high value, no held peak line should linger: the peak
      // tracks the (instantly-snapped) bar value directly under reduced
      // motion, so peakSegs === smoother.value at all times.
      expect(peakLineCount(target)).toBe(0);

      state.value = 0;
      flushSync();
      expect(peakLineCount(target)).toBe(0);
    } finally {
      restore();
    }
  });
});

describe('LinearSMeter — runtime prefers-reduced-motion flips (MOR-1233 fix cycle 1, F1/F2)', () => {
  it('KILL F1: stops both live loops when reduced motion turns ON mid-session', () => {
    const { setMatches, restore } = mockReducedMotion(false);
    try {
      mountReactive({ value: 20 });
      flushSync();
      rafSpy.mockClear();

      // OS preference flips to "reduce" while both loops (ballistics +
      // peak-hold) are already scheduled.
      setMatches(true);

      // Both loops must be torn down independently — exactly one
      // cancelAnimationFrame per loop (2 total). toHaveBeenCalled() alone
      // is satisfied by either loop cancelling on its own, so a mutant that
      // severs just one loop's cancel branch (verifier N6/N8) would survive
      // undetected; toHaveBeenCalledTimes(2) requires both.
      expect(cafSpy).toHaveBeenCalledTimes(2);
      expect(rafSpy).not.toHaveBeenCalled();
    } finally {
      restore();
    }
  });

  it('KILL F2: resumes both loops when reduced motion turns OFF mid-session', () => {
    const { setMatches, restore } = mockReducedMotion(true);
    try {
      mountReactive({ value: 20 });
      flushSync();
      expect(rafSpy).not.toHaveBeenCalled(); // nothing scheduled at mount

      // OS preference clears while mounted — both loops must (re)start
      // independently: exactly one requestAnimationFrame per loop (2
      // total). toHaveBeenCalled() alone would still pass if only one loop
      // resumed (verifier N6/N8); toHaveBeenCalledTimes(2) requires both.
      setMatches(false);

      expect(rafSpy).toHaveBeenCalledTimes(2);
    } finally {
      restore();
    }
  });

  it('detaches both reduced-motion change listeners on unmount — no leak', () => {
    const { listenerCount, restore } = mockReducedMotion(false);
    try {
      const { component } = mountReactive({ value: 20 });
      flushSync();
      // Ballistics smoother + peak-hold each register their own listener.
      expect(listenerCount()).toBeGreaterThanOrEqual(2);

      unmount(component);
      components = components.filter((c) => c !== component);
      expect(listenerCount()).toBe(0);
    } finally {
      restore();
    }
  });
});
