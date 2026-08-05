/**
 * MOR-1074 — the `fieldline` meter form: 12 discrete segments, which is what
 * distinguishes it from `studioline`'s continuous bar rail and `meridian`'s
 * needle (MOR-977 §3.1). The pin that matters most is the same one studioline's
 * twin file ends on: an unobserved reading must render as unknown, not as a
 * ladder sitting dark, which reads as "no signal" — a different fact.
 */
import { describe, it, expect } from 'vitest';
import { renderMeter as renderStudioline } from '../../studioline/meters-renderer';
import { STUDIOLINE_TOKENS } from '../../studioline/tokens';
import { FIELDLINE_TOKENS } from '../tokens';
import { FIELDLINE_SCALE_TICKS, FIELDLINE_SEGMENT_COUNT, renderMeter } from '../meters-renderer';

const render = (fields: Record<string, number | null>) =>
  renderMeter({ kind: 'meter', fields }, FIELDLINE_TOKENS);

describe('the meter is a discrete segment ladder', () => {
  it('always renders the same 12 blocks, whatever the reading', () => {
    for (const value of [null, 0, 4, 9, 15, 99]) {
      expect(render({ value }).segments).toHaveLength(FIELDLINE_SEGMENT_COUNT);
    }
  });

  it('takes its block geometry from the token set, gap included', () => {
    const m = render({ value: 5 });
    expect(m.trackWidth).toBe(FIELDLINE_TOKENS.meters.trackWidth);
    expect(m.segmentGap).toBe(FIELDLINE_TOKENS.meters.segmentGap);
    expect(Number.parseFloat(m.segmentGap)).toBeGreaterThan(0);
  });

  it('quantises the reading to a countable number of lit blocks', () => {
    expect(render({ value: 15, max: 15 }).litCount).toBe(12);
    expect(render({ value: 7.5, max: 15 }).litCount).toBe(6);
    expect(render({ value: 0, max: 15 }).litCount).toBe(0);
    // The lit flags and the count never disagree.
    const m = render({ value: 7.5, max: 15 });
    expect(m.segments.filter((s) => s.lit)).toHaveLength(m.litCount!);
  });

  it('clamps an over-range reading to the ladder rather than overflowing it', () => {
    expect(render({ value: 40, max: 15 }).litCount).toBe(12);
    expect(render({ value: -3, max: 15 }).litCount).toBe(0);
  });

  it('zones at S9: blocks below the crossover are RX-toned, above it are not', () => {
    const m = render({ value: 12, max: 15, s9: 9 });
    expect(m.crossoverIndex).toBe(7);
    expect(m.segments.slice(0, 7).every((s) => s.zone === 'normal' && s.tone === FIELDLINE_TOKENS.rx.active)).toBe(true);
    expect(m.segments.slice(7).every((s) => s.zone === 'over' && s.tone === FIELDLINE_TOKENS.tx.tuning)).toBe(true);
  });

  it('holds the peak as ONE segment, never as a second fill', () => {
    const m = render({ value: 4, peak: 12, max: 15 });
    const held = m.segments.filter((s) => s.peakHold);
    expect(held).toHaveLength(1);
    expect(held[0].index).toBe(9);
    expect(render({ value: 4 }).segments.some((s) => s.peakHold)).toBe(false);
  });

  it('renders the scale as marks at the zone boundaries only', () => {
    expect(render({ value: 4 }).scaleTicks).toEqual(FIELDLINE_SCALE_TICKS);
  });

  it('renders an unobserved reading as unknown, never as a dark ladder at zero', () => {
    const m = render({ value: null });
    expect(m.unknown).toBe(true);
    expect(m.litCount).toBeNull();
    expect(m.segments.every((s) => !s.lit)).toBe(true);
    // A genuine zero is a DIFFERENT fact and says so.
    expect(render({ value: 0 })).toMatchObject({ unknown: false, litCount: 0 });
  });
});

describe('the meter form is structurally different from studioline, not recoloured', () => {
  it('quantises where studioline is continuous, at the same reading', () => {
    const mine = render({ value: 7.5, max: 15 });
    const theirs = renderStudioline({ kind: 'meter', fields: { value: 7.5, max: 15 } }, STUDIOLINE_TOKENS);
    expect(theirs.fill).toBe(0.5); // a fraction of one track
    expect(mine.litCount).toBe(6); // a count of blocks
    expect(mine).not.toHaveProperty('fill');
    expect(theirs).not.toHaveProperty('segments');
  });

  it('holds peak as a whole segment where studioline holds a 1px tick', () => {
    const theirs = renderStudioline({ kind: 'meter', fields: { value: 4, peak: 12, max: 15 } }, STUDIOLINE_TOKENS);
    expect(theirs.peakWidthPx).toBe(1);
    expect(render({ value: 4, peak: 12, max: 15 }).segments.filter((s) => s.peakHold)).toHaveLength(1);
  });

  it('agrees with studioline on the UNKNOWN reading — the one fact both must not soften', () => {
    expect(render({ value: null }).unknown).toBe(true);
    expect(renderStudioline({ kind: 'meter', fields: { value: null } }, STUDIOLINE_TOKENS).unknown).toBe(true);
  });
});
