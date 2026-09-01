/**
 * MOR-2149 — the `segmentline` meter form: a segmented LCD bar graph whose
 * two fractions (`fillFraction`, `s9Fraction`) both divide by `max`, mirroring
 * `studioline/__tests__/meters-renderer.test.ts`'s shape. The pin that
 * matters most is the same one both sibling files end on: an unobserved
 * reading must render as unknown, not as a track sitting at zero, which reads
 * as "no signal" — a different fact.
 */
import { describe, it, expect } from 'vitest';
import { invokeRenderer, RendererInputError } from '../../contract';
import type { DesignLanguageTokens } from '../../contract';
import { SEGMENTLINE_TOKENS } from '../tokens';
import { HOT_THRESHOLD, renderMeter } from '../meters-renderer';

const render = (fields: Record<string, number | null>) =>
  renderMeter({ kind: 'meter', fields }, SEGMENTLINE_TOKENS);

describe('the meter takes its segment pitch from the tokens, not a private default', () => {
  it('reads the pitch off SEGMENTLINE_TOKENS.meters', () => {
    const m = render({ value: 0.5, max: 1, s9: 0.6 });
    expect(m.segmentWidthPx).toBe(Number.parseFloat(SEGMENTLINE_TOKENS.meters.trackWidth));
    expect(m.segmentGapPx).toBe(Number.parseFloat(SEGMENTLINE_TOKENS.meters.segmentGap));
  });

  // SEGMENTLINE_TOKENS' own pitch is 7px/3px, which is numerically equal to
  // a plausible fallback default — a test that only checks against
  // SEGMENTLINE_TOKENS cannot tell "read the token" apart from "ignored the
  // token and returned 7/3 anyway". A token set whose pitch does NOT equal
  // 7/3 closes that gap: the output can only match if the renderer actually
  // read the `tokens` parameter it was called with.
  it('changes when the token set changes — the read is load-bearing, not a coincidence', () => {
    const otherTokens: DesignLanguageTokens = {
      ...SEGMENTLINE_TOKENS,
      meters: { trackWidth: '11px', segmentGap: '5px' },
    };
    const m = renderMeter({ kind: 'meter', fields: { value: 0.5, max: 1, s9: 0.6 } }, otherTokens);
    expect(m.segmentWidthPx).toBe(11);
    expect(m.segmentGapPx).toBe(5);
  });
});

describe('fillFraction and s9Fraction are two INDEPENDENT divisions by max', () => {
  // A deliberately asymmetric fixture: value/max and s9/max produce two
  // DIFFERENT fractions (0.25 vs 0.75). A symmetric fixture (e.g. value ===
  // s9) cannot tell a swapped assignment apart from a correct one — this one
  // can: if fillFraction and s9Fraction were transposed, or if either
  // division read the wrong pair of operands, this test fails.
  it('computes fillFraction from value/max and s9Fraction from s9/max, not the other way round', () => {
    const m = render({ value: 3, max: 12, s9: 9 });
    expect(m.fillFraction).toBe(0.25);
    expect(m.s9Fraction).toBe(0.75);
  });

  it('at the real call-site scale (value/s9 already 0..1, max 1), the two fractions equal the raw inputs', () => {
    const m = render({ value: 0.2, max: 1, s9: 0.6 });
    expect(m.fillFraction).toBe(0.2);
    expect(m.s9Fraction).toBe(0.6);
  });

  it('clamps an over-range reading to the track rather than overflowing it', () => {
    expect(render({ value: 40, max: 15, s9: 9 }).fillFraction).toBe(1);
    expect(render({ value: -3, max: 15, s9: 9 }).fillFraction).toBe(0);
  });

  it('s9Fraction defaults to 0 when s9 is absent, independent of the fill', () => {
    expect(render({ value: 6, max: 12 }).s9Fraction).toBe(0);
  });
});

describe('HOT_THRESHOLD is a literal boundary, not an approximation', () => {
  it('is exactly 0.8', () => {
    expect(HOT_THRESHOLD).toBe(0.8);
  });

  it('marks hot AT 0.8 exactly (>=, not >)', () => {
    expect(render({ value: 0.8, max: 1, s9: 0.6 }).hot).toBe(true);
  });

  it('does not mark hot one hundredth below 0.8', () => {
    expect(render({ value: 0.79, max: 1, s9: 0.6 }).hot).toBe(false);
  });
});

describe('an unobserved reading stays unobserved', () => {
  it('renders unknown, never a track resting at zero', () => {
    const m = render({ value: null, max: 1, s9: 0.6 });
    expect(m.unknown).toBe(true);
    expect(m.fillFraction).toBe(0);
    expect(m.hot).toBe(false);
  });

  it('still computes s9Fraction when the value itself is unobserved', () => {
    expect(render({ value: null, max: 15, s9: 9 }).s9Fraction).toBe(0.6);
  });

  it('a genuine zero reading is a DIFFERENT fact from unobserved, and says so', () => {
    expect(render({ value: 0, max: 1, s9: 0.6 })).toMatchObject({ unknown: false, fillFraction: 0 });
  });
});

describe('the renderer survives the MOR-1072 structural gate', () => {
  it('renders through invokeRenderer', () => {
    const viewModel = { kind: 'meter', fields: { value: 0.5, max: 1, s9: 0.6 } };
    expect(invokeRenderer(renderMeter, viewModel, SEGMENTLINE_TOKENS)).toMatchObject({ unknown: false });
  });

  it('cannot be reached with a capability-shaped payload', () => {
    const smuggled = { kind: 'meter', fields: { value: 0.5 }, capabilities: { modes: ['USB'] } };
    expect(() => invokeRenderer(renderMeter, smuggled, SEGMENTLINE_TOKENS)).toThrow(RendererInputError);
  });
});
