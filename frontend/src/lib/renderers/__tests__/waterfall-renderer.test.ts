import { describe, it, expect, vi } from 'vitest';
import {
  COLOR_SCHEMES,
  WaterfallRenderer,
  defaultWaterfallOptions,
  type WaterfallOptions,
} from '../waterfall-renderer';

function mockCanvas(w = 100, h = 50): HTMLCanvasElement {
  const imgData = { width: w, height: 1, data: new Uint8ClampedArray(w * 4) } as unknown as ImageData;
  const ctx = {
    canvas: { width: w, height: h } as HTMLCanvasElement,
    createImageData: () => imgData,
    putImageData: vi.fn(), drawImage: vi.fn(), fillRect: vi.fn(),
    set fillStyle(_: string) {},
  } as unknown as CanvasRenderingContext2D;
  (ctx.canvas as any).width = w;
  (ctx.canvas as any).height = h;
  return { width: w, height: h, getContext: () => ctx } as unknown as HTMLCanvasElement;
}

function makeRenderer(w = 100, h = 50, opts?: Partial<WaterfallOptions>): WaterfallRenderer {
  return new WaterfallRenderer(mockCanvas(w, h), { ...defaultWaterfallOptions, ...opts });
}

// ── COLOR_SCHEMES ────────────────────────────────────────────────────────────

describe('COLOR_SCHEMES', () => {
  it('contains classic, thermal, and grayscale palettes', () => {
    expect(Object.keys(COLOR_SCHEMES)).toEqual(['classic', 'thermal', 'grayscale']);
  });

  it.each(Object.entries(COLOR_SCHEMES))('%s starts at 0 and ends at 1', (_name, stops) => {
    expect(stops[0].stop).toBe(0);
    expect(stops[stops.length - 1].stop).toBe(1);
  });

  it('grayscale interpolates black to white', () => {
    expect(COLOR_SCHEMES.grayscale).toEqual([
      { stop: 0.0, color: '#000000' },
      { stop: 1.0, color: '#FFFFFF' },
    ]);
  });
});

// ── constructor ──────────────────────────────────────────────────────────────

describe('WaterfallRenderer constructor', () => {
  it('throws when getContext returns null', () => {
    const canvas = { getContext: () => null } as unknown as HTMLCanvasElement;
    expect(() => new WaterfallRenderer(canvas, defaultWaterfallOptions)).toThrow('Cannot get 2d context');
  });

  it('creates without error for a valid canvas', () => {
    expect(() => makeRenderer()).not.toThrow();
  });

  it('handles zero-size canvas without error', () => {
    expect(() => makeRenderer(0, 0)).not.toThrow();
  });
});

// ── pixelToFreq ──────────────────────────────────────────────────────────────

describe('pixelToFreq', () => {
  it('maps left edge to low frequency', () => {
    const r = makeRenderer(200, 50, { centerHz: 14_074_000, spanHz: 100_000 });
    expect(r.pixelToFreq(0)).toBe(14_074_000 - 50_000);
  });

  it('maps center pixel to center frequency', () => {
    const r = makeRenderer(200, 50, { centerHz: 14_074_000, spanHz: 100_000 });
    expect(r.pixelToFreq(100)).toBe(14_074_000);
  });

  it('maps right edge to high frequency', () => {
    const r = makeRenderer(200, 50, { centerHz: 14_074_000, spanHz: 100_000 });
    expect(r.pixelToFreq(200)).toBe(14_074_000 + 50_000);
  });

  it('returns centerHz when spanHz is zero', () => {
    const r = makeRenderer(200, 50, { centerHz: 7_000_000, spanHz: 0 });
    expect(r.pixelToFreq(42)).toBe(7_000_000);
  });
});

// ── pushRow ──────────────────────────────────────────────────────────────────

describe('pushRow', () => {
  it('does not throw for normal scope data', () => {
    const r = makeRenderer();
    expect(() => r.pushRow(new Uint8Array(100).fill(40))).not.toThrow();
  });

  it('does not throw for empty data', () => {
    const r = makeRenderer();
    expect(() => r.pushRow(new Uint8Array(0))).not.toThrow();
  });

  it('does nothing after destroy', () => {
    const r = makeRenderer();
    r.destroy();
    expect(() => r.pushRow(new Uint8Array(100).fill(40))).not.toThrow();
  });

  it('handles data length different from canvas width', () => {
    const r = makeRenderer(200, 50);
    expect(() => r.pushRow(new Uint8Array(50).fill(30))).not.toThrow();
    expect(() => r.pushRow(new Uint8Array(400).fill(60))).not.toThrow();
  });
});

// ── updateOptions ────────────────────────────────────────────────────────────

describe('updateOptions', () => {
  it('switches color scheme', () => {
    const r = makeRenderer(100, 50, { colorScheme: 'classic' });
    r.updateOptions({ colorScheme: 'thermal' });
    // verify it renders without error after scheme change
    expect(() => r.pushRow(new Uint8Array(100).fill(50))).not.toThrow();
  });

  it('updates refLevel', () => {
    const r = makeRenderer();
    r.updateOptions({ refLevel: 15 });
    expect(() => r.pushRow(new Uint8Array(100).fill(40))).not.toThrow();
  });

  it('merges partial options preserving existing values', () => {
    const r = makeRenderer(100, 50, { centerHz: 14_000_000, spanHz: 100_000 });
    r.updateOptions({ refLevel: -10 });
    // centerHz should still work
    expect(r.pixelToFreq(50)).toBe(14_000_000);
  });
});

// ── history clearing on confirmed span change (MOR-1479) ─────────────────────
//
// SPAN change remaps every waterfall row's pixel→frequency (pixelToFreq uses
// options.spanHz directly), so old rows drawn under the previous span bend/
// jump at the seam. Owner ruling: clear the backlog on SPAN change only —
// center-frequency retunes are explicitly out of scope (separate ruling).

describe('history clearing on confirmed span change', () => {
  it('does not clear on the very first span observation (initial fill)', () => {
    const r = makeRenderer(100, 50, { spanHz: 0 });
    const clearSpy = vi.spyOn(r, 'clear');
    r.updateOptions({ spanHz: 100_000 });
    expect(clearSpy).not.toHaveBeenCalled();
  });

  it('clears history when the confirmed span changes', () => {
    const r = makeRenderer(100, 50, { spanHz: 0 });
    r.updateOptions({ spanHz: 100_000 }); // establish baseline (first observation)
    const clearSpy = vi.spyOn(r, 'clear');
    r.updateOptions({ spanHz: 200_000 });
    expect(clearSpy).toHaveBeenCalledTimes(1);
  });

  it('does not clear when the reported span is unchanged (no-op observation)', () => {
    const r = makeRenderer(100, 50, { spanHz: 0 });
    r.updateOptions({ spanHz: 100_000 }); // baseline
    const clearSpy = vi.spyOn(r, 'clear');
    r.updateOptions({ spanHz: 100_000 });
    expect(clearSpy).not.toHaveBeenCalled();
  });

  it('does not clear when an unrelated option changes (spanHz omitted)', () => {
    const r = makeRenderer(100, 50, { spanHz: 0 });
    r.updateOptions({ spanHz: 100_000 }); // baseline
    const clearSpy = vi.spyOn(r, 'clear');
    r.updateOptions({ refLevel: 5 });
    r.updateOptions({ colorScheme: 'thermal' });
    expect(clearSpy).not.toHaveBeenCalled();
  });

  it('clears on each step of a rapid double span change', () => {
    const r = makeRenderer(100, 50, { spanHz: 0 });
    r.updateOptions({ spanHz: 100_000 }); // baseline
    const clearSpy = vi.spyOn(r, 'clear');
    r.updateOptions({ spanHz: 200_000 });
    r.updateOptions({ spanHz: 300_000 });
    expect(clearSpy).toHaveBeenCalledTimes(2);
  });

  it('holds/pauses without clearing: repeated same-span pushes stay quiet', () => {
    const r = makeRenderer(100, 50, { spanHz: 0 });
    r.updateOptions({ spanHz: 100_000 }); // baseline
    const clearSpy = vi.spyOn(r, 'clear');
    for (let i = 0; i < 5; i++) {
      r.updateOptions({ spanHz: 100_000 });
    }
    expect(clearSpy).not.toHaveBeenCalled();
  });

  it('does not clear across a disconnect/reconnect gap that returns to the same span', () => {
    const r = makeRenderer(100, 50, { spanHz: 0 });
    r.updateOptions({ spanHz: 100_000 }); // baseline
    const clearSpy = vi.spyOn(r, 'clear');
    r.updateOptions({ spanHz: 0 }); // disconnect / no-data placeholder
    r.updateOptions({ spanHz: 100_000 }); // reconnect, same span as before
    expect(clearSpy).not.toHaveBeenCalled();
  });

  it('clears when the span differs from before a disconnect gap', () => {
    const r = makeRenderer(100, 50, { spanHz: 0 });
    r.updateOptions({ spanHz: 100_000 }); // baseline
    const clearSpy = vi.spyOn(r, 'clear');
    r.updateOptions({ spanHz: 0 }); // disconnect
    r.updateOptions({ spanHz: 150_000 }); // reconnect with a different span
    expect(clearSpy).toHaveBeenCalledTimes(1);
  });
});

// ── panorama viewport reprojection (MOR-2464) ────────────────────────────────
//
// History reprojects by viewport-center deltas (centerHz) only; the
// sampling offset (panoramaShiftHz) places new rows. Uncovered edges stay
// blank; hit tests map through the displayed viewport center.

describe('panorama viewport reprojection (MOR-2464)', () => {
  function trackedCanvas(w = 100, h = 50) {
    const rows: Array<{ width: number; height: number; data: Uint8ClampedArray }> = [];
    const draws: unknown[][] = [];
    const fills: number[][] = [];
    const ctx = {
      canvas: { width: w, height: h },
      createImageData: (width: number) => ({ width, height: 1, data: new Uint8ClampedArray(width * 4) }),
      putImageData: (img: { width: number; height: number; data: Uint8ClampedArray }) => rows.push(img),
      drawImage: (...args: unknown[]) => draws.push(args),
      fillRect: (x: number, y: number, rw: number, rh: number) => fills.push([x, y, rw, rh]),
      set fillStyle(_: string) {},
    } as unknown as CanvasRenderingContext2D;
    const canvas = { width: w, height: h, getContext: () => ctx } as unknown as HTMLCanvasElement;
    return { canvas, rows, draws, fills };
  }

  function trackedRenderer(w = 100, h = 50) {
    const tracked = trackedCanvas(w, h);
    const renderer = new WaterfallRenderer(tracked.canvas, {
      ...defaultWaterfallOptions, centerHz: 14_050_000, spanHz: 100_000,
    });
    return { renderer, ...tracked };
  }

  // drawImage overloads: per-push scroll targets dy = 1; reprojects dy = 0.
  const reprojectDraws = (draws: unknown[][]) => draws.filter((d) => d[6] === 0);

  const peakRow = (...indices: number[]): Uint8Array => {
    const data = new Uint8Array(100).fill(0);
    for (const index of indices.length ? indices : [50]) data[index] = 80;
    return data;
  };
  const isRed = (row: { data: Uint8ClampedArray }, x: number): boolean =>
    row.data[x * 4] === 255 && row.data[x * 4 + 1] === 0 && row.data[x * 4 + 2] === 0;
  const isBlank = (row: { data: Uint8ClampedArray }, x: number): boolean =>
    row.data[x * 4] === 0x00 && row.data[x * 4 + 1] === 0x10 && row.data[x * 4 + 2] === 0x20;

  const VIEWPORT_A = 14_050_000;
  const VIEWPORT_B = 14_051_000;

  it('reprojects history by the viewport-center delta, never by the sampling offset', () => {
    const { renderer, draws, fills } = trackedRenderer();
    renderer.updateOptions({ centerHz: VIEWPORT_B, panoramaShiftHz: 1_000 });
    expect(reprojectDraws(draws)).toEqual([
      [expect.anything(), 1, 0, 99, 50, 0, 0, 99, 50],
    ]);
    expect(fills).toContainEqual([99, 0, 1, 50]);
    // A sampling-offset change alone (same viewport) must not reproject.
    renderer.updateOptions({ panoramaShiftHz: 2_000 });
    expect(reprojectDraws(draws)).toHaveLength(1);
  });

  it('reprojects a negative viewport delta to the right and blanks the left strip', () => {
    const { renderer, draws, fills } = trackedRenderer();
    renderer.updateOptions({ centerHz: VIEWPORT_A - 10_000 });
    expect(reprojectDraws(draws)).toEqual([
      [expect.anything(), 0, 0, 90, 50, 10, 0, 90, 50],
    ]);
    expect(fills).toContainEqual([0, 0, 10, 50]);
  });

  it('does not move history when recentered samples arrive at an unchanged viewport', () => {
    const { renderer, rows, draws } = trackedRenderer();
    // Samples centered on A; the viewport glides A→B (1 px at this span).
    renderer.pushRow(peakRow(50));
    renderer.updateOptions({ centerHz: VIEWPORT_B, panoramaShiftHz: 1_000 });
    renderer.pushRow(peakRow(50));
    expect(reprojectDraws(draws)).toEqual([
      [expect.anything(), 1, 0, 99, 50, 0, 0, 99, 50],
    ]);
    // The recentered frame (samples centered on B) arrives; the viewport
    // stays on B: history must not move, and the fresh row must land on the
    // same RF positions as the history.
    renderer.updateOptions({ centerHz: VIEWPORT_B, panoramaShiftHz: 0 });
    expect(reprojectDraws(draws)).toHaveLength(1);
    renderer.pushRow(peakRow(49, 50));
    const row = rows.at(-1)!;
    expect(isRed(row, 49)).toBe(true);
    expect(isRed(row, 50)).toBe(true);
    expect(isRed(row, 48)).toBe(false);
  });

  it('rounds fractional viewport deltas without cumulative drift', () => {
    const { renderer, draws } = trackedRenderer();
    renderer.updateOptions({ centerHz: VIEWPORT_A + 400 });
    expect(reprojectDraws(draws)).toHaveLength(0);
    renderer.updateOptions({ centerHz: VIEWPORT_A + 1_000 });
    renderer.updateOptions({ centerHz: VIEWPORT_A + 1_400 });
    expect(reprojectDraws(draws)).toEqual([
      [expect.anything(), 1, 0, 99, 50, 0, 0, 99, 50],
    ]);
  });

  it('re-anchors after resize without a spurious shift', () => {
    const { renderer, draws } = trackedRenderer();
    renderer.updateOptions({ centerHz: VIEWPORT_B });
    expect(reprojectDraws(draws)).toHaveLength(1);
    renderer.resize(200, 100);
    renderer.updateOptions({ centerHz: VIEWPORT_B });
    expect(reprojectDraws(draws)).toHaveLength(1);
    renderer.updateOptions({ centerHz: VIEWPORT_B + 1_000 });
    expect(reprojectDraws(draws).at(-1)).toEqual([
      expect.anything(), 2, 0, 198, 100, 0, 0, 198, 100,
    ]);
  });

  it('keeps hit tests on the displayed viewport across the recenter transition', () => {
    const { renderer } = trackedRenderer();
    renderer.updateOptions({ centerHz: VIEWPORT_B, panoramaShiftHz: 1_000 });
    expect(renderer.pixelToFreq(50)).toBe(VIEWPORT_B);
    renderer.updateOptions({ panoramaShiftHz: 0 });
    expect(renderer.pixelToFreq(50)).toBe(VIEWPORT_B);
    expect(renderer.pixelToFreq(0)).toBe(VIEWPORT_B - 50_000);
    expect(renderer.pixelToFreq(100)).toBe(VIEWPORT_B + 50_000);
  });

  it('samples new rows at the exact sampling offset and leaves uncovered edges blank', () => {
    const { renderer, rows, draws } = trackedRenderer();
    renderer.updateOptions({ panoramaShiftHz: 10_000 });
    expect(reprojectDraws(draws)).toHaveLength(0);
    renderer.pushRow(peakRow(50));
    const row = rows.at(-1)!;
    expect(isRed(row, 40)).toBe(true);
    expect(isRed(row, 50)).toBe(false);
    expect(isBlank(row, 95)).toBe(true);
  });

  it('ignores viewport shifts on a zero-size canvas without throwing', () => {
    const r = makeRenderer(0, 0);
    expect(() => {
      r.updateOptions({ centerHz: VIEWPORT_B });
      r.pushRow(peakRow(50));
    }).not.toThrow();
  });
});

// ── resize ───────────────────────────────────────────────────────────────────

describe('resize', () => {
  it('accepts new dimensions and clears', () => {
    const r = makeRenderer();
    expect(() => r.resize(200, 100)).not.toThrow();
  });

  it('handles resize to zero gracefully', () => {
    const r = makeRenderer();
    expect(() => r.resize(0, 0)).not.toThrow();
  });
});

// ── destroy ──────────────────────────────────────────────────────────────────

describe('destroy', () => {
  it('marks renderer as destroyed', () => {
    const r = makeRenderer();
    r.destroy();
    // subsequent operations should be no-ops
    expect(() => r.pushRow(new Uint8Array(50))).not.toThrow();
    expect(() => r.resize(200, 100)).not.toThrow();
    expect(() => r.clear()).not.toThrow();
  });
});
