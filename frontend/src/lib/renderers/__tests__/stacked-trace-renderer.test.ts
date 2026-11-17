import { afterEach, describe, expect, it, vi } from 'vitest';
import { defaultWaterfallOptions } from '../waterfall-renderer';
import { StackedTraceRenderer, StackedTraceRenderError } from '../stacked-trace-renderer';
import { defaultStackedTraceSettings, projectStackedDepth } from '../stacked-trace-geometry';
import { webglFixture, countTypedArrays } from './webgl-fixture';

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

const options = { ...defaultWaterfallOptions, centerHz: 14_050_000, spanHz: 100_000 };
// Four rows, and history rows that are the plain maxima: smoothing has its own tests.
const settings = { ...defaultStackedTraceSettings, rows: 4, rowPeriodMs: 100, rowBlend: 1, rowSoften: 0 };
/** Sixty rows of 100 ms: the six-second stack of the time-base tests. */
const sixSeconds = { ...defaultStackedTraceSettings, rows: 60, rowPeriodMs: 100 };
const frame = (length = 475, value = 40) => new Uint8Array(length).fill(value);

describe('MOR-4463 stacked-trace renderer', () => {
  it('replaces the live trace on every frame and commits one history row per row period', () => {
    const f = webglFixture(200, 100);
    const r = new StackedTraceRenderer(f.canvas, options, settings);
    f.calls.length = 0;
    // 30 ms cadence: frames at 0..270 ms.
    for (let i = 0; i < 10; i++) r.pushRow(frame(), options, i * 30);
    const slots = f.named('texSubImage2D').map(call => call.args[3]);
    // A committing frame writes the history row and then the live trace in
    // the next slot. Frame 0 commits slot 0 (live: 1). Commits follow a
    // 100 ms grid: the frame at 120 ms commits slot 1 (live: 2), the frame
    // at 210 ms commits slot 2 (live: 3); the frames between rewrite the
    // live slot.
    expect(slots).toEqual([0, 1, 1, 1, 1, 1, 2, 2, 2, 2, 3, 3, 3]);
    expect(r.historyRows).toBe(3);
    expect(f.getContext).toHaveBeenCalledWith('webgl2', expect.any(Object));
  });

  it('keeps at most the configured number of history rows and reuses the ring', () => {
    const f = webglFixture(200, 100);
    const r = new StackedTraceRenderer(f.canvas, options, settings);
    f.calls.length = 0;
    for (let i = 0; i < 12; i++) r.pushRow(frame(), options, i * 100);
    expect(r.historyRows).toBe(4);
    // Every frame commits here: history row, then the live trace one slot on.
    expect(f.named('texSubImage2D').map(call => call.args[3]))
      .toEqual(Array.from({ length: 12 }, (_, i) => [i % 5, (i + 1) % 5]).flat());
  });

  it('draws the background, then the history back to front, then the live trace', () => {
    const f = webglFixture(200, 100);
    const r = new StackedTraceRenderer(f.canvas, options, settings);
    for (let i = 0; i < 3; i++) r.pushRow(frame(), options, i * 100);
    r.pushRow(frame(), options, 230);
    f.calls.length = 0;
    r.draw(230);
    // One background quad plus three history rows plus the live row.
    expect(f.named('drawArrays')).toHaveLength(5);
    expect(f.named('drawArrays')[0].args.slice(1)).toEqual([0, 4]);
    // Oldest slot first; the live slot (3) is painted last, in front.
    expect(f.named('uniform1i').filter(call => call.args[0] === 'uRow').map(call => call.args[1]))
      .toEqual([0, 1, 2, 3]);
    const fog = f.named('uniform2f').filter(call => call.args[0] === 'uShade').map(call => call.args[1] as number);
    expect(fog[fog.length - 1]).toBe(0);
    for (let i = 1; i < fog.length; i++) expect(fog[i]).toBeLessThanOrEqual(fog[i - 1]);
  });

  it('glides the history while a committed row moves back and rests afterwards', () => {
    const f = webglFixture(200, 100);
    const r = new StackedTraceRenderer(f.canvas, options, settings);
    r.pushRow(frame(), options, 0);
    r.pushRow(frame(), options, 100);
    const base = (now: number) => {
      f.calls.length = 0;
      const animating = r.draw(now);
      const placed = f.named('uniform4f').filter(call => call.args[0] === 'uPlace').map(call => call.args[1] as number);
      return { animating, oldest: placed[0] };
    };
    const start = base(100), middle = base(150), rested = base(200), later = base(900);
    expect(start.animating).toBe(true);
    expect(middle.animating).toBe(true);
    expect(middle.oldest).toBeGreaterThan(start.oldest);
    expect(rested.oldest).toBeGreaterThan(middle.oldest);
    expect(rested.animating).toBe(false);
    expect(later).toEqual(rested);
  });

  it('paces the glide by the measured commit interval of a slow source', () => {
    const f = webglFixture(200, 100);
    const r = new StackedTraceRenderer(f.canvas, options, settings);
    r.pushRow(frame(), options, 0);
    r.pushRow(frame(), options, 400);
    expect(r.draw(600)).toBe(true);
    expect(r.draw(800)).toBe(false);
  });

  it('re-projects stored rows through uniforms when the view retunes, without touching the ring', () => {
    const f = webglFixture(200, 100);
    const r = new StackedTraceRenderer(f.canvas, options, settings);
    r.pushRow(frame(), options, 0);
    f.calls.length = 0;
    expect(r.updateOptions({ centerHz: 14_060_000 })).toBe(true);
    r.draw(500);
    expect(f.named('texSubImage2D')).toHaveLength(0);
    expect(f.named('texImage2D')).toHaveLength(0);
    const map = f.named('uniform2f').filter(call => call.args[0] === 'uMap');
    // The row was captured at 14.000–14.100 MHz; the view now starts at 14.010.
    for (const call of map) expect(call.args.slice(1)).toEqual([1, 0.1]);
    const cover = f.named('uniform2f').filter(call => call.args[0] === 'uCover');
    for (const call of cover) expect(call.args.slice(1)).toEqual([-0.1, 0.9]);
    expect(r.updateOptions({ centerHz: 14_060_000 })).toBe(false);
    expect(r.updateOptions({ colorScheme: 'thermal' })).toBe(true);
  });

  it('takes any sample count per row: the ring widens and each row keeps its own count', () => {
    const f = webglFixture(200, 100);
    const r = new StackedTraceRenderer(f.canvas, options, settings);
    r.pushRow(frame(475), options, 0);
    r.pushRow(frame(2048), options, 100);
    r.pushRow(frame(101), { ...options, sampleOffset: 1, sampleAmplitudeMax: 160 }, 200);
    // Ring width = samples plus their pairwise-maximum levels.
    const allocations = f.named('texImage2D').map(call => call.args[3]);
    expect(allocations).toEqual([1, 952, 4095]);
    expect(f.named('copyTexSubImage2D').map(call => call.args[6])).toEqual([1, 952]);
    f.calls.length = 0;
    r.draw(200);
    expect(f.named('uniform2f').filter(call => call.args[0] === 'uSamples').map(call => call.args.slice(1)))
      .toEqual([[475, 80], [2048, 80], [100, 160], [100, 160]]);
    // Bins per column drive the peak taps: 2048 bins over 100 columns of
    // the view, and a row drawn at scale s shows 1 / s of the view.
    const placed = f.named('uniform4f').filter(call => call.args[0] === 'uPlace').map(call => call.args.slice(1) as number[]);
    expect(placed[1][1] * placed[1][3]).toBeCloseTo(2048 / 99, 6);
    expect(placed[1][3]).toBeLessThan(1);
  });

  it('ignores a frame without samples and paints only the background before the first row', () => {
    const f = webglFixture(200, 100);
    const r = new StackedTraceRenderer(f.canvas, options, settings);
    r.pushRow(new Uint8Array(0), options, 0);
    f.calls.length = 0;
    expect(r.draw(0)).toBe(false);
    expect(f.named('drawArrays')).toHaveLength(1);
    expect(r.historyRows).toBe(0);
  });

  it('allocates no typed arrays while frames flow and the stack is painted', () => {
    const f = webglFixture(200, 100);
    const r = new StackedTraceRenderer(f.canvas, options, settings);
    const pixels = frame();
    r.pushRow(pixels, options, 0); r.draw(0);
    const allocated = countTypedArrays();
    for (let i = 1; i <= 500; i++) { r.pushRow(pixels, options, i * 33); r.draw(i * 33); }
    expect(allocated()).toBe(0);
  });

  it('reports a missing WebGL2 context as a typed error and stops after destroy', () => {
    const canvas = document.createElement('canvas');
    Object.defineProperty(canvas, 'getContext', { value: () => null });
    expect(() => new StackedTraceRenderer(canvas, options)).toThrow(StackedTraceRenderError);
    const f = webglFixture(200, 100);
    const r = new StackedTraceRenderer(f.canvas, options, settings);
    r.pushRow(frame(), options, 0);
    r.destroy();
    f.calls.length = 0;
    r.pushRow(frame(), options, 100);
    expect(r.draw(100)).toBe(false);
    expect(f.calls).toHaveLength(0);
  });
});

/** History rows painted by one draw, oldest first: slot and the depth its baseline stands for. */
function paintedHistory(f: ReturnType<typeof webglFixture>, r: StackedTraceRenderer, now: number, height: number) {
  f.calls.length = 0;
  r.draw(now);
  const bases = f.named('uniform4f').filter(call => call.args[0] === 'uPlace').map(call => call.args[1] as number);
  bases.pop(); // the live front row
  const baseAt = (depth: number) => {
    const placed = { baseline: 0, scale: 0, fog: 0 };
    projectStackedDepth(depth, sixSeconds, placed);
    return (sixSeconds.frontInsetPx + placed.baseline * height) / height * 2 - 1;
  };
  return { rows: bases.length, oldest: bases[0], baseAt };
}

describe('MOR-4463 stacked-trace time base', () => {
  it('holds six seconds of history at a 90 ms cadence that does not divide the row period', () => {
    const f = webglFixture(200, 100);
    const r = new StackedTraceRenderer(f.canvas, options, sixSeconds);
    for (let t = 0; t <= 6_480; t += 90) r.pushRow(frame(), options, t);
    const painted = paintedHistory(f, r, 6_480, 100);
    expect(painted.rows).toBeGreaterThanOrEqual(58);
    expect(painted.rows).toBeLessThanOrEqual(60);
    expect(painted.oldest).toBeGreaterThan(painted.baseAt(0.9));
    expect(painted.oldest).toBeLessThanOrEqual(painted.baseAt(1));
  });

  it('holds six seconds of history from a slow 400 ms source: fewer rows, same depth', () => {
    const f = webglFixture(200, 100);
    const r = new StackedTraceRenderer(f.canvas, options, sixSeconds);
    for (let t = 0; t <= 12_000; t += 400) r.pushRow(frame(), options, t);
    const painted = paintedHistory(f, r, 12_000, 100);
    // Rows committed at 6 400 … 12 000 ms are younger than six seconds.
    expect(painted.rows).toBe(15);
    expect(painted.oldest).toBeGreaterThan(painted.baseAt(0.9));
    expect(painted.oldest).toBeLessThanOrEqual(painted.baseAt(1));
  });

  it('holds six seconds of history under jitter', () => {
    const f = webglFixture(200, 100);
    const r = new StackedTraceRenderer(f.canvas, options, sixSeconds);
    let t = 0;
    for (let i = 0; t <= 12_000; i++) { r.pushRow(frame(), options, t); t += i % 2 === 0 ? 60 : 140; }
    const painted = paintedHistory(f, r, 12_000, 100);
    // 60/140 ms alternation reaches the 100 ms grid on every second frame.
    expect(painted.rows).toBeGreaterThanOrEqual(29);
    expect(painted.rows).toBeLessThanOrEqual(30);
    expect(painted.oldest).toBeGreaterThan(painted.baseAt(0.9));
  });

  it('does not age the picture while the source is silent', () => {
    const f = webglFixture(200, 100);
    const r = new StackedTraceRenderer(f.canvas, options, sixSeconds);
    for (let t = 0; t <= 3_000; t += 100) r.pushRow(frame(), options, t);
    const before = paintedHistory(f, r, 3_100, 100);
    const after = paintedHistory(f, r, 60_000, 100);
    expect(after.rows).toBe(before.rows);
    expect(after.oldest).toBe(before.oldest);
    // A silence counts as at most one second of stream time when frames return.
    r.pushRow(frame(), options, 60_000);
    expect(paintedHistory(f, r, 60_000, 100).rows).toBeGreaterThanOrEqual(before.rows);
  });
});

describe('MOR-4463 stacked-trace peak levels', () => {
  it('uploads each row with its pairwise-maximum levels', () => {
    const f = webglFixture(200, 100);
    const r = new StackedTraceRenderer(f.canvas, options, settings);
    f.calls.length = 0;
    const uploaded: number[][] = [];
    (f.gl as unknown as Record<string, unknown>).texSubImage2D = (...args: unknown[]) => {
      uploaded.push(Array.from((args[8] as Uint8Array).subarray(0, args[4] as number)));
    };
    r.pushRow(Uint8Array.of(1, 9, 3, 4, 7), options, 0);
    expect(uploaded[0]).toEqual([1, 9, 3, 4, 7, 9, 4, 7, 9, 7, 9]);
  });

  it('skips the sample offset when it builds the levels', () => {
    const f = webglFixture(200, 100);
    const r = new StackedTraceRenderer(f.canvas, options, settings);
    f.calls.length = 0;
    const uploaded: number[][] = [];
    (f.gl as unknown as Record<string, unknown>).texSubImage2D = (...args: unknown[]) => {
      uploaded.push(Array.from((args[8] as Uint8Array).subarray(0, args[4] as number)));
    };
    r.pushRow(Uint8Array.of(77, 77, 2, 5, 3), { ...options, sampleOffset: 2 }, 0);
    // The history row and the live trace both start at the offset.
    expect(uploaded).toEqual([[2, 5, 3, 5, 3, 5], [2, 5, 3, 5, 3, 5]]);
  });

  it('reduces a frame denser than the device texture width by maxima instead of failing', () => {
    const f = webglFixture(200, 100); // MAX_TEXTURE_SIZE 16384
    const r = new StackedTraceRenderer(f.canvas, options, settings);
    const dense = new Uint8Array(20_000).fill(10); dense[12_345] = 80;
    f.calls.length = 0;
    expect(() => r.pushRow(dense, options, 0)).not.toThrow();
    const upload = f.named('texSubImage2D')[0];
    expect(upload.args[4]).toBeLessThanOrEqual(16_384);
    expect((upload.args[8] as Uint8Array)[12_345 >> 2]).toBe(80);
    f.calls.length = 0;
    r.draw(0);
    expect(f.named('uniform2f').find(call => call.args[0] === 'uSamples')!.args.slice(1)).toEqual([5_000, 80]);
  });
});

describe('MOR-4463 stacked-trace peaks in time', () => {
  /** Captures each upload as [slot, level-0 samples]. */
  function uploads(f: ReturnType<typeof webglFixture>, samples: number) {
    const seen: { slot: number; data: number[] }[] = [];
    (f.gl as unknown as Record<string, unknown>).texSubImage2D = (...args: unknown[]) => {
      seen.push({ slot: args[3] as number, data: Array.from((args[8] as Uint8Array).subarray(0, samples)) });
    };
    return seen;
  }

  it('commits a history row holding the peak of the frames since the previous row', () => {
    const f = webglFixture(200, 100);
    const r = new StackedTraceRenderer(f.canvas, options, settings);
    const quiet = Uint8Array.of(10, 10, 10, 10), click = Uint8Array.of(10, 80, 10, 10), tail = Uint8Array.of(10, 10, 10, 30);
    r.pushRow(quiet, options, 0);
    const seen = uploads(f, 4);
    r.pushRow(click, options, 30);   // a click one frame long
    r.pushRow(quiet, options, 60);
    r.pushRow(tail, options, 120);   // the frame that reaches the 100 ms grid
    // Live trace follows every frame; the committed row (slot 1) keeps the click.
    expect(seen.map(upload => upload.slot)).toEqual([1, 1, 1, 2]);
    expect(seen[2]).toEqual({ slot: 1, data: [10, 80, 10, 30] });
    expect(seen[3]).toEqual({ slot: 2, data: [10, 10, 10, 30] });
    // The next row starts its own peak: the click does not leak forward.
    r.pushRow(quiet, options, 220);
    expect(seen[4]).toEqual({ slot: 2, data: [10, 10, 10, 10] });
  });

  it('never mixes frames of different placement or size into one row', () => {
    const f = webglFixture(200, 100);
    const r = new StackedTraceRenderer(f.canvas, options, settings);
    r.pushRow(Uint8Array.of(10, 10, 10, 10), options, 0);
    const seen = uploads(f, 4);
    r.pushRow(Uint8Array.of(80, 10, 10, 10), options, 30);
    r.pushRow(Uint8Array.of(10, 10, 10, 20), { ...options, centerHz: 14_060_000 }, 60);   // retuned source
    r.pushRow(Uint8Array.of(10, 30, 10, 10), { ...options, centerHz: 14_060_000 }, 120);
    expect(seen[2]).toEqual({ slot: 1, data: [10, 30, 10, 20] });
    f.calls.length = 0;
    r.draw(120);
    // The committed row carries the placement of the frames it was built from.
    const maps = f.named('uniform2f').filter(call => call.args[0] === 'uMap').map(call => call.args.slice(1));
    expect(maps[1]).toEqual([1, -0.1]);
  });

  // Each property that places or scales a frame separates rows on its own:
  // a loud frame taken under the old value must not reach the committed row.
  const covered = { ...options, coverage: { startHz: 14_000_000, endHz: 14_100_000 } };
  it.each([
    ['sample count', options, options, 4, 5],
    ['source centre', covered, { ...covered, centerHz: 14_060_000 }, 4, 4],
    // Half the span around a centre that keeps the first sample on its frequency.
    ['source span', covered, { ...covered, sampleSpanHz: 50_000, centerHz: 14_025_000 }, 4, 4],
    ['amplitude range', options, { ...options, sampleAmplitudeMax: 160 }, 4, 4],
    ['coverage', covered, { ...covered, coverage: { startHz: 14_010_000, endHz: 14_090_000 } }, 4, 4],
  ] as const)('starts the row peak anew when the %s changes', (_name, first, changed, length, firstLength) => {
    const f = webglFixture(200, 100);
    const r = new StackedTraceRenderer(f.canvas, options, settings);
    r.pushRow(new Uint8Array(firstLength).fill(10), first, 0);
    const seen = uploads(f, length);
    r.pushRow(new Uint8Array(firstLength).fill(80), first, 30);
    r.pushRow(new Uint8Array(length).fill(10), changed, 60);
    r.pushRow(new Uint8Array(length).fill(20), changed, 120);
    expect(seen[2]).toEqual({ slot: 1, data: new Array(length).fill(20) });
  });
});

describe('MOR-4463 stacked-trace resources', () => {
  it('fits a row into a small device texture by reducing it, peaks kept', () => {
    const f = webglFixture(200, 100);
    const gl = f.gl as unknown as Record<string, (...args: never[]) => unknown>;
    const base = gl.getParameter;
    gl.getParameter = ((name: unknown) => name === (f.gl as unknown as Record<string, unknown>).MAX_TEXTURE_SIZE ? 1024 : base(name as never)) as never;
    const r = new StackedTraceRenderer(f.canvas, options);
    const data = new Uint8Array(8192).fill(10); data[5000] = 80;
    f.calls.length = 0;
    expect(() => r.pushRow(data, options, 0)).not.toThrow();
    for (const upload of f.named('texSubImage2D')) expect(upload.args[4]).toBeLessThanOrEqual(1024);
    for (const allocation of f.named('texImage2D')) expect(allocation.args[3]).toBeLessThanOrEqual(1024);
    // 8192 points become 512 (1023 texels with the levels); bin 5000 lands in entry 312.
    // The first upload is the committed history row, smoothed across frequency
    // by the default setting (10 + (80 - 10) / 2); the second is the live row, raw.
    const uploads = f.named('texSubImage2D');
    expect(uploads).toHaveLength(2);
    expect((uploads[0].args[8] as Uint8Array)[5000 >> 4]).toBe(45);
    expect((uploads[1].args[8] as Uint8Array)[5000 >> 4]).toBe(80);
    f.calls.length = 0;
    r.draw(0);
    expect(f.named('uniform2f').find(call => call.args[0] === 'uSamples')!.args.slice(1)).toEqual([512, 80]);
  });

  it('deletes the program and shaders when the vertex shader does not compile', () => {
    const f = webglFixture(200, 100);
    const gl = f.gl as unknown as Record<string, unknown>;
    const programs: object[] = [];
    gl.createProgram = () => { const program = {}; programs.push(program); return program; };
    gl.getShaderParameter = () => false;
    expect(() => new StackedTraceRenderer(f.canvas, options)).toThrow(StackedTraceRenderError);
    expect(programs).toHaveLength(1);
    expect(f.named('deleteProgram').map(call => call.args[0])).toContain(programs[0]);
    expect(f.named('deleteShader').length).toBeGreaterThanOrEqual(1);
  });

  it('sizes the front inset by the pixel scale it is given', () => {
    const f = webglFixture(200, 100);
    const r = new StackedTraceRenderer(f.canvas, options);
    const floor = () => {
      f.calls.length = 0; r.draw(0);
      return f.named('uniform3f').find(call => call.args[0] === 'uFloor')!.args[1];
    };
    expect(floor()).toBe(16);
    r.setPixelScale(2);
    expect(floor()).toBe(32);
  });
});

describe('MOR-4463 stacked-trace perspective', () => {
  it('draws every row across the pane at its own scale: the view narrows, the row shows more of the band', () => {
    const f = webglFixture(400, 200);
    const r = new StackedTraceRenderer(f.canvas, options);
    const s = defaultStackedTraceSettings;
    for (let t = 0; t <= 9_600; t += 100) r.pushRow(frame(), options, t);
    f.calls.length = 0;
    r.draw(60_000);
    // Oldest first, the live front row last. At rest the stream clock stands
    // one frame interval past the last frame: the row committed at t is
    // (9 700 - t) / 9 600 deep.
    const placed = f.named('uniform4f').filter(call => call.args[0] === 'uPlace').map(call => call.args.slice(1) as number[]);
    expect(placed.length).toBe(s.rows);
    const front = placed[placed.length - 1], expected = { baseline: 0, scale: 0, fog: 0 };
    expect(front[3]).toBe(1);
    expect(front[2]).toBeCloseTo(s.traceHeight * 200, 9);
    expect(front[1]).toBeCloseTo(475 / 199, 9);
    placed.slice(0, -1).forEach((row, index) => {
      projectStackedDepth((9_700 - (index + 2) * 100) / 9_600, s, expected);
      expect(row[3]).toBeCloseTo(expected.scale, 9);
      // Height shrinks with the width; a column spans 1 / scale as many bins.
      expect(row[2]).toBeCloseTo(s.traceHeight * 200 * expected.scale, 6);
      expect(row[1]).toBeCloseTo(475 / 199 / expected.scale, 6);
      expect(row[0]).toBeCloseTo((s.frontInsetPx + expected.baseline * 200) / 200 * 2 - 1, 9);
    });
    // The oldest row on screen is close to the 60 % of the last depth.
    expect(placed[0][3]).toBeGreaterThan(0.6);
    expect(placed[0][3]).toBeLessThan(0.62);
  });

  it('gives the floor the same perspective: front baseline, rise to the vanishing point, strength', () => {
    const f = webglFixture(400, 200);
    const r = new StackedTraceRenderer(f.canvas, options);
    const s = defaultStackedTraceSettings;
    r.draw(0);
    const floor = f.named('uniform3f').find(call => call.args[0] === 'uFloor')!.args.slice(1) as number[];
    expect(floor[0]).toBe(s.frontInsetPx);
    expect(floor[1]).toBeCloseTo(s.floorRise * (1 + s.recede) * 200, 9);
    expect(floor[2]).toBe(s.recede);
  });

  it('dissolves the far rows only lightly', () => {
    const f = webglFixture(400, 200);
    const r = new StackedTraceRenderer(f.canvas, options);
    for (let t = 0; t <= 9_600; t += 100) r.pushRow(frame(), options, t);
    f.calls.length = 0;
    r.draw(60_000);
    const fog = f.named('uniform2f').filter(call => call.args[0] === 'uShade').map(call => call.args[1] as number);
    expect(Math.max(...fog)).toBe(fog[0]);
    expect(fog[0]).toBeGreaterThan(0.9 * defaultStackedTraceSettings.haze);
    expect(fog[0]).toBeLessThanOrEqual(defaultStackedTraceSettings.haze);
    expect(defaultStackedTraceSettings.haze).toBeLessThanOrEqual(0.2);
  });
});

describe('MOR-4463 stacked-trace rows blended in time', () => {
  function uploads(f: ReturnType<typeof webglFixture>, samples: number) {
    const seen: { slot: number; data: number[] }[] = [];
    (f.gl as unknown as Record<string, unknown>).texSubImage2D = (...args: unknown[]) => {
      seen.push({ slot: args[3] as number, data: Array.from((args[8] as Uint8Array).subarray(0, samples)) });
    };
    return seen;
  }
  const blended = { ...defaultStackedTraceSettings, rows: 4, rowPeriodMs: 100, rowSoften: 0 };
  /** The history rows committed by frames 100 ms apart, in order. */
  function history(levels: number[], frameOptions = (_index: number) => options, rowSettings = blended) {
    const f = webglFixture(200, 100);
    const r = new StackedTraceRenderer(f.canvas, options, rowSettings);
    const seen = uploads(f, 2);
    levels.forEach((level, index) => r.pushRow(Uint8Array.of(level, 10), frameOptions(index), index * 100));
    // Every frame commits: the history row, then the live trace one slot on.
    return seen.filter((_, index) => index % 2 === 0).map(upload => upload.data);
  }

  it('blends a history row with the row before it, by the configured share', () => {
    expect(defaultStackedTraceSettings.rowBlend).toBe(0.6);
    // 0.6 * 20 + 0.4 * 80 = 44, then 0.6 * 20 + 0.4 * 44 = 29.6.
    expect(history([80, 20, 20])).toEqual([[80, 10], [44, 10], [30, 10]]);
  });

  it('keeps a signal one row long at the larger part of its height, and lets it fade', () => {
    // 0.6 * 80 + 0.4 * 20 = 56: the dot stands; 0.6 * 20 + 0.4 * 56 = 34.4 after it.
    expect(history([20, 80, 20])).toEqual([[20, 10], [56, 10], [34, 10]]);
  });

  it('leaves the live front trace unblended', () => {
    const f = webglFixture(200, 100);
    const r = new StackedTraceRenderer(f.canvas, options, blended);
    const seen = uploads(f, 2);
    r.pushRow(Uint8Array.of(80, 10), options, 0);
    r.pushRow(Uint8Array.of(20, 10), options, 100);
    expect(seen.map(upload => upload.data)).toEqual([[80, 10], [80, 10], [44, 10], [20, 10]]);
  });

  const covered = { ...options, coverage: { startHz: 14_000_000, endHz: 14_100_000 } };
  it.each([
    ['source centre', covered, { ...covered, centerHz: 14_060_000 }],
    ['source span', covered, { ...covered, sampleSpanHz: 50_000, centerHz: 14_025_000 }],
    ['amplitude range', covered, { ...covered, sampleAmplitudeMax: 160 }],
    ['coverage', covered, { ...covered, coverage: { startHz: 14_010_000, endHz: 14_090_000 } }],
  ] as const)('does not blend rows across a change of the %s', (_name, first, changed) => {
    expect(history([80, 20, 20], index => index === 0 ? first : changed)).toEqual([[80, 10], [20, 10], [20, 10]]);
  });

  it('does not blend rows of different sample counts', () => {
    const f = webglFixture(200, 100);
    const r = new StackedTraceRenderer(f.canvas, options, blended);
    const seen = uploads(f, 2);
    r.pushRow(Uint8Array.of(80, 80, 80), options, 0);
    r.pushRow(Uint8Array.of(20, 20), options, 100);
    expect(seen[2].data).toEqual([20, 20]);
  });

  it('smooths a history row across frequency before it is blended: a quarter, a half, a quarter', () => {
    const f = webglFixture(200, 100);
    const r = new StackedTraceRenderer(f.canvas, options, { ...blended, rowSoften: 1 });
    const seen = uploads(f, 5);
    r.pushRow(Uint8Array.of(0, 0, 80, 0, 40), options, 0);
    // The edge samples count their missing neighbour as themselves; the live trace stays as delivered.
    expect(seen.map(upload => upload.data)).toEqual([[0, 20, 40, 30, 30], [0, 0, 80, 0, 40]]);
    r.pushRow(Uint8Array.of(0, 0, 0, 0, 0), options, 100);
    // 40 % of the smoothed row before it.
    expect(seen[2].data).toEqual([0, 8, 16, 12, 12]);
  });

  it('smooths across frequency by the configured amount', () => {
    const f = webglFixture(200, 100);
    const r = new StackedTraceRenderer(f.canvas, options, { ...blended, rowSoften: 0.5 });
    const seen = uploads(f, 3);
    r.pushRow(Uint8Array.of(0, 80, 0), options, 0);
    expect(seen[0].data).toEqual([10, 60, 10]);
  });

  it('commits the plain maxima when blending is switched off', () => {
    expect(history([80, 20, 20], () => options, { ...blended, rowBlend: 1 })).toEqual([[80, 10], [20, 10], [20, 10]]);
  });
});

describe('MOR-4463 stacked-trace settings changed while it runs', () => {
  it('applies new settings to the rows it holds and keeps its ring', () => {
    const f = webglFixture(400, 200);
    const r = new StackedTraceRenderer(f.canvas, options, settings);
    for (let t = 0; t <= 300; t += 100) r.pushRow(frame(), options, t);
    const drawn = () => {
      f.calls.length = 0; r.draw(60_000);
      return {
        floor: f.named('uniform3f').find(call => call.args[0] === 'uFloor')!.args.slice(1) as number[],
        place: f.named('uniform4f').filter(call => call.args[0] === 'uPlace').map(call => call.args.slice(1) as number[]),
        fog: f.named('uniform2f').filter(call => call.args[0] === 'uShade').map(call => call.args[1] as number),
      };
    };
    const before = drawn();
    r.setSettings({ ...settings, recede: 1.5, floorRise: 0.3, traceHeight: 0.7, haze: 0.8, rows: 99, floorDrop: 3, colorGain: 50 });
    const after = drawn();
    expect(after.floor).toEqual([16, 0.3 * 2.5 * 200, 1.5]);
    expect(after.floor).not.toEqual(before.floor);
    // The front row takes the new ridge height; a far row the new narrowing and haze.
    expect(after.place[after.place.length - 1][2]).toBeCloseTo(0.7 * 200, 9);
    expect(after.place[0][3]).toBeLessThan(before.place[0][3]);
    expect(after.fog[0]).toBeGreaterThan(before.fog[0]);
    // The level uniform: the floor of the frames (40) less the new drop, a linear colour gamma, the colour window.
    expect(f.named('uniform3f').find(call => call.args[0] === 'uLevel')!.args.slice(1)).toEqual([37, 1, 60]);
    // Same ring: the rows held are still drawn, none was re-uploaded, and the row count stays the built one.
    expect(after.place).toHaveLength(before.place.length);
    expect(f.named('texSubImage2D')).toHaveLength(0);
    expect(f.named('texImage2D')).toHaveLength(0);
    for (let t = 400; t <= 2_000; t += 100) r.pushRow(frame(), options, t);
    expect(r.historyRows).toBe(4);
  });

  it('paces new history rows by a new row step', () => {
    const f = webglFixture(400, 200);
    const many = { ...settings, rows: 20 };
    const r = new StackedTraceRenderer(f.canvas, options, many);
    r.pushRow(frame(), options, 0);
    r.setSettings({ ...many, rowPeriodMs: 200 });
    for (let t = 100; t <= 800; t += 100) r.pushRow(frame(), options, t);
    // Rows at 0 and 100 ms under the old grid, then one every 200 ms: 300,
    // 500, 700. The old step would have committed nine.
    expect(r.historyRows).toBe(5);
  });
});

describe('MOR-4463 stacked-trace height from the noise floor', () => {
  const level = (f: ReturnType<typeof webglFixture>, r: StackedTraceRenderer, now: number) => {
    f.calls.length = 0; r.draw(now);
    return f.named('uniform3f').find(call => call.args[0] === 'uLevel')!.args.slice(1) as number[];
  };
  const noisy = (floor: number, length = 475) => { const data = frame(length, floor); data.fill(80, 100, 120); return data; };

  it('stands the surface a little below the noise floor of the frames, with the colour gamma and window', () => {
    const f = webglFixture(200, 100);
    const r = new StackedTraceRenderer(f.canvas, options, settings);
    r.pushRow(noisy(12), options, 0);
    const [floor, gamma, window] = level(f, r, 0);
    expect(floor).toBe(12 - defaultStackedTraceSettings.floorDrop);
    // Gain 70: gamma 4 ^ -0.4, colours reach further down than a linear ramp.
    expect(gamma).toBeCloseTo(4 ** -0.4, 12);
    expect(gamma).toBeLessThan(1);
    expect(window).toBe(60);
  });

  it('follows a drifting floor slowly and takes another band at once', () => {
    const f = webglFixture(200, 100);
    const r = new StackedTraceRenderer(f.canvas, options, settings);
    r.pushRow(noisy(12), options, 0);
    r.pushRow(noisy(22), options, 100);
    expect(level(f, r, 100)[0]).toBeCloseTo(12.4 - defaultStackedTraceSettings.floorDrop, 9);
    r.pushRow(noisy(60), options, 200);
    expect(level(f, r, 200)[0]).toBe(60 - defaultStackedTraceSettings.floorDrop);
  });

  it('measures the floor on the 0..80 scale whatever the amplitude range of the source, past the sample offset', () => {
    const f = webglFixture(200, 100);
    const r = new StackedTraceRenderer(f.canvas, options, settings);
    const data = new Uint8Array(40).fill(200);
    data.fill(30, 8);
    r.pushRow(data, { ...options, sampleAmplitudeMax: 160, sampleOffset: 8 }, 0);
    expect(level(f, r, 0)[0]).toBe(15 - defaultStackedTraceSettings.floorDrop);
  });

  it('does not answer to the reference level: the floor sets the height', () => {
    const f = webglFixture(200, 100);
    const r = new StackedTraceRenderer(f.canvas, options, settings);
    r.pushRow(noisy(12), options, 0);
    const before = level(f, r, 0);
    expect(r.updateOptions({ refLevel: 10 })).toBe(false);
    expect(level(f, r, 0)).toEqual(before);
  });
});
