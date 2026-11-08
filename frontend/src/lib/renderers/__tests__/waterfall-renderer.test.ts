import { beforeEach, describe, it, expect, vi } from 'vitest';
import {
  COLOR_SCHEMES,
  WaterfallRenderer,
  defaultWaterfallOptions,
  type WaterfallOptions,
} from '../waterfall-renderer';

// MOR-4256: the renderer retains history on an offscreen canvas it creates
// itself via document.createElement; jsdom canvases have no 2d context, so
// every construction swaps in a mock the same way the main canvas is
// mocked. `historyFactory` resets per test; the tracked/pixel describes
// install their own.
let historyFactory: () => HTMLCanvasElement = () => looseCanvas();
const realCreateElement = document.createElement.bind(document);
function createElementImpl(tag: string, ...rest: unknown[]): HTMLElement {
  return tag === 'canvas'
    ? historyFactory() as unknown as HTMLElement
    : (realCreateElement as (...args: unknown[]) => HTMLElement)(tag, ...rest);
}
vi.spyOn(document, 'createElement').mockImplementation(
  createElementImpl as typeof document.createElement,
);

beforeEach(() => {
  historyFactory = () => looseCanvas();
});

function looseCanvas(): HTMLCanvasElement {
  const ctx = {
    canvas: null as unknown as HTMLCanvasElement,
    createImageData: (w: number, h: number) => ({
      width: w, height: h, data: new Uint8ClampedArray(w * h * 4),
    }),
    putImageData: vi.fn(), drawImage: vi.fn(), fillRect: vi.fn(),
    set fillStyle(_: string) {},
  } as unknown as CanvasRenderingContext2D;
  const canvas = { width: 0, height: 0, getContext: () => ctx } as unknown as HTMLCanvasElement;
  (ctx as { canvas: HTMLCanvasElement }).canvas = canvas;
  return canvas;
}

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
// The retained history plane (MOR-4256) tracks the SAMPLE-WINDOW center
// (centerHz - panoramaShiftHz): a display-only viewport move composites
// from it reversibly, a recentered frame (real retune) rebases it — content
// keeps its frequencies and fresh rows cover the full viewport again. Hit
// tests map through the displayed viewport center.
describe('panorama viewport reprojection (MOR-2464)', () => {
  function trackedCanvas(w = 100, h = 50) {
    const rows: Array<{ width: number; height: number; data: Uint8ClampedArray }> = [];
    const draws: unknown[][] = [];
    const fills: number[][] = [];
    const ctx = {
      canvas: null as unknown as HTMLCanvasElement,
      createImageData: (width: number) => ({ width, height: 1, data: new Uint8ClampedArray(width * 4) }),
      putImageData: (img: { width: number; height: number; data: Uint8ClampedArray }) => rows.push(img),
      drawImage: (...args: unknown[]) => draws.push(args),
      fillRect: (x: number, y: number, rw: number, rh: number) => fills.push([x, y, rw, rh]),
      set fillStyle(_: string) {},
    } as unknown as CanvasRenderingContext2D;
    const canvas = { width: w, height: h, getContext: () => ctx } as unknown as HTMLCanvasElement;
    (ctx as { canvas: HTMLCanvasElement }).canvas = canvas;
    return { canvas, rows, draws, fills };
  }

  // Rows land in the retained history plane now (MOR-4256); the factory
  // keeps every created history canvas (resize replaces it) and the
  // accessors read across them all, in order.
  function trackedRenderer(w = 100, h = 50, opts?: Partial<WaterfallOptions>) {
    const tracked = trackedCanvas(w, h);
    const historyCanvases: ReturnType<typeof trackedCanvas>[] = [];
    historyFactory = () => {
      const created = trackedCanvas(w, h);
      historyCanvases.push(created);
      return created.canvas;
    };
    const renderer = new WaterfallRenderer(tracked.canvas, {
      ...defaultWaterfallOptions, centerHz: 14_050_000, spanHz: 100_000, ...opts,
    });
    return {
      renderer,
      ...tracked,
      historyCanvas: () => historyCanvases.at(-1)!.canvas,
      historyRows: () => historyCanvases.flatMap((c) => c.rows),
      historyDraws: () => historyCanvases.flatMap((c) => c.draws),
      historyFills: () => historyCanvases.flatMap((c) => c.fills),
    };
  }

  // drawImage overloads: per-push scroll targets dy = 1; reprojects dy = 0.
  const reprojectDraws = (draws: unknown[][]) => draws.filter((d) => d[6] === 0);
  // The viewport-offset sequence the 9-arg projection draws apply, with
  // consecutive repeats collapsed: MOR-4256 re-projects from the retained
  // history after EVERY pushed row too, so what pins a viewport move is the
  // offset changing, not the draw count.
  const reprojectOffsets = (draws: unknown[][]): number[] => {
    const offsets = reprojectDraws(draws)
      .filter((d) => d.length === 9)
      .map((d) => ((d[5] as number) === 0 ? (d[1] as number) : -(d[5] as number)));
    return offsets.filter((offset, index) => index === 0 || offsets[index - 1] !== offset);
  };

  const peakRow = (...indices: number[]): Uint8Array => {
    const data = new Uint8Array(100).fill(0);
    for (const index of indices.length ? indices : [50]) data[index] = 80;
    return data;
  };
  const isRed = (row: { data: Uint8ClampedArray }, x: number): boolean =>
    row.data[x * 4] === 255 && row.data[x * 4 + 1] === 0 && row.data[x * 4 + 2] === 0;

  const VIEWPORT_A = 14_050_000;
  const VIEWPORT_B = 14_051_000;

  it('reprojects history by the viewport-center delta while the sample window holds', () => {
    const { renderer, draws, fills } = trackedRenderer();
    renderer.updateOptions({ centerHz: VIEWPORT_B, panoramaShiftHz: 1_000 });
    expect(reprojectDraws(draws)).toEqual([
      [expect.anything(), 1, 0, 99, 50, 0, 0, 99, 50],
    ]);
    // The strip the draw does not cover stays the background fill.
    expect(fills).toContainEqual([0, 0, 100, 50]);
    // A same-sample-window update with only a SUB-PIXEL offset change
    // projects nothing new.
    renderer.updateOptions({ centerHz: VIEWPORT_B + 400, panoramaShiftHz: 1_400 });
    expect(reprojectOffsets(draws)).toEqual([1]);
  });
  it('rebases retained history on a sample-window move and blanks the strip that left', () => {
    const { renderer, historyDraws, historyFills } = trackedRenderer();
    // A recentered frame 10 kHz lower (a real retune): the retained plane
    // shifts so frequencies keep their columns; the strip that left the
    // window blanks honestly — in the history plane, not destructively on
    // screen, so a later preview excursion still projects from what is left.
    renderer.updateOptions({ centerHz: VIEWPORT_A - 10_000 });
    expect(reprojectDraws(historyDraws())).toEqual([
      [expect.anything(), 0, 0, 90, 50, 10, 0, 90, 50],
    ]);
    expect(historyFills()).toContainEqual([0, 0, 10, 50]);
  });

  it('rebases on recentered samples and keeps fresh rows covering the full viewport', () => {
    const { renderer, historyRows, historyDraws, draws } = trackedRenderer();
    // Samples centered on A; the viewport glides A→B (1 px at this span).
    renderer.pushRow(peakRow(50));
    renderer.updateOptions({ centerHz: VIEWPORT_B, panoramaShiftHz: 1_000 });    renderer.pushRow(peakRow(50));
    // The viewport moved exactly once (A→B); the pushed row's own
    // re-projection at that same offset is not a move.
    expect(reprojectOffsets(draws)).toEqual([1]);
    expect(reprojectDraws(draws)[0]).toEqual([
      expect.anything(), 1, 0, 99, 50, 0, 0, 99, 50,
    ]);
    // Recentered samples (center B) at the unchanged viewport B: the
    // sample window moved, so the retained plane rebases by the same 1 px
    // and the viewport offset settles back — on screen nothing jumps.
    renderer.updateOptions({ centerHz: VIEWPORT_B, panoramaShiftHz: 0 });
    expect(reprojectDraws(historyDraws())).toEqual([
      [expect.anything(), 1, 0, 99, 50, 0, 0, 99, 50],
    ]);
    renderer.pushRow(peakRow(49, 50));
    // New rows rest at their indices in the rebased plane — full width,
    // nothing clipped at the anchor-plane edge.
    const row = historyRows().at(-1)!;
    expect(isRed(row, 49)).toBe(true);
    expect(isRed(row, 50)).toBe(true);
    expect(isRed(row, 51)).toBe(false);
  });

  it('rounds fractional sample-window moves without cumulative drift', () => {
    const { renderer, historyDraws } = trackedRenderer();
    renderer.updateOptions({ centerHz: VIEWPORT_A + 400 });
    expect(reprojectDraws(historyDraws())).toHaveLength(0);
    renderer.updateOptions({ centerHz: VIEWPORT_A + 1_000 });
    expect(reprojectDraws(historyDraws())).toEqual([
      [expect.anything(), 1, 0, 99, 50, 0, 0, 99, 50],
    ]);
    // 1.4 px of total travel rounds to the same 1 px — the carried
    // residue keeps repeated retunes drift-free.
    renderer.updateOptions({ centerHz: VIEWPORT_A + 1_400 });
    expect(reprojectDraws(historyDraws())).toHaveLength(1);
  });
  it('keeps the frequency anchor across resize without a spurious shift', () => {
    const { renderer, historyDraws } = trackedRenderer();
    renderer.updateOptions({ centerHz: VIEWPORT_B });
    expect(reprojectDraws(historyDraws())).toEqual([
      [expect.anything(), 1, 0, 99, 50, 0, 0, 99, 50],
    ]);
    renderer.resize(200, 100);    renderer.updateOptions({ centerHz: VIEWPORT_B });
    // No rebase and no reprojection: the scaled plane still answers the
    // same anchor, only the pixel offset is recomputed at the new width.
    expect(reprojectDraws(historyDraws())).toHaveLength(1);
    renderer.updateOptions({ centerHz: VIEWPORT_B + 1_000 });
    expect(reprojectDraws(historyDraws()).at(-1)).toEqual([
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

  it('rests new rows at their indices in the anchor plane under a preview shift', () => {
    const { renderer, historyRows, historyDraws, draws } = trackedRenderer();
    // A display-only viewport move keeps the sample window: NO rebase —
    // the projection carries the shift instead.
    renderer.updateOptions({ centerHz: VIEWPORT_A + 10_000, panoramaShiftHz: 10_000 });
    expect(reprojectOffsets(draws)).toEqual([10]);
    expect(reprojectDraws(historyDraws())).toHaveLength(0);
    renderer.pushRow(peakRow(50));
    const row = historyRows().at(-1)!;
    expect(isRed(row, 50)).toBe(true);
    expect(isRed(row, 49)).toBe(false);
    expect(isRed(row, 51)).toBe(false);
  });

  it('anchors the first real sample window without a huge reproject and keeps its first row', () => {
    const { renderer, historyRows, draws, fills, historyDraws } = trackedRenderer(100, 50, { centerHz: 0, spanHz: 0 });
    // First frame: real RF center lands while the constructor default was 0/0.
    const clearFills = fills.length;    renderer.updateOptions({ centerHz: 14_050_000, spanHz: 100_000, panoramaShiftHz: 0 });
    expect(reprojectDraws(draws)).toHaveLength(0);
    expect(fills.length).toBe(clearFills);
    renderer.pushRow(peakRow(50));
    expect(historyRows().length).toBeGreaterThan(0);
    expect(isRed(historyRows()[0], 50)).toBe(true);
    renderer.updateOptions({ centerHz: 14_051_000 });
    expect(reprojectDraws(historyDraws())).toEqual([
      [expect.anything(), 1, 0, 99, 50, 0, 0, 99, 50],
    ]);
    const tiny = makeRenderer(0, 0);
    expect(() => {
      tiny.updateOptions({ centerHz: VIEWPORT_B });
      tiny.pushRow(peakRow(50));
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

// ── retained history projection (MOR-4256) ───────────────────────────────────
//
// A reversible viewport excursion (drag preview out and back, a cancelled
// pan) must restore every history column: the visible canvas is a
// projection of the RETAINED copy, never a destructive in-place shift of
// the only one. These mocks move real pixels so the assertions read the
// painted result, without a single new scope row.
describe('retained history projection (MOR-4256)', () => {
  const BG = [0x00, 0x10, 0x20, 255];
  const RED = [255, 0, 0, 255];

  // A minimal pixel-true canvas: a real backing array, cleared on resize
  // like a real canvas, with drawImage snapshots (self-copies do not
  // smear) and nearest-neighbor scaling for the resize path.
  function pixelCanvas(w: number, h: number) {
    let pixels = new Uint8ClampedArray(w * h * 4);
    let canvasW = w;
    let canvasH = h;
    const canvas = {} as HTMLCanvasElement & { __pixels: Uint8ClampedArray };
    for (const prop of ['width', 'height'] as const) {
      Object.defineProperty(canvas, prop, {
        get: () => (prop === 'width' ? canvasW : canvasH),
        set: (value: number) => {
          if (prop === 'width') canvasW = value;
          else canvasH = value;
          // A real canvas clears its backing store when resized.
          pixels = new Uint8ClampedArray(canvasW * canvasH * 4);
        },
      });
    }
    Object.defineProperty(canvas, '__pixels', { get: () => pixels });
    const blit = (
      src: { width: number; height: number; __pixels: Uint8ClampedArray },
      sx: number, sy: number, sw: number, sh: number,
      dx: number, dy: number, dw: number, dh: number,
    ) => {
      // drawImage snapshots its source — copy it out first so a self-copy
      // (the per-row scroll) does not smear.
      const snapshot = src.__pixels.slice();
      for (let y = 0; y < dh; y++) {
        for (let x = 0; x < dw; x++) {
          const tx = dx + x;
          const ty = dy + y;
          if (tx < 0 || ty < 0 || tx >= canvas.width || ty >= canvas.height) continue;
          const sxp = sx + Math.floor((x / dw) * sw);
          const syp = sy + Math.floor((y / dh) * sh);
          if (sxp < 0 || syp < 0 || sxp >= src.width || syp >= src.height) continue;
          pixels.set(snapshot.subarray((syp * src.width + sxp) * 4, (syp * src.width + sxp) * 4 + 4),
            (ty * canvas.width + tx) * 4);
        }
      }
    };
    const ctx = {
      canvas,
      fillStyle: '#000000',
      createImageData: (cw: number, ch: number) => ({
        width: cw, height: ch, data: new Uint8ClampedArray(cw * ch * 4),
      }),
      putImageData(img: { width: number; height: number; data: Uint8ClampedArray }, dx: number, dy: number) {
        for (let y = 0; y < img.height; y++) {
          for (let x = 0; x < img.width; x++) {
            const tx = dx + x;
            const ty = dy + y;
            if (tx < 0 || ty < 0 || tx >= canvas.width || ty >= canvas.height) continue;
            pixels.set(img.data.subarray((y * img.width + x) * 4, (y * img.width + x) * 4 + 4),
              (ty * canvas.width + tx) * 4);
          }
        }
      },
      drawImage(...args: unknown[]) {
        const src = args[0] as unknown as { width: number; height: number; __pixels: Uint8ClampedArray };
        if (args.length === 3) {
          const [, dx, dy] = args as [unknown, number, number];
          blit(src, 0, 0, src.width, src.height, dx, dy, src.width, src.height);
        } else if (args.length === 5) {
          const [, dx, dy, dw, dh] = args as [unknown, number, number, number, number];
          blit(src, 0, 0, src.width, src.height, dx, dy, dw, dh);
        } else {
          const [, sx, sy, sw, sh, dx, dy, dw, dh] = args as
            [unknown, number, number, number, number, number, number, number, number];
          blit(src, sx, sy, sw, sh, dx, dy, dw, dh);
        }
      },
      fillRect(x: number, y: number, rw: number, rh: number) {
        const hex = ctx.fillStyle.replace('#', '');
        const rgb = [0, 2, 4].map((i) => parseInt(hex.slice(i, i + 2), 16));
        for (let yy = y; yy < y + rh; yy++) {
          for (let xx = x; xx < x + rw; xx++) {
            if (xx < 0 || yy < 0 || xx >= canvas.width || yy >= canvas.height) continue;
            pixels.set([rgb[0]!, rgb[1]!, rgb[2]!, 255], (yy * canvas.width + xx) * 4);
          }
        }
      },
    };
    (canvas as { getContext?: unknown }).getContext = () => ctx;
    return canvas;
  }

  function pixelRenderer(w = 100, h = 50) {
    const visible = pixelCanvas(w, h);
    historyFactory = () => pixelCanvas(w, h);
    const renderer = new WaterfallRenderer(visible, {
      ...defaultWaterfallOptions, centerHz: 14_050_000, spanHz: 100_000,
    });
    return { renderer, visible };
  }

  const peakRow = (...indices: number[]): Uint8Array => {
    const data = new Uint8Array(100).fill(0);
    for (const index of indices.length ? indices : [50]) data[index] = 80;
    return data;
  };

  it('restores every column after a reversible viewport excursion, even one wider than the canvas', () => {
    const { renderer, visible } = pixelRenderer();
    const pixels = visible.__pixels;
    const pxAt = (x: number, y: number) =>
      Array.from(pixels.subarray((y * 100 + x) * 4, (y * 100 + x) * 4 + 4));

    renderer.pushRow(peakRow(50));
    expect(pxAt(50, 0)).toEqual(RED);
    const rested = Array.from(pixels);

    // Excursion: viewport +10 kHz = 10 px at this span/width. The marker
    // follows the pointer; the exposed strip reads background.
    renderer.updateOptions({ centerHz: 14_060_000, panoramaShiftHz: 10_000 });
    expect(pxAt(40, 0)).toEqual(RED);
    expect(pxAt(95, 0)).toEqual(BG);

    // Back at the origin (drag returned, or cancelled): the retained copy
    // restores every column — the pre-MOR-4256 in-place shift left the
    // blanked strips behind.
    renderer.updateOptions({ centerHz: 14_050_000, panoramaShiftHz: 0 });
    expect(Array.from(pixels)).toEqual(rested);

    // An excursion wider than the canvas blanks everything — and still
    // restores, from the same retained copy and no new scope row.
    renderer.updateOptions({ centerHz: 14_350_000, panoramaShiftHz: 300_000 });
    expect(pxAt(50, 0)).toEqual(BG);
    expect(pxAt(0, 0)).toEqual(BG);
    renderer.updateOptions({ centerHz: 14_050_000, panoramaShiftHz: 0 });
    expect(Array.from(pixels)).toEqual(rested);
  });

  it('keeps newly pushed rows and the restored history aligned through an excursion', () => {
    const { renderer, visible } = pixelRenderer();
    const pixels = visible.__pixels;
    const pxAt = (x: number, y: number) =>
      Array.from(pixels.subarray((y * 100 + x) * 4, (y * 100 + x) * 4 + 4));

    renderer.pushRow(peakRow(50));
    // Viewport +10 px, then a new row arrives sampled for that viewport
    // (panoramaShiftHz keeps the sample window anchored at 14.050 MHz).
    renderer.updateOptions({ centerHz: 14_060_000, panoramaShiftHz: 10_000 });
    renderer.pushRow(peakRow(50));
    // On screen both rows line up at x=40 — the new row landed in the same
    // anchor-plane column as the resting marker.
    expect(pxAt(40, 0)).toEqual(RED);
    expect(pxAt(40, 1)).toEqual(RED);
    // Back at the origin both markers return to x=50, history intact.
    renderer.updateOptions({ centerHz: 14_050_000, panoramaShiftHz: 0 });
    expect(pxAt(50, 0)).toEqual(RED);
    expect(pxAt(50, 1)).toEqual(RED);
    expect(pxAt(95, 0)).toEqual(BG);
  });

  it('covers the full viewport with fresh rows after a real retune, including one of a whole span', () => {
    const { renderer, visible } = pixelRenderer();
    const pxAt = (x: number, y: number) =>
      Array.from(visible.__pixels.subarray((y * visible.width + x) * 4, (y * visible.width + x) * 4 + 4));
    const fullRow = () => new Uint8Array(100).fill(80);

    renderer.pushRow(peakRow(50));
    // Confirmed retune: recentered samples at the settled viewport. The
    // retained plane rebases — the old marker keeps its frequency (x=40)
    // and the strip that left the window blanks honestly.
    renderer.updateOptions({ centerHz: 14_060_000, panoramaShiftHz: 0 });
    expect(pxAt(40, 0)).toEqual(RED);
    expect(pxAt(95, 0)).toEqual(BG);
    // …but fresh recentered rows cover the FULL viewport again (the
    // round-1 design clipped them at the anchor plane's edge).
    renderer.pushRow(fullRow());
    expect(pxAt(0, 0)).toEqual(RED);
    expect(pxAt(99, 0)).toEqual(RED);
    // A retune of a whole span leaves no recoverable history, yet fresh
    // rows still paint — never a permanently blank waterfall.
    renderer.updateOptions({ centerHz: 14_160_000, panoramaShiftHz: 0 });
    renderer.pushRow(fullRow());
    expect(pxAt(0, 0)).toEqual(RED);
    expect(pxAt(99, 0)).toEqual(RED);
  });

  it('keeps the frequency anchor across a resize at a nonzero viewport offset', () => {
    const { renderer, visible } = pixelRenderer();
    const pxAt = (x: number, y: number) =>
      Array.from(visible.__pixels.subarray((y * visible.width + x) * 4, (y * visible.width + x) * 4 + 4));

    renderer.pushRow(peakRow(50));
    // Preview offset 10 px: the marker shows at x=40.
    renderer.updateOptions({ centerHz: 14_060_000, panoramaShiftHz: 10_000 });
    expect(pxAt(40, 0)).toEqual(RED);
    // Doubling the canvas scales the marker to anchor x=100 and the
    // viewport offset to 20 px: the marker must show at x=80, not drift
    // to x=100 through a re-anchor.
    renderer.resize(200, 100);
    expect(pxAt(80, 0)).toEqual(RED);
    // Returning to the original viewport shows it at the scaled anchor.
    renderer.updateOptions({ centerHz: 14_050_000, panoramaShiftHz: 0 });
    expect(pxAt(100, 0)).toEqual(RED);
  });
});

