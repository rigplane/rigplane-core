import { describe, it, expect, beforeEach } from 'vitest';
import {
  renderSpectrum, SpectrumRenderer, defaultSpectrumOptions, spectrumDisplayAmplitude,
  defaultSpectrumColorRoles, resolveSpectrumColorRoles, spectrumColorRolesToOptions,
  type SpectrumColorRoles, type SpectrumOptions,
} from '../spectrum-renderer';

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

describe('spectrumDisplayAmplitude', () => {
  it('preserves the established zero, mid-scale, and clamp transfer', () => {
    expect(spectrumDisplayAmplitude(0, 0)).toBe(0);
    expect(spectrumDisplayAmplitude(40, 0)).toBeCloseTo(Math.sqrt(0.5), 8);
    expect(spectrumDisplayAmplitude(80, 0)).toBe(1);
    expect(spectrumDisplayAmplitude(255, 0)).toBe(1);
  });

  it('keeps the existing reference adjustment in the shared transfer', () => {
    expect(spectrumDisplayAmplitude(40, 30)).toBeCloseTo(Math.sqrt(0.75), 8);
    expect(spectrumDisplayAmplitude(40, -30)).toBe(0.5);
  });
});

// ── Colour roles ────────────────────────────────────────────────────────────
//
// PREVIOUS_LITERALS is transcribed from
// `git show 8ff079b1c:frontend/src/lib/renderers/spectrum-renderer.ts`
// (lines 28-31, 140, 180, 184, 198) — the strings this module painted with
// before colour roles existed. It is deliberately a duplicate of the
// production defaults so that changing a default without changing this
// record turns the first test below red.
const PREVIOUS_LITERALS = {
  line: 'rgba(210,220,230,0.85)',
  fillTop: 'rgba(30,58,138,0.30)',
  fillBottom: 'rgba(30,58,138,0.02)',
  grid: 'rgba(255,255,255,0.15)',
  text: 'rgba(180,200,220,0.6)',
  tuneLine: 'rgba(239,68,68,0.75)',
  passbandFill: 'rgba(59,130,246,0.15)',
  passbandEdge: 'rgba(59,130,246,0.4)',
} as const;

/** Records every paint-affecting assignment in the order the renderer makes it. */
function createPaintCtx() {
  const paint = {
    fillStyle: [] as string[],
    strokeStyle: [] as string[],
    gradientStops: [] as [number, string][],
  };
  const noop = () => {};
  const gradient = { addColorStop: (o: number, c: string) => paint.gradientStops.push([o, c]) };
  const ctx = {
    clearRect: noop, closePath: noop, stroke: noop, fill: noop, beginPath: noop,
    fillRect: noop, fillText: noop, moveTo: noop, lineTo: noop, setLineDash: noop,
    createLinearGradient: () => gradient,
    set fillStyle(v: unknown) { if (typeof v === 'string') paint.fillStyle.push(v); },
    set strokeStyle(v: unknown) { if (typeof v === 'string') paint.strokeStyle.push(v); },
    set lineWidth(_: unknown) {}, set font(_: unknown) {}, set textAlign(_: unknown) {},
  } as unknown as CanvasRenderingContext2D;
  return { ctx, paint };
}

const overlayOpts = (o: Partial<SpectrumOptions> = {}): SpectrumOptions => opts({
  spanHz: 1e6, centerHz: 14_500_000, tuneHz: 14_500_000, passbandHz: 2_400, mode: 'USB', ...o,
});

// ── Panorama viewport shift (MOR-2464) ───────────────────────────────────────
//
// Viewport center arrives as `centerHz` (labels, hit tests); the
// sample-to-viewport offset as `panoramaShiftHz`. Uncovered edges stay blank.
describe('panorama viewport shift (MOR-2464)', () => {
  const WIDTH = 100;
  const HEIGHT = 250;
  const baseOpts = (o: Partial<SpectrumOptions> = {}): SpectrumOptions => opts({
    spanHz: 100, centerHz: 50, scopeMode: 0, ...o,
  });
  const peakData = (peakIndex: number, peakValue = 80): Uint8Array => {
    const data = new Uint8Array(100).fill(10);
    data[peakIndex] = peakValue;
    return data;
  };
  // Trace/fill sample points only (vertical grid ends at y === HEIGHT,
  // horizontal grid at x === WIDTH).
  const tracePoints = (log: { lineTo: number[][] }) =>
    log.lineTo.filter(([x, y]) => x !== WIDTH && y < HEIGHT);
  const yAt = (log: { lineTo: number[][] }, x: number) =>
    log.lineTo.filter(([px]) => px === x).map(([, y]) => y);

  it('shifts trace samples left by the viewport shift and blanks the uncovered right edge', () => {
    const { ctx, log } = createMockCtx();
    renderSpectrum(ctx, peakData(50), WIDTH, HEIGHT, baseOpts({ panoramaShiftHz: 10 }));
    expect(tracePoints(log).some(([x, y]) => x === 40 && y === 0)).toBe(true);
    expect(Math.max(...tracePoints(log).map(([x]) => x))).toBe(89);
    expect(tracePoints(log).some(([x]) => x === 90)).toBe(false);
  });

  it('shifts trace samples right for a negative shift and blanks the uncovered left edge', () => {
    const { ctx, log } = createMockCtx();
    renderSpectrum(ctx, peakData(50), WIDTH, HEIGHT, baseOpts({ panoramaShiftHz: -10 }));
    expect(tracePoints(log).some(([x, y]) => x === 60 && y === 0)).toBe(true);
    expect(Math.min(...tracePoints(log).map(([x]) => x))).toBe(10);
    expect(tracePoints(log).some(([x]) => x === 9)).toBe(false);
  });

  it('interpolates adjacent samples for sub-pixel shifts', () => {
    const data = peakData(50, 30);
    data[51] = 80;
    const shifted = createMockCtx();
    renderSpectrum(shifted.ctx, data, WIDTH, HEIGHT, baseOpts({ panoramaShiftHz: 0.5 }));
    const expectedY = HEIGHT * (1 - Math.sqrt(55 / 80));
    expect(tracePoints(shifted.log).some(([x, y]) => x === 50 && Math.abs(y - expectedY) < 0.01))
      .toBe(true);
    const unshifted = createMockCtx();
    renderSpectrum(unshifted.ctx, data, WIDTH, HEIGHT, baseOpts());
    const unshiftedY = HEIGHT * (1 - Math.sqrt(30 / 80));
    expect(tracePoints(unshifted.log)
      .some(([x, y]) => x === 50 && Math.abs(y - unshiftedY) < 0.01)).toBe(true);
    expect(Math.abs(expectedY - unshiftedY)).toBeGreaterThan(1);
  });

  it('keeps the CTR carrier marker at width/2 while the panorama is shifted', () => {
    const { ctx, log } = createMockCtx();
    renderSpectrum(ctx, peakData(50), WIDTH, HEIGHT, baseOpts({
      tuneHz: 55, panoramaShiftHz: 10,
    }));
    // WIDTH/2 is also a grid position: grid one moveTo, marker the second.
    expect(log.moveTo.filter(([x]) => x === WIDTH / 2)).toHaveLength(2);
  });

  it('renders the resting profile with the established nearest-bin mapping', () => {
    // width 40 over 20 samples: bin spans two screen columns.
    const data = new Uint8Array(20).fill(0);
    data[10] = 80;
    const { ctx, log } = createMockCtx();
    renderSpectrum(ctx, data, 40, 200, opts({ spanHz: 40, centerHz: 20, scopeMode: 0 }));
    expect(yAt(log, 18)).toContain(200);
    expect(yAt(log, 19)).toContain(200);
    expect(yAt(log, 20)).toContain(0);
    expect(yAt(log, 21)).toContain(0);
    expect(yAt(log, 22)).toContain(200);
    // Full-run fill closes at the pre-MOR-2464 right-bottom corner.
    expect(log.lineTo).toContainEqual([40, 200]);
  });

  it('interpolates the translated screen profile, not native bins, on fractional shifts', () => {
    const data = new Uint8Array(20).fill(0);
    data[10] = 80;
    const { ctx, log } = createMockCtx();
    renderSpectrum(ctx, data, 40, 200, opts({
      spanHz: 40, centerHz: 20, scopeMode: 0, panoramaShiftHz: 0.5,
    }));
    // Column 18 is inside the data[9] step; the edge at 19/20 blends
    // screen columns only (a native-bin mix would lift column 18).
    expect(yAt(log, 18)).toContain(200);
    const blended = 200 * (1 - Math.sqrt(40 / 80));
    expect(yAt(log, 19).some((y) => Math.abs(y - blended) < 1e-6)).toBe(true);
    expect(yAt(log, 20)).toContain(0);
  });

  it('keeps the FIX carrier marker proportional and unaffected by shift semantics', () => {
    const { ctx, log } = createMockCtx();
    renderSpectrum(ctx, peakData(50), WIDTH, HEIGHT, baseOpts({
      tuneHz: 25, scopeMode: 1, panoramaShiftHz: 0,
    }));
    expect(log.moveTo.some(([x]) => x === 25)).toBe(true);
    expect(log.moveTo.filter(([x]) => x === WIDTH / 2)).toHaveLength(1);
  });

  it('labels frequencies through the shifted viewport center', () => {
    const { ctx, log } = createMockCtx();
    renderSpectrum(ctx, peakData(50), WIDTH, HEIGHT, baseOpts({
      spanHz: 100_000, centerHz: 14_501_000,
    }));
    expect(log.fillText[0][0]).toBe('14.451');
    expect(log.fillText[10][0]).toBe('14.551');
  });

  it('shifts the peak-hold line with the same viewport', () => {
    const renderer = new SpectrumRenderer();
    renderer.setPeakHoldEnabled(true);
    const first = createMockCtx();
    renderer.render(first.ctx, peakData(50), WIDTH, HEIGHT, baseOpts());
    const second = createMockCtx();
    renderer.render(second.ctx, new Uint8Array(100).fill(10), WIDTH, HEIGHT,
      baseOpts({ panoramaShiftHz: 10 }));
    expect(tracePoints(second.log).some(([x, y]) => x === 40 && y === 0)).toBe(true);
  });
});

// ── Source-geometry reset (MOR-2464) ─────────────────────────────────────────
//
// Averaging history and peak hold reset when the SOURCE window changes
// (geometryKey) and survive viewport animation.
describe('SpectrumRenderer source-geometry reset (MOR-2464)', () => {
  it('keeps averaging across viewport-only changes, resets on geometry change', () => {
    const renderer = new SpectrumRenderer();
    for (let i = 0; i < 5; i++) {
      renderer.render(createMockCtx().ctx, new Uint8Array(10).fill(60), 10, 100, opts({ geometryKey: 'window-a' }));
    }
    const animated = createMockCtx();
    renderer.render(animated.ctx, new Uint8Array(10).fill(10), 10, 100,
      opts({ geometryKey: 'window-a', centerHz: 14_051_000, panoramaShiftHz: 1_000 }));
    const freshA = createMockCtx();
    new SpectrumRenderer().render(freshA.ctx, new Uint8Array(10).fill(10), 10, 100, opts({ geometryKey: 'window-a' }));
    expect(animated.log.lineTo).not.toEqual(freshA.log.lineTo);
    const moved = createMockCtx();
    renderer.render(moved.ctx, new Uint8Array(10).fill(10), 10, 100, opts({ geometryKey: 'window-b' }));
    const freshB = createMockCtx();
    new SpectrumRenderer().render(freshB.ctx, new Uint8Array(10).fill(10), 10, 100, opts({ geometryKey: 'window-b' }));
    expect(moved.log.lineTo).toEqual(freshB.log.lineTo);
  });

  it('keeps peak hold across viewport-only changes, drops it on geometry change', () => {
    const peakY = 100 * (1 - Math.sqrt(70 / 80));
    const renderer = new SpectrumRenderer();
    renderer.render(createMockCtx().ctx, new Uint8Array(10).fill(70), 10, 100, opts({ geometryKey: 'window-a' }));
    const animated = createMockCtx();
    renderer.render(animated.ctx, new Uint8Array(10).fill(10), 10, 100,
      opts({ geometryKey: 'window-a', panoramaShiftHz: 500 }));
    expect(animated.log.lineTo.some(([, y]) => Math.abs(y - peakY) < 0.5)).toBe(true);
    const moved = createMockCtx();
    renderer.render(moved.ctx, new Uint8Array(10).fill(10), 10, 100, opts({ geometryKey: 'window-b' }));
    expect(moved.log.lineTo.some(([, y]) => Math.abs(y - peakY) < 0.5)).toBe(false);
  });
});

describe('spectrum colour roles', () => {
  it('resolves to the literals this renderer painted with before roles existed', () => {
    expect(resolveSpectrumColorRoles()).toEqual({
      trace: PREVIOUS_LITERALS.line,
      traceFill: { top: PREVIOUS_LITERALS.fillTop, bottom: PREVIOUS_LITERALS.fillBottom },
      grid: PREVIOUS_LITERALS.grid,
      axisText: PREVIOUS_LITERALS.text,
      tuneLine: PREVIOUS_LITERALS.tuneLine,
      passbandFill: PREVIOUS_LITERALS.passbandFill,
      passbandEdge: PREVIOUS_LITERALS.passbandEdge,
    });
    expect(resolveSpectrumColorRoles()).toEqual(defaultSpectrumColorRoles);
  });

  it('resolves an undefined-valued override to the default, nested fill included', () => {
    // `Partial<SpectrumColorRoles>` admits an explicit `undefined` for every
    // top-level role; the nested cast stands for a host record that carries
    // one under `traceFill` too.
    const overrides: Partial<SpectrumColorRoles> = {
      tuneLine: undefined,
      traceFill: { top: undefined, bottom: undefined } as unknown as SpectrumColorRoles['traceFill'],
    };
    expect(resolveSpectrumColorRoles(overrides)).toEqual(defaultSpectrumColorRoles);
    expect(spectrumColorRolesToOptions(resolveSpectrumColorRoles(overrides))).toMatchObject({
      tuneLineColor: PREVIOUS_LITERALS.tuneLine,
      fillColor: PREVIOUS_LITERALS.fillTop,
      fillColorBottom: PREVIOUS_LITERALS.fillBottom,
    });
  });

  it('carries the resolved roles into the option fields of the same names', () => {
    const roles = resolveSpectrumColorRoles({ trace: '#111111', traceFill: { top: '#222222', bottom: '#333333' } });
    expect(spectrumColorRolesToOptions(roles)).toEqual({
      lineColor: '#111111',
      fillColor: '#222222',
      fillColorBottom: '#333333',
      gridColor: PREVIOUS_LITERALS.grid,
      textColor: PREVIOUS_LITERALS.text,
      tuneLineColor: PREVIOUS_LITERALS.tuneLine,
      passbandFillColor: PREVIOUS_LITERALS.passbandFill,
      passbandEdgeColor: PREVIOUS_LITERALS.passbandEdge,
    });
  });

  it('keeps the default option colours equal to the previous literals', () => {
    expect(defaultSpectrumOptions).toMatchObject({
      lineColor: PREVIOUS_LITERALS.line,
      fillColor: PREVIOUS_LITERALS.fillTop,
      fillColorBottom: PREVIOUS_LITERALS.fillBottom,
      gridColor: PREVIOUS_LITERALS.grid,
      textColor: PREVIOUS_LITERALS.text,
      tuneLineColor: PREVIOUS_LITERALS.tuneLine,
      passbandFillColor: PREVIOUS_LITERALS.passbandFill,
      passbandEdgeColor: PREVIOUS_LITERALS.passbandEdge,
    });
  });

  it('paints the default overlay pass with the previous tune-line and passband literals', () => {
    const { ctx, paint } = createPaintCtx();
    renderSpectrum(ctx, data50(), 800, 400, overlayOpts());
    expect(paint.gradientStops).toEqual([[0, PREVIOUS_LITERALS.fillTop], [1, PREVIOUS_LITERALS.fillBottom]]);
    expect(paint.fillStyle).toEqual([PREVIOUS_LITERALS.text, PREVIOUS_LITERALS.passbandFill]);
    expect(paint.strokeStyle).toEqual([
      PREVIOUS_LITERALS.grid,
      PREVIOUS_LITERALS.line,
      PREVIOUS_LITERALS.passbandEdge,
      PREVIOUS_LITERALS.tuneLine,
    ]);
  });

  it('paints the overlay pass with the supplied colours instead of the previous literals', () => {
    const { ctx, paint } = createPaintCtx();
    renderSpectrum(ctx, data50(), 800, 400, overlayOpts({
      ...spectrumColorRolesToOptions(resolveSpectrumColorRoles({
        trace: '#010101', traceFill: { top: '#020202', bottom: '#030303' }, grid: '#040404',
        axisText: '#050505', tuneLine: '#060606', passbandFill: '#070707', passbandEdge: '#080808',
      })),
    }));
    expect(paint.gradientStops).toEqual([[0, '#020202'], [1, '#030303']]);
    expect(paint.fillStyle).toEqual(['#050505', '#070707']);
    expect(paint.strokeStyle).toEqual(['#040404', '#010101', '#080808', '#060606']);
    for (const literal of Object.values(PREVIOUS_LITERALS)) {
      expect([...paint.fillStyle, ...paint.strokeStyle]).not.toContain(literal);
    }
  });

  it('rebuilds the cached gradient when only the bottom stop changes', () => {
    const cache = { current: null } as Parameters<typeof renderSpectrum>[5];
    const first = createPaintCtx();
    renderSpectrum(first.ctx, data50(), 800, 400, opts(), cache);
    const second = createPaintCtx();
    renderSpectrum(second.ctx, data50(), 800, 400, opts({ fillColorBottom: '#090909' }), cache);
    expect(second.paint.gradientStops).toEqual([[0, PREVIOUS_LITERALS.fillTop], [1, '#090909']]);
  });
});
