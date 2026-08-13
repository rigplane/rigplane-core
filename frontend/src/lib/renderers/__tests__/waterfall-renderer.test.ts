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
