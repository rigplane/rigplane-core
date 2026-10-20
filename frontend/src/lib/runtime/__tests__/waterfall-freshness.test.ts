import { describe, expect, it } from 'vitest';
import {
  WATERFALL_DISCONTINUITY_MS,
  WATERFALL_RESUME_FRAMES,
  WATERFALL_RESUME_SPACING_MS,
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
  it('paints the first delivery of a fresh gate, then re-earns painting after the resume cost', () => {
    const clock = fakeClock();
    const gate = new WaterfallFreshnessGate(clock);
    expect(gate.admit()).toBe(true);
    // A fresh gate starts unsaturated: the first delivery paints (no
    // spacing history to judge), the next four live-spaced deliveries are
    // the resume cost, and the fifth re-earns painting.
    const stream = admissions(gate, clock, Array.from({ length: 7 }, () => 83));
    expect(stream).toEqual([false, false, false, false, true, true, true]);
  });

  it('still replays a short-stall burst (status quo below the discontinuity threshold)', () => {
    const clock = fakeClock();
    const gate = new WaterfallFreshnessGate(clock);
    gate.admit();
    // Reach steady painting first (fresh-gate resume cost), then a
    // sub-threshold stall keeps admitting.
    admissions(gate, clock, Array.from({ length: 5 }, () => 83));
    const burst = admissions(gate, clock, [500, ...Array.from({ length: 10 }, () => 1)]);
    expect(burst).toEqual(Array.from({ length: 11 }, () => true));
    expect(500).toBeLessThan(WATERFALL_DISCONTINUITY_MS);
  });

  it('collapses a post-gap burst and resumes after live-spaced frames re-earn painting', () => {
    const clock = fakeClock();
    const gate = new WaterfallFreshnessGate(clock);
    gate.admit();
    // 64-frame deferred burst after a 2 h gap, draining at back-to-back
    // spacing below the resume threshold, then live frames at a spacing
    // above it (no drain-spacing measurement exists yet; only the
    // below/above-threshold split is pinned).
    const burst = admissions(gate, clock, [2 * 60 * 60 * 1000, ...Array.from({ length: 63 }, () => 16)]);
    expect(burst).toEqual(Array.from({ length: 64 }, () => false));
    const resume = admissions(gate, clock, Array.from({ length: 7 }, () => 83));
    expect(resume.slice(0, WATERFALL_RESUME_FRAMES - 1)).toEqual(Array.from({ length: WATERFALL_RESUME_FRAMES - 1 }, () => false));
    expect(resume[WATERFALL_RESUME_FRAMES - 1]).toBe(true);
    expect(resume.slice(WATERFALL_RESUME_FRAMES)).toEqual([true, true]);
  });

  it('re-earns painting at the 50 ms audio-fft cadence', () => {
    const clock = fakeClock();
    const gate = new WaterfallFreshnessGate(clock);
    gate.admit();
    clock.advance(60_000);
    expect(gate.admit()).toBe(false);
    // AudioFftScope targets fps=20 (src/rigplane/audio/fft_scope.py): live
    // audio-fft frames are produced at 50 ms, just above the resume
    // spacing — their delivery spacing at the render boundary can still
    // batch tighter than production.
    expect(50).toBeGreaterThanOrEqual(WATERFALL_RESUME_SPACING_MS);
    const resume = admissions(gate, clock, Array.from({ length: 6 }, () => 50));
    expect(resume).toEqual([false, false, false, false, true, true]);
  });

  it('updates the timing baseline on suppressed deliveries, not just painted ones', () => {
    const clock = fakeClock();
    const gate = new WaterfallFreshnessGate(clock);
    gate.admit();
    clock.advance(2 * 60 * 60 * 1000);
    expect(gate.admit()).toBe(false);
    // The suppressed delivery still counts: spacing is measured between
    // consecutive deliveries, so a burst-drain frame stays burst-drain.
    clock.advance(1);
    expect(gate.admit()).toBe(false);
  });

  it('resumes only after consecutive live-spaced frames: an interleaved burst frame resets the count', () => {
    const clock = fakeClock();
    const gate = new WaterfallFreshnessGate(clock);
    gate.admit();
    clock.advance(2 * 60 * 60 * 1000);
    expect(gate.admit()).toBe(false);
    expect(admissions(gate, clock, [83, 83, 83])).toEqual([false, false, false]);
    // A burst-spaced frame mid-resume breaks the consecutive run instead
    // of pausing it: a mixed drain/live stretch must restart from zero.
    expect(admissions(gate, clock, [16])).toEqual([false]);
    expect(admissions(gate, clock, Array.from({ length: 5 }, () => 83))).toEqual([false, false, false, false, true]);
  });

  it('treats spacing exactly at the discontinuity threshold as live, not a new discontinuity', () => {
    const clock = fakeClock();
    const gate = new WaterfallFreshnessGate(clock);
    gate.admit();
    clock.advance(2 * 60 * 60 * 1000);
    expect(gate.admit()).toBe(false);
    // Exactly WATERFALL_DISCONTINUITY_MS falls on the live side of the `>`
    // boundary: such deliveries earn resume credit, so the fifth admits.
    const resume = admissions(gate, clock, Array.from({ length: 5 }, () => WATERFALL_DISCONTINUITY_MS));
    expect(resume).toEqual([false, false, false, false, true]);
  });

  it('counts spacing exactly at the resume threshold as live-spaced', () => {
    const clock = fakeClock();
    const gate = new WaterfallFreshnessGate(clock);
    gate.admit();
    clock.advance(2 * 60 * 60 * 1000);
    expect(gate.admit()).toBe(false);
    // Exactly WATERFALL_RESUME_SPACING_MS falls on the live side of the
    // `<` boundary and earns resume credit; only strictly smaller spacing
    // counts as burst drain.
    const resume = admissions(gate, clock, Array.from({ length: 5 }, () => WATERFALL_RESUME_SPACING_MS));
    expect(resume).toEqual([false, false, false, false, true]);
  });
});
