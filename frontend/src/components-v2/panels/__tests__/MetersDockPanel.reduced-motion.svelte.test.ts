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
// smoothing.isolated.test.ts / LinearSMeter.reduced-motion.svelte.test.ts,
// matching the established per-file convention in this codebase (no shared
// test util) — this is test-only code, not production LOC.

// Capability state is seeded into the REAL store, not vi.mock'd: this file
// runs in the `fast` pool (`isolate: false`), where a module-scope mock races
// the shared module cache — a sibling file can leave the panel's dependency
// chain bound to a different module instance than the one the mock applies
// to (see #2408/#2409).
import type { Capabilities } from '$lib/types/capabilities';
import { clearCapabilities, setCapabilities } from '$lib/stores/capabilities.svelte';

function makeCaps(): Capabilities {
  return {
    model: 'IC-7610',
    scope: true,
    audio: true,
    tx: true,
    capabilities: ['scope', 'tx'],
    receivers: 2,
    vfoScheme: 'main_sub',
    freqRanges: [{ start: 1800000, end: 30000000, label: 'HF' }],
    modes: ['USB', 'LSB', 'CW', 'AM', 'FM'],
    filters: ['FIL1', 'FIL2', 'FIL3'],
    audioConfig: { sampleRate: 48000, channels: 1, codecs: ['opus'] },
    webrtc: { available: true, enabled: false },
    txBands: null,
    stateContractVersion: 1,
    providerGeneration: 0,
    // MOR-1470: a declared power table puts the panel in the engineering
    // domain — powerMeter props below are watts, exactly what the backend
    // publishes for a rig with this table (MOR-469).
    meterCalibrations: {
      power: [
        { raw: 0, actual: 0, label: '0' },
        { raw: 143, actual: 50, label: '50' },
        { raw: 212, actual: 100, label: '100' },
      ],
    },
  };
}

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
  setCapabilities(makeCaps());
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
  clearCapabilities();
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

function peakMarkerLeft(t: HTMLElement, meterKey = 'po'): string | null {
  const el = t.querySelector(
    `[data-meter="${meterKey}"] [data-testid="peak-marker"]`,
  ) as HTMLElement | null;
  return el?.style.left ?? null;
}

function tileBarFillWidth(t: HTMLElement, meterKey = 'po'): string | null {
  const el = t.querySelector(`[data-meter="${meterKey}"] .tile-bar-fill`) as HTMLElement | null;
  return el?.style.width ?? null;
}

describe('MetersDockPanel — prefers-reduced-motion (MOR-1249)', () => {
  it('schedules no decay interval when reduced motion is preferred at mount', () => {
    const { restore } = mockReducedMotion(true);
    try {
      mountReactive({ powerMeter: 100, txActive: true });
      expect(setIntervalSpy).not.toHaveBeenCalled();
      expect(netActiveIntervals()).toBe(0);
    } finally {
      restore();
    }
  });

  it('still schedules the decay interval when reduced motion is NOT preferred (baseline)', () => {
    const { restore } = mockReducedMotion(false);
    try {
      mountReactive({ powerMeter: 100, txActive: true });
      expect(setIntervalSpy).toHaveBeenCalledTimes(1);
      expect(netActiveIntervals()).toBe(1);
    } finally {
      restore();
    }
  });

  it('snaps the Po NUMBER directly to the live raw sample when reduced motion is preferred (no glide)', () => {
    const { restore } = mockReducedMotion(true);
    try {
      // 100 W peak; 2 W trough — engineering domain (MOR-1470).
      const { t, state } = mountReactive({ powerMeter: 100, txActive: true });
      expect(poNumber(t)).toBe(100);

      // A drop must be reflected IMMEDIATELY (no held peak decaying toward
      // it over PEAK_DECAY_MS) — proving there is no gliding animation, and
      // proving it without advancing any timers (none are scheduled).
      state.powerMeter = 2;
      flushSync();
      expect(poNumber(t)).toBeLessThan(10);
    } finally {
      restore();
    }
  });

  // MOR-1252 owner decision (2026-08-04, option b): the peak marker is a
  // STATIC HOLD under reduced motion, not a removal — it latches at the
  // highest observed value and stays visible while the NUMBER (pinned live
  // above) keeps tracking the raw sample. This replaces the MOR-1249-era
  // removal pin (`snapPeaks()` used to clear `peaks[key]` outright, which
  // gated the marker's render condition to always-false under reduce).
  it('KILL: renders and holds the peak marker under reduced motion, decoupled from the live NUMBER (MOR-1252 static hold)', () => {
    const { restore } = mockReducedMotion(true);
    try {
      const { t, state } = mountReactive({ powerMeter: 100, txActive: true });
      expect(peakMarkerCount(t)).toBe(1);

      // A drop must NOT move or remove the held marker — only the NUMBER
      // (already pinned live above) reflects the new sample. A mutant that
      // reverts to clearing `peaks[key]` under reduce (the old removal
      // fix) makes the marker vanish here.
      state.powerMeter = 2;
      flushSync();
      expect(peakMarkerCount(t)).toBe(1);
      expect(poNumber(t)).toBeLessThan(10); // number stays live, not frozen
    } finally {
      restore();
    }
  });

  it('KILL: does not move the held peak marker when fed a further-lower value (true static hold)', () => {
    const { restore } = mockReducedMotion(true);
    try {
      const { t, state } = mountReactive({ powerMeter: 100, txActive: true });
      const leftAfterMount = peakMarkerLeft(t);
      expect(leftAfterMount).toBeTruthy();

      state.powerMeter = 2;
      flushSync();
      expect(peakMarkerLeft(t)).toBe(leftAfterMount); // unchanged — latch held

      state.powerMeter = 1;
      flushSync();
      expect(peakMarkerLeft(t)).toBe(leftAfterMount); // still unchanged
    } finally {
      restore();
    }
  });

  it('KILL: resets the held peak instantly (no glide) once the hold window elapses, with no interval ever scheduled', () => {
    const { restore } = mockReducedMotion(true);
    try {
      const { t, state } = mountReactive({ powerMeter: 100, txActive: true });
      const highLeft = peakMarkerLeft(t);
      expect(highLeft).toBeTruthy();

      state.powerMeter = 2;
      flushSync();
      expect(peakMarkerLeft(t)).toBe(highLeft); // held

      // Advance well past PEAK_DECAY_MS (1500ms) with no new sample — the
      // static-hold design is event-driven (computed on the next real
      // update), not a ticking reset: nothing should fire on its own.
      vi.advanceTimersByTime(2000);
      expect(netActiveIntervals()).toBe(0);
      expect(peakMarkerLeft(t)).toBe(highLeft); // still held — no timer reset it

      // The next genuine sample after expiry resets in a single synchronous
      // jump, landing exactly at the fresh sample's own live position (not
      // decayed/interpolated toward it) — proving the reset is a re-seat,
      // not a partial glide.
      state.powerMeter = 3;
      flushSync();
      const resetLeft = peakMarkerLeft(t);
      expect(resetLeft).not.toBe(highLeft);
      expect(parseFloat(resetLeft ?? '')).toBeCloseTo(parseFloat(tileBarFillWidth(t) ?? ''), 5);
    } finally {
      restore();
    }
  });
});

describe('MetersDockPanel — runtime prefers-reduced-motion flips (MOR-1249, mirrors MOR-1233 F1/F2)', () => {
  it('KILL F1: stops the live decay interval and snaps the peak to current when reduced motion turns ON mid-session', () => {
    const { setMatches, restore } = mockReducedMotion(false);
    try {
      const { t, state } = mountReactive({ powerMeter: 100, txActive: true });
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
      state.powerMeter = 2;
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
      const { t, state } = mountReactive({ powerMeter: 100, txActive: true });
      expect(poNumber(t)).toBe(100); // snapped at mount (reduce was ON)
      expect(netActiveIntervals()).toBe(0); // no interval yet

      // OS preference clears while mounted — the interval must resume and
      // a fresh peak must latch (re-seated from the current live value).
      setMatches(false);
      expect(netActiveIntervals()).toBe(1);

      // Drop the raw signal shortly after the flip and advance one tick:
      // with peak-hold active again, the NUMBER must hold near the peak
      // rather than tracking the trough instantaneously.
      state.powerMeter = 2;
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
      mountReactive({ powerMeter: 100, txActive: true });
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
      const { component } = mountReactive({ powerMeter: 100, txActive: true });
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
