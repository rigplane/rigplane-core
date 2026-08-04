import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import MetersDockPanel from '../MetersDockPanel.svelte';

// MOR-1249: MetersDockPanel's setInterval-driven peak-hold decay (the
// Po/SWR/ALC/Id peak markers, PEAK_DECAY_MS) previously ran unconditionally,
// ignoring `prefers-reduced-motion`. After MOR-1233 snapped the bar-fill
// smoother, this left the panel internally inconsistent: the bar snapped
// while the peak marker glided. These tests pin the fix, mirroring the
// MOR-1233 precedent already shipped for LinearSMeter's own rAF peak-hold
// loop: under reduced motion the decay interval is not scheduled at all (no
// ticks), any already-latched peak snaps to the live raw value (never
// freezes at a stale position), and the preference is honored on RUNTIME
// flips in both directions.
//
// The MatchMediaMock + mockReducedMotion() helper is duplicated from
// smoothing.svelte.test.ts / LinearSMeter.reduced-motion.svelte.test.ts,
// matching the established per-file convention in this codebase (no shared
// test util) — this is test-only code, not production LOC.

vi.mock('$lib/stores/capabilities.svelte', () => ({
  hasTx: vi.fn(() => true),
}));
vi.mock('$lib/runtime/adapters/capabilities-adapter', () => ({
  getMeterCalibration: vi.fn(() => null),
  getMeterRedline: vi.fn(() => null),
}));

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
  // Anchor wall-clock time; stepAllPeaks() reads Date.now() for latch/decay.
  vi.setSystemTime(0);
  // Vitest's fake timers also fake requestAnimationFrame, and the panel's
  // (MOR-1233-covered, unrelated) per-tile bar-fill smoother schedules an
  // rAF frame of its own on mount whenever reduced motion is off — which
  // would pollute a global `vi.getTimerCount()` read. Spying directly on
  // setInterval/clearInterval (call-through, so fake-timer scheduling still
  // works) isolates the assertions to the ONE thing this fix owns: the
  // peak-decay interval's lifecycle.
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

// Net live intervals created by MetersDockPanel's own code: every
// setInterval call in this component is paired 1:1 with a guarded
// clearInterval call (see startTicking/stopTicking), so this difference is
// exactly 0 or 1 — never more, per the interval-lifecycle requirement.
function netActiveIntervals(): number {
  return setIntervalSpy.mock.calls.length - clearIntervalSpy.mock.calls.length;
}

// A $state props proxy so prop mutations re-render the mounted component.
function mountReactive(props: Record<string, unknown>) {
  const state = $state(props);
  const t = document.createElement('div');
  document.body.appendChild(t);
  roots.push(t);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const component = mount(MetersDockPanel as any, { target: t, props: state });
  flushSync();
  components.push(component);
  return { t, state, component };
}

function poNumber(t: HTMLElement): number {
  return parseInt(t.querySelector('[data-meter="po"] .tile-value')?.textContent ?? '0', 10);
}

function peakMarkerCount(t: HTMLElement): number {
  return t.querySelectorAll('[data-testid="peak-marker"]').length;
}

describe('MetersDockPanel — prefers-reduced-motion (MOR-1249)', () => {
  it('schedules no decay interval when reduced motion is preferred at mount', () => {
    const { restore } = mockReducedMotion(true);
    try {
      mountReactive({ powerMeter: 212, txActive: true });
      expect(setIntervalSpy).not.toHaveBeenCalled();
      expect(netActiveIntervals()).toBe(0);
    } finally {
      restore();
    }
  });

  it('still schedules the decay interval when reduced motion is NOT preferred (baseline)', () => {
    const { restore } = mockReducedMotion(false);
    try {
      mountReactive({ powerMeter: 212, txActive: true });
      expect(setIntervalSpy).toHaveBeenCalledTimes(1);
      expect(netActiveIntervals()).toBe(1);
    } finally {
      restore();
    }
  });

  it('snaps the Po NUMBER directly to the live raw sample when reduced motion is preferred (no glide)', () => {
    const { restore } = mockReducedMotion(true);
    try {
      // raw 212 -> 100W; raw 5 -> ~2W (see MetersDockPanel.peakhold test).
      const { t, state } = mountReactive({ powerMeter: 212, txActive: true });
      expect(poNumber(t)).toBe(100);

      // A drop must be reflected IMMEDIATELY (no held peak decaying toward
      // it over PEAK_DECAY_MS) — proving there is no gliding animation, and
      // proving it without advancing any timers (none are scheduled).
      state.powerMeter = 5;
      flushSync();
      expect(poNumber(t)).toBeLessThan(10);
    } finally {
      restore();
    }
  });

  // Removal semantics — provisional pending MOR-1252: the marker is gated
  // on latch existence (`peaks[tile.key] !== undefined`), not on a value
  // comparison, so its disappearance here is a deliberate implementation
  // choice (mirroring LinearSMeter's shipped removal), not an unavoidable
  // consequence of the snap fix. This test pins the CURRENT behavior; it
  // is expected to change if MOR-1252 rules for a static/visible hold
  // instead of removal.
  it('renders no peak marker under reduced motion (removal semantics — provisional pending MOR-1252)', () => {
    const { restore } = mockReducedMotion(true);
    try {
      const { t } = mountReactive({ powerMeter: 212, txActive: true });
      expect(peakMarkerCount(t)).toBe(0);
    } finally {
      restore();
    }
  });
});

describe('MetersDockPanel — runtime prefers-reduced-motion flips (MOR-1249, mirrors MOR-1233 F1/F2)', () => {
  it('KILL F1: stops the live decay interval and snaps the peak to current when reduced motion turns ON mid-session', () => {
    const { setMatches, restore } = mockReducedMotion(false);
    try {
      const { t, state } = mountReactive({ powerMeter: 212, txActive: true });
      // Latch a peak, then let it partially decay so the marker is live.
      vi.advanceTimersByTime(100);
      flushSync();
      expect(poNumber(t)).toBe(100);
      expect(netActiveIntervals()).toBe(1);

      // OS preference flips to "reduce" while the decay interval is live.
      setMatches(true);
      expect(netActiveIntervals()).toBe(0); // the interval was torn down

      // Drop the raw signal; without any timer advancing, the number must
      // reflect the live trough immediately — proving the held peak was
      // cleared (snapped), not merely paused mid-decay (frozen).
      state.powerMeter = 5;
      flushSync();
      expect(poNumber(t)).toBeLessThan(10);

      // Further time passing schedules nothing new.
      vi.advanceTimersByTime(5000);
      expect(netActiveIntervals()).toBe(0);
    } finally {
      restore();
    }
  });

  it('KILL F2: resumes the live decay interval and peak-hold behavior when reduced motion turns OFF mid-session', () => {
    const { setMatches, restore } = mockReducedMotion(true);
    try {
      const { t, state } = mountReactive({ powerMeter: 212, txActive: true });
      expect(poNumber(t)).toBe(100); // snapped at mount (reduce was ON)
      expect(netActiveIntervals()).toBe(0); // no interval yet

      // OS preference clears while mounted — the interval must resume and
      // a fresh peak must latch (re-seated from the current live value).
      setMatches(false);
      expect(netActiveIntervals()).toBe(1);

      // Drop the raw signal shortly after the flip and advance one tick:
      // with peak-hold active again, the NUMBER must hold near the peak
      // rather than tracking the trough instantaneously.
      state.powerMeter = 5;
      vi.advanceTimersByTime(100);
      flushSync();
      expect(poNumber(t)).toBeGreaterThan(70);
    } finally {
      restore();
    }
  });

  it('multiple flips leave exactly one or zero intervals scheduled, never more', () => {
    const { setMatches, restore } = mockReducedMotion(false);
    try {
      mountReactive({ powerMeter: 212, txActive: true });
      expect(netActiveIntervals()).toBe(1);

      setMatches(true);
      expect(netActiveIntervals()).toBe(0);
      setMatches(true); // redundant flip — must stay idempotent
      expect(netActiveIntervals()).toBe(0);

      setMatches(false);
      expect(netActiveIntervals()).toBe(1);
      setMatches(false); // redundant flip — must not create a second interval
      expect(netActiveIntervals()).toBe(1);

      setMatches(true);
      expect(netActiveIntervals()).toBe(0);
      setMatches(false);
      expect(netActiveIntervals()).toBe(1);
    } finally {
      restore();
    }
  });

  it('detaches the reduced-motion change listener and clears the interval on unmount — no leak', () => {
    const { listenerCount, restore } = mockReducedMotion(false);
    try {
      const { component } = mountReactive({ powerMeter: 212, txActive: true });
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
