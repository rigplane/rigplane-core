import { describe, it, expect, beforeEach } from 'vitest';
import { renderSpectrum, SpectrumRenderer, defaultSpectrumOptions, type SpectrumOptions } from '../spectrum-renderer';

function createMockCtx() {
  const log = {
    fillRect: [] as number[][], fillText: [] as [string, number, number][],
    moveTo: [] as number[][], lineTo: [] as number[][],
    setLineDash: [] as number[][], strokes: 0, fills: 0,
  };
  const noop = () => {};
  const ctx = {
    clearRect: noop, closePath: noop, stroke: () => { log.strokes++; },
    fill: () => { log.fills++; }, beginPath: noop,
    fillRect: (x: number, y: number, w: number, h: number) => log.fillRect.push([x, y, w, h]),
    fillText: (t: string, x: number, y: number) => log.fillText.push([t, x, y]),
    moveTo: (x: number, y: number) => log.moveTo.push([x, y]),
    lineTo: (x: number, y: number) => log.lineTo.push([x, y]),
    setLineDash: (d: number[]) => log.setLineDash.push(d),
    createLinearGradient: () => ({ addColorStop: noop }),
    set fillStyle(_: any) {}, set strokeStyle(_: any) {},
    set lineWidth(_: any) {}, set font(_: any) {}, set textAlign(_: any) {},
  } as unknown as CanvasRenderingContext2D;
  return { ctx, log };
}

const opts = (o: Partial<SpectrumOptions> = {}): SpectrumOptions => ({ ...defaultSpectrumOptions, ...o });
const data50 = () => new Uint8Array(50).fill(30);

describe('renderSpectrum', () => {
  it.each([
    ['empty data', new Uint8Array(0), 800, 400],
    ['zero width', new Uint8Array(100), 0, 400],
    ['zero height', new Uint8Array(100), 800, 0],
  ])('early return for %s', (_, d, w, h) => {
    const { ctx, log } = createMockCtx();
    renderSpectrum(ctx, d, w, h, opts());
    expect(log.strokes).toBe(0);
  });

  it('draws grid: 6 horizontal + 11 vertical lines', () => {
    const { ctx, log } = createMockCtx();
    renderSpectrum(ctx, data50(), 800, 400, opts());
    expect(log.strokes).toBeGreaterThanOrEqual(17);
    expect(log.lineTo.filter(([x]) => x === 800).length).toBeGreaterThanOrEqual(6);
    expect(log.lineTo.filter(([, y]) => y === 400).length).toBeGreaterThanOrEqual(11);
  });

  it('renders 11 frequency labels when span/center set', () => {
    const { ctx, log } = createMockCtx();
    renderSpectrum(ctx, data50(), 800, 400, opts({ spanHz: 1e6, centerHz: 14_500_000 }));
    expect(log.fillText.length).toBe(11);
    expect(log.fillText[0][0]).toBe('14.000');
    expect(log.fillText[10][0]).toBe('15.000');
  });

  it.each([
    ['spanHz=0', { spanHz: 0, centerHz: 14e6 }],
    ['centerHz=0', { spanHz: 1e6, centerHz: 0 }],
  ])('skips labels when %s', (_, o) => {
    const { ctx, log } = createMockCtx();
    renderSpectrum(ctx, data50(), 800, 400, opts(o));
    expect(log.fillText.length).toBe(0);
  });

  it('suppresses RF labels and carrier overlays without changing the audio trace geometry', () => {
    const { ctx, log } = createMockCtx();
    const reference = createMockCtx();
    const pixels = new Uint8Array([0, 20, 80, 40, 0]);
    renderSpectrum(ctx, pixels, 80, 40, opts({
      spanHz: 12000, centerHz: 6000, showRfOverlays: false,
      tuneHz: 6000, passbandHz: 2400, mode: 'USB',
    }));
    renderSpectrum(reference.ctx, pixels, 80, 40, opts());
    expect(log.fillText).toEqual([]);
    expect(log.fillRect).toEqual(reference.log.fillRect);
    expect(log.lineTo).toEqual(reference.log.lineTo);
    expect(log.strokes).toBe(reference.log.strokes);
  });

  it('clamps label x within [20, width-20]', () => {
    const { ctx, log } = createMockCtx();
    renderSpectrum(ctx, data50(), 800, 400, opts({ spanHz: 1e6, centerHz: 14_500_000 }));
    for (const [, x] of log.fillText) expect(x).toBeGreaterThanOrEqual(20);
    for (const [, x] of log.fillText) expect(x).toBeLessThanOrEqual(780);
  });

  it('maps amplitude 0→bottom, 80→top', () => {
    const { ctx: c0, log: l0 } = createMockCtx();
    renderSpectrum(c0, new Uint8Array(10).fill(0), 10, 200, opts());
    expect(l0.lineTo.some(([, y]) => y === 200)).toBe(true);
    const { ctx: c80, log: l80 } = createMockCtx();
    renderSpectrum(c80, new Uint8Array(10).fill(80), 10, 200, opts());
    expect(l80.lineTo.some(([, y]) => y === 0)).toBe(true);
  });

  it('refLevel shifts amplitude mapping', () => {
    const d = new Uint8Array(10).fill(40);
    const { ctx: c1, log: l1 } = createMockCtx();
    const { ctx: c2, log: l2 } = createMockCtx();
    renderSpectrum(c1, d, 10, 200, opts({ refLevel: 0 }));
    renderSpectrum(c2, d, 10, 200, opts({ refLevel: 30 }));
    expect(l1.lineTo).not.toEqual(l2.lineTo);
  });

  it('draws passband overlay with dashed edges', () => {
    const { ctx, log } = createMockCtx();
    renderSpectrum(ctx, data50(), 800, 400, opts({
      spanHz: 1e6, centerHz: 14_074_000, tuneHz: 14_074_000,
      passbandHz: 3000, mode: 'USB', scopeMode: 0,
    }));
    expect(log.fillRect.length).toBeGreaterThanOrEqual(1);
    expect(log.setLineDash.length).toBe(2); // set dash + reset
  });

  it('no passband when passbandHz=0', () => {
    const { ctx, log } = createMockCtx();
    renderSpectrum(ctx, data50(), 800, 400, opts({
      spanHz: 1e6, centerHz: 14_074_000, passbandHz: 0, mode: 'USB',
    }));
    expect(log.setLineDash.length).toBe(0);
  });

  it('CTR mode: tune indicator at midpoint', () => {
    const { ctx, log } = createMockCtx();
    renderSpectrum(ctx, data50(), 800, 400, opts({
      spanHz: 1e6, centerHz: 14_074_000, tuneHz: 14_074_000, scopeMode: 0,
    }));
    expect(log.moveTo.some(([x]) => x === 400)).toBe(true);
  });

  it('FIX mode: tune indicator at frequency position', () => {
    const { ctx, log } = createMockCtx();
    renderSpectrum(ctx, data50(), 800, 400, opts({
      spanHz: 1e6, centerHz: 14_500_000, tuneHz: 14_250_000, scopeMode: 1,
    }));
    // tunePx = ((14_250_000 - 14_000_000) / 1_000_000) * 800 = 200
    expect(log.moveTo.some(([x]) => x === 200)).toBe(true);
  });

  it('fills bg only when bgColor is not transparent', () => {
    const hasBg = (o: Partial<SpectrumOptions>) => {
      const { ctx, log } = createMockCtx();
      renderSpectrum(ctx, new Uint8Array(10).fill(30), 100, 50, opts(o));
      return log.fillRect.some(([x, y, w, h]) => x === 0 && y === 0 && w === 100 && h === 50);
    };
    expect(hasBg({ bgColor: '#000' })).toBe(true);
    expect(hasBg({})).toBe(false);
  });
});

describe('SpectrumRenderer', () => {
  let renderer: SpectrumRenderer;
  beforeEach(() => { renderer = new SpectrumRenderer(); });

  it('renders without errors', () => {
    const { ctx } = createMockCtx();
    expect(() => renderer.render(ctx, data50(), 800, 400, opts())).not.toThrow();
  });

  it('averaging: smooths output across frames', () => {
    // Build history with high values
    for (let i = 0; i < 10; i++) {
      const { ctx } = createMockCtx();
      renderer.render(ctx, new Uint8Array(10).fill(60), 10, 100, opts());
    }
    const { ctx: c1, log: l1 } = createMockCtx();
    renderer.render(c1, new Uint8Array(10).fill(10), 10, 100, opts());
    // Fresh renderer with same low input — no history
    const fresh = new SpectrumRenderer();
    const { ctx: c2, log: l2 } = createMockCtx();
    fresh.render(c2, new Uint8Array(10).fill(10), 10, 100, opts());
    expect(l1.lineTo).not.toEqual(l2.lineTo);
  });

  it('setAvgEnabled(false) clears history', () => {
    renderer.setPeakHoldEnabled(false);
    for (let i = 0; i < 5; i++) {
      const { ctx } = createMockCtx();
      renderer.render(ctx, new Uint8Array(10).fill(60), 10, 100, opts());
    }
    renderer.setAvgEnabled(false);
    const { ctx: c1, log: l1 } = createMockCtx();
    renderer.render(c1, new Uint8Array(10).fill(10), 10, 100, opts());

    const fresh = new SpectrumRenderer();
    fresh.setAvgEnabled(false);
    fresh.setPeakHoldEnabled(false);
    const { ctx: c2, log: l2 } = createMockCtx();
    fresh.render(c2, new Uint8Array(10).fill(10), 10, 100, opts());
    expect(l1.lineTo).toEqual(l2.lineTo);
  });

  it('peak hold draws extra fill overlay by default', () => {
    const { ctx, log } = createMockCtx();
    renderer.render(ctx, new Uint8Array(10).fill(50), 10, 100, opts());
    expect(log.fills).toBeGreaterThanOrEqual(2);
  });

  it('setPeakHoldEnabled(false) suppresses peak overlay', () => {
    renderer.setPeakHoldEnabled(false);
    const { ctx, log } = createMockCtx();
    renderer.render(ctx, new Uint8Array(10).fill(50), 10, 100, opts());
    expect(log.fills).toBe(1);
  });

  it('peak hold retains peaks from prior high-amplitude frames', () => {
    const { ctx: c1 } = createMockCtx();
    renderer.render(c1, new Uint8Array(10).fill(70), 10, 100, opts());
    const { ctx: c2, log: l2 } = createMockCtx();
    renderer.render(c2, new Uint8Array(10).fill(10), 10, 100, opts());
    expect(l2.fills).toBeGreaterThanOrEqual(2);
  });
});
