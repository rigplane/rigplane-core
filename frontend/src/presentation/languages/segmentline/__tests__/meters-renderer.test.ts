import { describe, it, expect } from 'vitest';
import { invokeRenderer, RendererInputError } from '../../contract';
import { SEGMENTLINE_TOKENS } from '../tokens';
import { HOT_THRESHOLD, SEGMENTLINE_SEGMENT_COUNT, renderMeter } from '../meters-renderer';

const render = (fields: Record<string, number | null>) =>
  renderMeter({ kind: 'meter', fields }, SEGMENTLINE_TOKENS);

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
    expect(m.hot).toBe(false);
  });

  it('a genuine zero reading is a DIFFERENT fact from unobserved, and says so', () => {
    expect(render({ value: 0, max: 1, s9: 0.6 })).toMatchObject({ unknown: false });
  });
});

describe('MOR-2214: reports MeterDisplay geometry from the token set', () => {
  it('reports the 20-segment count and the token gap on an observed reading', () => {
    const m = render({ value: 0.5, max: 1, s9: 0.6 });
    expect(m.segmentCount).toBe(SEGMENTLINE_SEGMENT_COUNT);
    expect(m.segmentGapPx).toBe(Number.parseFloat(SEGMENTLINE_TOKENS.meters.segmentGap));
  });

  it('reports the same geometry on the unobserved branch too', () => {
    const m = render({ value: null, max: 1, s9: 0.6 });
    expect(m.segmentCount).toBe(SEGMENTLINE_SEGMENT_COUNT);
    expect(m.segmentGapPx).toBe(Number.parseFloat(SEGMENTLINE_TOKENS.meters.segmentGap));
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
