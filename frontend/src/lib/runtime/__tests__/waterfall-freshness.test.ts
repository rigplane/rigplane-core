import { describe, expect, it } from 'vitest';
import {
  WATERFALL_BURST_SPACING_MS,
  WATERFALL_DISCONTINUITY_MS,
  WATERFALL_SKIP_BUDGET_MS,
  WaterfallFreshnessGate,
} from '../waterfall-freshness';

function fakeClock() {
  let now = 0;
  return {
    now: () => now,
    advance: (ms: number) => { now += ms; },
  };
}

function admissions(gate: WaterfallFreshnessGate, clock: ReturnType<typeof fakeClock>, spacings: number[]): boolean[] {
  return spacings.map((spacing) => {
    clock.advance(spacing);
    return gate.admit();
  });
}

describe('waterfall freshness gate', () => {
  it('paints the first delivery of a fresh gate and latches steady painting at the first live-spaced delivery', () => {
    const clock = fakeClock();
    const gate = new WaterfallFreshnessGate(clock);
    const verdicts = admissions(gate, clock, [0, ...Array.from({ length: 5 }, () => 83)]);
    expect(verdicts).toEqual([true, true, true, true, true, true]);
  });

  it('collapses a burst straddling a fresh registration while it drains at burst spacing inside the budget', () => {
    const clock = fakeClock();
    const gate = new WaterfallFreshnessGate(clock);
    expect(gate.admit()).toBe(true);
    // A burst hitting a fresh registration (the providerGeneration remount
    // replaces the gate) paints at most one row while it drains at burst
    // spacing inside the skip budget: 63 frames × 16 ms = 1008 ms < 1500.
    const burst = admissions(gate, clock, Array.from({ length: 63 }, () => 16));
    expect(burst).toEqual(Array.from({ length: 63 }, () => false));
    expect(63 * 16).toBeLessThan(WATERFALL_SKIP_BUDGET_MS);
  });

  it('paints the first delivery after a >3 s gap and every live frame after it (demand off/on, source gap)', () => {
    const clock = fakeClock();
    const gate = new WaterfallFreshnessGate(clock);
    gate.admit();
    admissions(gate, clock, Array.from({ length: 5 }, () => 83));
    // A normal silence: scope demand off, source unavailable. The first
    // live frame after it must paint — no five-row loss — and live frames
    // keep painting.
    clock.advance(4_000);
    expect(gate.admit()).toBe(true);
    expect(admissions(gate, clock, Array.from({ length: 3 }, () => 50))).toEqual([true, true, true]);
  });

  it('collapses a post-gap burst to one painted row and paints the live frames after the drain', () => {
    const clock = fakeClock();
    const gate = new WaterfallFreshnessGate(clock);
    gate.admit();
    // 64-frame deferred burst after a 2 h gap, draining at back-to-back
    // spacing below the burst threshold (no drain-spacing measurement
    // exists yet; only the below/above-threshold split is pinned), then
    // live frames at a spacing above it. The entering frame of the
    // discontinuity paints (one deferred row, accepted cost); the drain
    // collapses; the first live-spaced frame latches steady painting.
    const burst = admissions(gate, clock, [2 * 60 * 60 * 1000, ...Array.from({ length: 63 }, () => 16)]);
    expect(burst[0]).toBe(true);
    expect(burst.slice(1)).toEqual(Array.from({ length: 63 }, () => false));
    const live = admissions(gate, clock, Array.from({ length: 7 }, () => 83));
    expect(live).toEqual(Array.from({ length: 7 }, () => true));
  });

  it('bounds skip mode in wall time: a burst that never slows still resumes painting within the budget', () => {
    const clock = fakeClock();
    const gate = new WaterfallFreshnessGate(clock);
    gate.admit();
    clock.advance(2 * 60 * 60 * 1000);
    expect(gate.admit()).toBe(true); // the discontinuity re-opens the skip window
    // 16 ms × 94 = 1504 ms: the first frame at or past the budget paints
    // and latches; every frame after it paints regardless of spacing, so
    // skip mode can never freeze the waterfall indefinitely.
    const burst = admissions(gate, clock, Array.from({ length: 100 }, () => 16));
    expect(burst.slice(0, 93)).toEqual(Array.from({ length: 93 }, () => false));
    expect(burst.slice(93)).toEqual(Array.from({ length: 7 }, () => true));
    expect(16 * 94).toBeGreaterThanOrEqual(WATERFALL_SKIP_BUDGET_MS);
    expect(16 * 94).toBeLessThanOrEqual(WATERFALL_SKIP_BUDGET_MS + 16);
  });

  it('cannot freeze on batched live deliveries: two-frame batches tighter than burst spacing resume within the budget', () => {
    const clock = fakeClock();
    const gate = new WaterfallFreshnessGate(clock);
    gate.admit();
    clock.advance(2 * 60 * 60 * 1000);
    expect(gate.admit()).toBe(true); // the gap frame paints, the skip window opens
    // Realistic batched live (round-3 review): deliveries arrive in
    // two-frame batches — 10 ms inside the batch, 90 ms between batches
    // (50 ms average, the fps=20 production cadence). The 10 ms member is
    // burst-spaced, but the first 90 ms member paints and latches steady
    // painting, so nothing freezes; only the first batch member is lost.
    const verdicts: boolean[] = [];
    for (let pair = 0; pair < 5; pair += 1) {
      clock.advance(10); verdicts.push(gate.admit());
      clock.advance(90); verdicts.push(gate.admit());
    }
    expect(verdicts[0]).toBe(false);
    expect(verdicts.slice(1)).toEqual(Array.from({ length: 9 }, () => true));
    expect(100).toBeLessThan(WATERFALL_SKIP_BUDGET_MS);
  });

  it('still replays a short-stall burst once painting is latched (status quo below the discontinuity threshold)', () => {
    const clock = fakeClock();
    const gate = new WaterfallFreshnessGate(clock);
    gate.admit();
    // Reach latched painting first (the first live-spaced delivery
    // latches), then a sub-threshold stall keeps admitting.
    admissions(gate, clock, Array.from({ length: 5 }, () => 83));
    const burst = admissions(gate, clock, [500, ...Array.from({ length: 10 }, () => 1)]);
    expect(burst).toEqual(Array.from({ length: 11 }, () => true));
    expect(500).toBeLessThan(WATERFALL_DISCONTINUITY_MS);
  });

  it('paints live frames at the 50 ms audio-fft cadence inside the skip window', () => {
    const clock = fakeClock();
    const gate = new WaterfallFreshnessGate(clock);
    gate.admit();
    clock.advance(60_000);
    expect(gate.admit()).toBe(true);
    // AudioFftScope is wired with explicit fps=20
    // (src/rigplane/web/server.py) and the SDR IQ scope defaults to
    // fps=20 (src/rigplane/sdr/iq_scope.py): live frames produced at
    // 50 ms are live-spaced and paint immediately inside the window.
    expect(50).toBeGreaterThanOrEqual(WATERFALL_BURST_SPACING_MS);
    const live = admissions(gate, clock, Array.from({ length: 6 }, () => 50));
    expect(live).toEqual([true, true, true, true, true, true]);
  });

  it('updates the timing baseline on suppressed deliveries, not just painted ones', () => {
    const clock = fakeClock();
    const gate = new WaterfallFreshnessGate(clock);
    gate.admit();
    clock.advance(2 * 60 * 60 * 1000);
    expect(gate.admit()).toBe(true);
    // The gap frame still counts: spacing is measured between consecutive
    // deliveries, so a burst-drain frame stays burst-drain.
    clock.advance(1);
    expect(gate.admit()).toBe(false);
    clock.advance(1);
    expect(gate.admit()).toBe(false);
  });

  it('treats spacing exactly at the discontinuity threshold as live, not a new discontinuity', () => {
    const clock = fakeClock();
    const gate = new WaterfallFreshnessGate(clock);
    // Exactly WATERFALL_DISCONTINUITY_MS falls on the live side of the
    // `>` boundary: no new skip window opens — the frame latches steady
    // painting (here via the expired initial budget), so the burst-spaced
    // frame right after it paints too.
    const verdicts = admissions(gate, clock, [0, WATERFALL_DISCONTINUITY_MS, 1]);
    expect(verdicts).toEqual([true, true, true]);
    // One millisecond more trips the discontinuity: the entering frame
    // paints, but the burst-spaced frame after it is suppressed inside
    // the freshly opened window.
    const clock2 = fakeClock();
    const gate2 = new WaterfallFreshnessGate(clock2);
    const verdicts2 = admissions(gate2, clock2, [0, WATERFALL_DISCONTINUITY_MS + 1, 1]);
    expect(verdicts2).toEqual([true, true, false]);
  });

  it('counts spacing exactly at the burst threshold as live-spaced', () => {
    const clock = fakeClock();
    const gate = new WaterfallFreshnessGate(clock);
    gate.admit();
    clock.advance(2 * 60 * 60 * 1000);
    expect(gate.admit()).toBe(true);
    // Exactly WATERFALL_BURST_SPACING_MS falls on the live side of the
    // `<` boundary and latches painting; only strictly smaller spacing
    // counts as burst drain.
    const verdicts = admissions(gate, clock, Array.from({ length: 3 }, () => WATERFALL_BURST_SPACING_MS));
    expect(verdicts).toEqual([true, true, true]);
  });
});
