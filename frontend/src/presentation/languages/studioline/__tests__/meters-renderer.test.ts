/**
 * MOR-1073 — the `studioline` meter form: a continuous bar rail, which is
 * what distinguishes it from `fieldline`'s discrete segments and `meridian`'s
 * needle (MOR-977 §3.1). The pin that matters is the LAST one: an unobserved
 * reading must render as unknown, not as a rail sitting at zero, which reads
 * as "no signal" — a different fact.
 */
import { describe, it, expect } from 'vitest';
import { STUDIOLINE_TOKENS } from '../tokens';
import { PEAK_TICK_WIDTH_PX, renderMeter } from '../meters-renderer';

const render = (fields: Record<string, number | null>) =>
  renderMeter({ kind: 'meter', fields }, STUDIOLINE_TOKENS);

describe('the meter is a continuous rail', () => {
  it('takes its track geometry from the token set', () => {
    const m = render({ value: 5 });
    expect(m.trackWidth).toBe(STUDIOLINE_TOKENS.meters.trackWidth);
    expect(m.segmentGap).toBe('1px');
  });

  it('MOR-2214: reports the default 20-segment geometry as MeterDisplay', () => {
    const m = render({ value: 5 });
    expect(m.segmentCount).toBe(20);
    expect(m.segmentGapPx).toBe(1);
  });

  it('fills as a fraction of the track', () => {
    expect(render({ value: 9, max: 15, s9: 9 })).toMatchObject({ fill: 0.6 });
    expect(render({ value: 15, max: 15 }).fill).toBe(1);
  });

  it('MOR-2250: reports the tone split as toneBelowS9/toneAboveS9, reusing the same rx.active/tx.tuning reads as tone/overTone', () => {
    const m = render({ value: 9, max: 15, s9: 9 });
    expect(m.toneBelowS9).toBe(m.tone);
    expect(m.toneAboveS9).toBe(m.overTone);
    expect(m.toneBelowS9).toBe(STUDIOLINE_TOKENS.rx.active);
    expect(m.toneAboveS9).toBe(STUDIOLINE_TOKENS.tx.tuning);
  });

  it('clamps an over-range reading to the track rather than overflowing it', () => {
    expect(render({ value: 40, max: 15 }).fill).toBe(1);
    expect(render({ value: -3, max: 15 }).fill).toBe(0);
  });

  // MOR-2250 (PR 2 of 2): `peak`/`scaleTicks` were deleted as dead fields
  // (no consumer anywhere — `renderSlot`'s `display` extraction never pulled
  // them, and nothing else read them either); `peakWidthPx` survived that
  // sweep because `fieldline/__tests__/meters-renderer.test.ts` DOES read it
  // (`theirs.peakWidthPx`, a cross-language comparison) — pinned directly
  // here too, not just via that indirect check.
  it('reports the 1px peak-tick width', () => {
    expect(render({ value: 4 }).peakWidthPx).toBe(PEAK_TICK_WIDTH_PX);
  });

  it('renders an unobserved reading as unknown, never as a rail at zero', () => {
    const m = render({ value: null });
    expect(m.unknown).toBe(true);
    expect(m.fill).toBeNull();
    expect(render({ value: 0 })).toMatchObject({ unknown: false, fill: 0 });
  });
});
