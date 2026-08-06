import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import BarGauge from '../BarGauge.svelte';

// MOR-1282: BarGauge's peak marker must honor `prefers-reduced-motion` the
// same way MetersDockPanel and LinearSMeter's own peak-hold loops already do
// (MOR-1233/1249/1252): no decay interval scheduled while reduced motion is
// preferred, the marker becomes a STATIC hold (frozen at the latched peak,
// not decaying/gliding), and it re-seats INSTANTLY on the next live sample
// once the hold window elapses — never on a timer. The MatchMediaMock +
// mockReducedMotion() helper is duplicated from the sibling *.reduced-motion
// test files (established per-file convention in this codebase, no shared
// test util) — test-only code, not production LOC.

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

let components: ReturnType<typeof mount>[] = [];
let roots: HTMLElement[] = [];
let setIntervalSpy: ReturnType<typeof vi.spyOn>;
let clearIntervalSpy: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  components = [];
  roots = [];
  vi.useFakeTimers();
  vi.setSystemTime(0);
  setIntervalSpy = vi.spyOn(window, 'setInterval');
  clearIntervalSpy = vi.spyOn(window, 'clearInterval');
});

afterEach(() => {
  components.forEach((c) => unmount(c));
  roots.forEach((r) => r.remove());
  components = [];
  roots = [];
  vi.useRealTimers();
});

function netActiveIntervals(): number {
  return setIntervalSpy.mock.calls.length - clearIntervalSpy.mock.calls.length;
}

function mountReactive(props: Record<string, unknown>) {
  const state = $state(props);
  const t = document.createElement('div');
  document.body.appendChild(t);
  roots.push(t);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const component = mount(BarGauge as any, { target: t, props: state });
  flushSync();
  components.push(component);
  return { t, state, component };
}

function markerX(t: HTMLElement): number | null {
  const el = t.querySelector('[data-testid="bar-gauge-peak-marker"]');
  const x = el?.getAttribute('x');
  return x === null || x === undefined ? null : parseFloat(x);
}

describe('BarGauge — prefers-reduced-motion peak marker (MOR-1282, mirrors MOR-1249/1252)', () => {
  it('schedules no decay interval when reduced motion is preferred at mount', () => {
    const { restore } = mockReducedMotion(true);
    try {
      mountReactive({ value: 1.0, label: 'Po', displayValue: '100W', showPeak: true });
      expect(netActiveIntervals()).toBe(0);
    } finally {
      restore();
    }
  });

  it('schedules the decay interval when reduced motion is NOT preferred (baseline)', () => {
    const { restore } = mockReducedMotion(false);
    try {
      mountReactive({ value: 1.0, label: 'Po', displayValue: '100W', showPeak: true });
      expect(netActiveIntervals()).toBe(1);
    } finally {
      restore();
    }
  });

  it('holds the marker statically under reduced motion — a further drop does not move it', () => {
    const { restore } = mockReducedMotion(true);
    try {
      const { t, state } = mountReactive({
        value: 1.0, label: 'Po', displayValue: '100W', showPeak: true,
      });
      const held = markerX(t);
      expect(held).not.toBeNull();

      state.value = 0.05;
      flushSync();
      expect(markerX(t)).toBe(held); // unchanged — static hold, no glide

      // No timer ever fires the reset — the hold is event-driven, not ticked.
      vi.advanceTimersByTime(2000);
      expect(netActiveIntervals()).toBe(0);
      expect(markerX(t)).toBe(held);
    } finally {
      restore();
    }
  });

  it('resets the held peak instantly once a fresh sample arrives past the hold window', () => {
    const { restore } = mockReducedMotion(true);
    try {
      const { t, state } = mountReactive({
        value: 1.0, label: 'Po', displayValue: '100W', showPeak: true,
      });
      const held = markerX(t);

      vi.advanceTimersByTime(2000); // past the 1500ms decay window
      state.value = 0.2; // the next genuine sample after expiry
      flushSync();
      expect(markerX(t)).not.toBe(held);
    } finally {
      restore();
    }
  });

  it('stops the live decay interval when reduced motion turns ON mid-session (F1)', () => {
    const { setMatches, restore } = mockReducedMotion(false);
    try {
      mountReactive({ value: 1.0, label: 'Po', displayValue: '100W', showPeak: true });
      expect(netActiveIntervals()).toBe(1);

      setMatches(true);
      expect(netActiveIntervals()).toBe(0);
    } finally {
      restore();
    }
  });

  it('resumes the live decay interval when reduced motion turns OFF mid-session (F2)', () => {
    const { setMatches, restore } = mockReducedMotion(true);
    try {
      mountReactive({ value: 1.0, label: 'Po', displayValue: '100W', showPeak: true });
      expect(netActiveIntervals()).toBe(0);

      setMatches(false);
      expect(netActiveIntervals()).toBe(1);
    } finally {
      restore();
    }
  });

  it('detaches the reduced-motion listener and clears the interval on unmount — no leak', () => {
    const { listenerCount, restore } = mockReducedMotion(false);
    try {
      const { component } = mountReactive({
        value: 1.0, label: 'Po', displayValue: '100W', showPeak: true,
      });
      expect(listenerCount()).toBeGreaterThan(0);
      expect(netActiveIntervals()).toBe(1);

      unmount(component);
      components = components.filter((c) => c !== component);
      expect(listenerCount()).toBe(0);
      expect(netActiveIntervals()).toBe(0);
    } finally {
      restore();
    }
  });
});
