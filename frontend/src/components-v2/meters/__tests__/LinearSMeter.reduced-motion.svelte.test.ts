import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { ComponentProps } from 'svelte';

// MOR-1451: `smeter-scale.ts` has no hardcoded fallback calibration curve —
// an uncalibrated radio collapses `value` (dB-rel-S9) toward the raw-scale
// identity mapping, which drags a `value: 20` fixture from "~S9+20" (near
// full-scale) down to a few segments and starves the peak-hold ballistics
// below of the travel distance they're built to exercise. None of the
// assertions here are about calibration correctness — they need *some*
// stable calibrated domain, so this fixture (the numbers `rigs/ic7610.toml`
// happens to declare) stands in for "a radio profile published a curve".
const IC7610_LIKE_CAL = [
  { raw: 0, actual: -54, label: 'S0' },
  { raw: 26, actual: -48, label: 'S1' },
  { raw: 52, actual: -36, label: 'S3' },
  { raw: 78, actual: -24, label: 'S5' },
  { raw: 103, actual: -12, label: 'S7' },
  { raw: 130, actual: 0, label: 'S9' },
  { raw: 165, actual: 10, label: 'S9+10' },
  { raw: 200, actual: 20, label: 'S9+20' },
  { raw: 240, actual: 40, label: 'S9+40' },
];

// The curve is seeded into the REAL capabilities store, not vi.mock'd: this
// file runs in the `fast` pool (`isolate: false`), where a module-scope mock
// races the shared module cache — a sibling file can leave `smeter-scale.ts`
// bound to a different module instance than the one the mock applies to.
// Seeding real store state is deterministic under any cache order.
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
    meterCalibrations: { s_meter: IC7610_LIKE_CAL },
  };
}

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
  setCapabilities(makeCaps());
});

afterEach(() => {
  components.forEach((component) => unmount(component));
  roots.forEach((root) => root.remove());
  components = [];
  roots = [];
  vi.restoreAllMocks();
  clearCapabilities();
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

  // MOR-1252 owner decision (2026-08-04, option b): under reduced motion the
  // peak indicator is a STATIC HOLD, not a removal — it latches at the
  // highest observed value and stays visible (motion removed, information
  // kept) instead of vanishing. This replaces the MOR-1233-era removal pin
  // above (peakSegs forced to smoother.value on every update, so the gap
  // that gates `showPeak` was always 0).
  it('KILL: holds the peak marker statically after a value drop, instead of vanishing (MOR-1252 static hold)', () => {
    const { restore } = mockReducedMotion(true);
    try {
      const { target, state } = mountReactive({ value: 20 });
      flushSync();
      // At mount the peak is freshly captured at the current value — no gap
      // yet, so no marker (unchanged from before).
      expect(peakLineCount(target)).toBe(0);

      // A drop must LATCH the prior peak, not erase it: the marker appears
      // and holds at the old high-water mark rather than tracking the bar
      // down to 0. A mutant reverting to the old
      // `peakSegs = smoother.value` behavior collapses the gap to 0 and
      // this assertion fails.
      state.value = 0;
      flushSync();
      expect(peakLineCount(target)).toBe(1);
    } finally {
      restore();
    }
  });

  it('KILL: does not move the held peak marker when fed a further-lower value (true static hold, not a slow follow)', () => {
    const { restore } = mockReducedMotion(true);
    try {
      const { target, state } = mountReactive({ value: 20 });
      flushSync();

      state.value = 0;
      flushSync();
      const firstLine = target.querySelector('line[stroke-width="2"]');
      const xAfterFirstDrop = firstLine?.getAttribute('x1');
      expect(xAfterFirstDrop).toBeTruthy();

      // A further, even-lower value within the same hold window must NOT
      // move the latch — a mutant that keeps recomputing the peak as some
      // function of the live value (rather than truly holding it) would
      // shift the marker here.
      state.value = -30;
      flushSync();
      const secondLine = target.querySelector('line[stroke-width="2"]');
      expect(secondLine?.getAttribute('x1')).toBe(xAfterFirstDrop);
    } finally {
      restore();
    }
  });

  it('KILL: resets the held peak instantly (no glide, no scheduled frames) once the hold window elapses', () => {
    const { restore } = mockReducedMotion(true);
    vi.useFakeTimers();
    vi.setSystemTime(0);
    try {
      const { target, state } = mountReactive({ value: 20 });
      flushSync();

      state.value = 0;
      flushSync();
      expect(peakLineCount(target)).toBe(1);
      rafSpy.mockClear();

      // Advance well past the 1s hold window with NO new sample arriving.
      // The static-hold design is event-driven (computed on the next real
      // update), not a ticking reset — nothing should fire on its own, and
      // no rAF/interval should ever be scheduled under reduced motion.
      vi.advanceTimersByTime(1500);
      expect(peakLineCount(target)).toBe(1); // still held — no timer reset it
      expect(rafSpy).not.toHaveBeenCalled();

      // The next genuine sample after expiry resets in a single synchronous
      // jump: the marker collapses back onto the live bar immediately
      // (peakSegs re-seats to the new current value), with no intermediate
      // decaying frame ever rendered.
      state.value = -20; // distinct from the previous value, still well below the old peak
      flushSync();
      expect(peakLineCount(target)).toBe(0); // reset landed instantly
      expect(rafSpy).not.toHaveBeenCalled(); // reset was computed, not animated
    } finally {
      vi.useRealTimers();
      restore();
    }
  });

  // J2 (verifier finding, MOR-1252 review): the reduce branch reads AND
  // writes peakSegs/peakTime, so without untrack() it depends on its own
  // writes and self-invalidates — it only converged incidentally (the
  // re-seat is idempotent and `||` short-circuits). The verifier's Z5
  // mutant (a step-glide reset instead of an instant jump) demonstrated
  // ~140 synchronous effect passes inside a single flush before landing on
  // the same end state, which is why a *bound* on this effect's pass count
  // is the cheap regression pin — not just its converged output.
  //
  // `performance.now()` is called only from this effect while reduced
  // motion holds (the smoother's own rAF-driven call site never runs under
  // reduce, and the non-reduce branch is dead code on this path), so its
  // call count is a precise, direct proxy for how many times the effect
  // body actually executed for one external update.
  //
  // A drop (current < peakSegs, within the hold window) never writes, so it
  // cannot discriminate a missing untrack() — measured empirically at
  // exactly 1 call with or without the wrap, since nothing invalidates a
  // dependency that was never written. A RISE (current >= peakSegs, the
  // write-back path) is what discriminates: measured empirically at
  // exactly 1 call with untrack() correctly scoping the read/write, vs 2
  // without it (the effect re-runs once more after writing peakSegs to a
  // genuinely new value, before the idempotent second pass settles) —
  // proving this effect's dependency set no longer includes its own
  // writes.
  it('KILL J2: the reduce-branch peak effect does not self-invalidate on a new-peak write (bounded performance.now() calls)', () => {
    const { restore } = mockReducedMotion(true);
    const nowSpy = vi.spyOn(performance, 'now');
    try {
      const { state } = mountReactive({ value: 20 });
      flushSync();
      nowSpy.mockClear();

      // Drop: no write occurs (current < peakSegs, hold window not
      // expired) — sanity check that this path stays a single read, not a
      // kill signal on its own.
      state.value = 0;
      flushSync();
      expect(nowSpy.mock.calls.length).toBe(1);

      // Rise: current >= peakSegs, so this DOES write peakSegs/peakTime —
      // the actual kill signal. Exactly 1 call proves the effect ran
      // exactly once; a missing untrack() measures 2 (see comment above).
      nowSpy.mockClear();
      state.value = 40;
      flushSync();
      expect(nowSpy.mock.calls.length).toBe(1);
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
