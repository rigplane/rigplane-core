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
  it('admits the first delivery and a steady live-spaced stream', () => {
    const clock = fakeClock();
    const gate = new WaterfallFreshnessGate(clock);
    expect(gate.admit()).toBe(true);
    expect(admissions(gate, clock, [83, 83, 83, 83, 83, 83, 83])).toEqual([true, true, true, true, true, true, true]);
  });

  it('still replays a short-stall burst (status quo below the discontinuity threshold)', () => {
    const clock = fakeClock();
    const gate = new WaterfallFreshnessGate(clock);
    gate.admit();
    const burst = admissions(gate, clock, [500, ...Array.from({ length: 10 }, () => 1)]);
    expect(burst).toEqual(Array.from({ length: 11 }, () => true));
    expect(500).toBeLessThan(WATERFALL_DISCONTINUITY_MS);
  });

  it('collapses a post-gap burst and resumes after live-spaced frames re-earn painting', () => {
    const clock = fakeClock();
    const gate = new WaterfallFreshnessGate(clock);
    gate.admit();
    // 64-frame deferred burst after a 2 h gap, draining at back-to-back
    // spacing (~16 ms measured in MOR-3471), then live frames at ~83 ms.
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
    // AudioFftScope targets fps=20 (src/rigplane/audio/fft_scope.py), so live
    // audio-fft frames arrive just above the resume spacing.
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
});
