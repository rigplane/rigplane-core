/**
 * MOR-1073 — the `studioline` meter form: a continuous bar rail, which is
 * what distinguishes it from `fieldline`'s discrete segments and `meridian`'s
 * needle (MOR-977 §3.1). The pin that matters is the LAST one: an unobserved
 * reading must render as unknown, not as a rail sitting at zero, which reads
 * as "no signal" — a different fact.
 */
import { describe, it, expect } from 'vitest';
import { STUDIOLINE_TOKENS } from '../tokens';
import { PEAK_TICK_WIDTH_PX, STUDIOLINE_SCALE_TICKS, renderMeter } from '../meters-renderer';

const render = (fields: Record<string, number | null>) =>
  renderMeter({ kind: 'meter', fields }, STUDIOLINE_TOKENS);

describe('the meter is a continuous rail', () => {
  it('takes its track geometry from the token set, with no segment gap', () => {
    const m = render({ value: 5 });
    expect(m.trackWidth).toBe(STUDIOLINE_TOKENS.meters.trackWidth);
    expect(m.segmentGap).toBe('0px');
  });

  it('fills as a fraction of the track and splits tone at S9', () => {
    expect(render({ value: 9, max: 15, s9: 9 })).toMatchObject({ fill: 0.6, crossover: 0.6 });
    expect(render({ value: 15, max: 15 }).fill).toBe(1);
  });

  it('clamps an over-range reading to the track rather than overflowing it', () => {
    expect(render({ value: 40, max: 15 }).fill).toBe(1);
    expect(render({ value: -3, max: 15 }).fill).toBe(0);
  });

  it('holds the peak as a 1px tick, not a second fill', () => {
    expect(render({ value: 4, peak: 12, max: 15 })).toMatchObject({ peak: 0.8, peakWidthPx: PEAK_TICK_WIDTH_PX });
    expect(render({ value: 4 }).peak).toBeNull();
  });

  it('renders the scale as sparse text ticks below the rail', () => {
    expect(render({ value: 4 }).scaleTicks).toEqual(STUDIOLINE_SCALE_TICKS);
  });

  it('renders an unobserved reading as unknown, never as a rail at zero', () => {
    const m = render({ value: null });
    expect(m.unknown).toBe(true);
    expect(m.fill).toBeNull();
    expect(render({ value: 0 })).toMatchObject({ unknown: false, fill: 0 });
  });
});
