import { afterEach, describe, expect, it, vi } from 'vitest';
import { defaultWaterfallOptions } from '../waterfall-renderer';
import { StackedTraceRenderer, StackedTraceRenderError } from '../stacked-trace-renderer';
import { defaultStackedTraceSettings, projectStackedDepth, unprojectStackedFloor } from '../stacked-trace-geometry';
import { webglFixture, countTypedArrays } from './webgl-fixture';

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

const options = { ...defaultWaterfallOptions, centerHz: 14_050_000, spanHz: 100_000 };
const settings = { ...defaultStackedTraceSettings, rows: 4, rowPeriodMs: 100 };
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
    expect(r.updateOptions({ refLevel: 10 })).toBe(true);
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
    // Bins per column drive the peak taps: 2048 bins over 100 columns.
    const taps = f.named('uniform4f').filter(call => call.args[0] === 'uPlace').map(call => call.args[2] as number);
    expect(taps[1]).toBeCloseTo(2048 / 99, 6);
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
    let located = 0;
    for (let i = 1; i <= 500; i++) {
      r.pushRow(pixels, options, i * 33); r.draw(i * 33);
      located += r.locate((i % 50) / 50, (i % 37) / 37);
    }
    expect(allocated()).toBe(0);
    expect(located).toBeGreaterThan(0);
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
    projectStackedDepth(depth, defaultStackedTraceSettings, placed);
    return (defaultStackedTraceSettings.frontInsetPx + placed.baseline * height) / height * 2 - 1;
  };
  return { rows: bases.length, oldest: bases[0], baseAt };
}

describe('MOR-4463 stacked-trace time base', () => {
  it('holds six seconds of history at a 90 ms cadence that does not divide the row period', () => {
    const f = webglFixture(200, 100);
    const r = new StackedTraceRenderer(f.canvas, options);
    for (let t = 0; t <= 6_480; t += 90) r.pushRow(frame(), options, t);
    const painted = paintedHistory(f, r, 6_480, 100);
    expect(painted.rows).toBeGreaterThanOrEqual(58);
    expect(painted.rows).toBeLessThanOrEqual(60);
    expect(painted.oldest).toBeGreaterThan(painted.baseAt(0.9));
    expect(painted.oldest).toBeLessThanOrEqual(painted.baseAt(1));
  });

  it('holds six seconds of history from a slow 400 ms source: fewer rows, same depth', () => {
    const f = webglFixture(200, 100);
    const r = new StackedTraceRenderer(f.canvas, options);
    for (let t = 0; t <= 12_000; t += 400) r.pushRow(frame(), options, t);
    const painted = paintedHistory(f, r, 12_000, 100);
    // Rows committed at 6 400 … 12 000 ms are younger than six seconds.
    expect(painted.rows).toBe(15);
    expect(painted.oldest).toBeGreaterThan(painted.baseAt(0.9));
    expect(painted.oldest).toBeLessThanOrEqual(painted.baseAt(1));
  });

  it('holds six seconds of history under jitter', () => {
    const f = webglFixture(200, 100);
    const r = new StackedTraceRenderer(f.canvas, options);
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
    const r = new StackedTraceRenderer(f.canvas, options);
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
    const r = new StackedTraceRenderer(f.canvas, options);
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
    const r = new StackedTraceRenderer(f.canvas, options);
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
    const r = new StackedTraceRenderer(f.canvas, options);
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
    expect((f.named('texSubImage2D')[0].args[8] as Uint8Array)[5000 >> 4]).toBe(80);
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

/** What one draw placed on screen, front row first: fractions of the pane, y upward. */
function placedRows(f: ReturnType<typeof webglFixture>, r: StackedTraceRenderer, now: number, height: number) {
  f.calls.length = 0;
  r.draw(now);
  return f.named('uniform4f').filter(call => call.args[0] === 'uPlace').map(call => ({
    base: ((call.args[1] as number) + 1) / 2, trace: (call.args[3] as number) / height, scale: call.args[4] as number,
  })).reverse();
}

describe('MOR-4463 stacked-trace perspective', () => {
  const grid = Array.from({ length: 19 }, (_, i) => (i + 1) / 20);

  it('draws every row at its own size about the middle of the pane', () => {
    const f = webglFixture(400, 200);
    const r = new StackedTraceRenderer(f.canvas, options);
    for (let t = 0; t <= 6_000; t += 100) r.pushRow(frame(), options, t);
    const rows = placedRows(f, r, 60_000, 200);
    expect(rows.length).toBeGreaterThan(55);
    expect(rows[0].scale).toBe(1);
    const placed = { baseline: 0, scale: 0, fog: 0 };
    for (let i = 1; i < rows.length; i++) {
      expect(rows[i].scale).toBeLessThan(rows[i - 1].scale);
      // Width and height shrink together: the trace height is the front one times the scale.
      expect(rows[i].trace).toBeCloseTo(rows[0].trace * rows[i].scale, 6);
    }
    projectStackedDepth(1, defaultStackedTraceSettings, placed);
    expect(rows[rows.length - 1].scale).toBeGreaterThan(placed.scale);
    expect(rows[rows.length - 1].scale).toBeLessThan(placed.scale * 1.1);
    const floor = f.named('uniform3f').find(call => call.args[0] === 'uFloor')!.args;
    const s = defaultStackedTraceSettings;
    expect(floor.slice(1)).toEqual([s.frontInsetPx, s.floorRise * (1 + s.recede) * 200, s.recede]);
  });

  // The sweep: a pointer on the visible strip of each row — just under its
  // crest, above every nearer row — at a grid of frequencies. The screen
  // points come from what the draw handed to GL, not from `locate` itself.
  it.each([['at rest', 60_000], ['while the rows glide', 6_050]])('maps a click on any row to the frequency drawn there, %s', (_, now) => {
    const f = webglFixture(1200, 1200);
    const r = new StackedTraceRenderer(f.canvas, options);
    for (let t = 0; t <= 6_000; t += 100) r.pushRow(frame(475, 40), options, t);
    const rows = placedRows(f, r, now, 1200);
    expect(rows.length).toBeGreaterThan(55);
    for (const row of rows) {
      // A level of 40 of 80 stands half a trace height above the baseline.
      const up = row.base + 0.5 * row.trace - 1e-4;
      for (const fraction of grid) {
        expect(r.locate(0.5 + (fraction - 0.5) * row.scale, 1 - up)).toBeCloseTo(fraction, 9);
      }
    }
  });

  it('maps a click on the baseline of any flat row to its frequency', () => {
    const f = webglFixture(1200, 1200);
    const r = new StackedTraceRenderer(f.canvas, options);
    for (let t = 0; t <= 6_000; t += 100) r.pushRow(frame(475, 0), options, t);
    for (const row of placedRows(f, r, 60_000, 1200)) {
      for (const fraction of grid) {
        expect(r.locate(0.5 + (fraction - 0.5) * row.scale, 1 - row.base)).toBeCloseTo(fraction, 9);
      }
    }
  });

  it('tunes to a tall signal clicked anywhere on its body, not to the floor behind it', () => {
    const f = webglFixture(1200, 1200);
    const r = new StackedTraceRenderer(f.canvas, options);
    // Sample 380 of 475 is view fraction 0.8; noise elsewhere is flat zero.
    const carrier = frame(475, 0); carrier.fill(80, 376, 385);
    for (let t = 0; t <= 300; t += 100) r.pushRow(carrier, options, t);
    const [front] = placedRows(f, r, 60_000, 1200);
    for (const part of [0.1, 0.5, 0.95]) {
      expect(r.locate(0.8, 1 - (front.base + part * front.trace))).toBeCloseTo(0.8, 9);
    }
    // Beside the signal, at the same height, nothing is drawn: the pointer is on the floor.
    const point = { depth: 0, fraction: 0 };
    const s = defaultStackedTraceSettings;
    unprojectStackedFloor(0.6, 0.5 * front.trace, s, point);
    expect(point.fraction).toBeGreaterThan(0.62);
    expect(r.locate(0.6, 1 - (front.base + 0.5 * front.trace))).toBeCloseTo(point.fraction, 9);
  });

  it('looks through the part of a row that holds no samples to the row behind it', () => {
    const f = webglFixture(1200, 1200);
    const r = new StackedTraceRenderer(f.canvas, options, settings);
    const half = { ...options, coverage: { startHz: 14_000_000, endHz: 14_050_000 } };
    r.pushRow(frame(475, 40), options, 0);
    r.pushRow(frame(475, 80), half, 100);
    r.pushRow(frame(475, 80), half, 200);
    const rows = placedRows(f, r, 60_000, 1200);
    const oldest = rows[rows.length - 1];
    // Under the full-scale front rows on the covered half; through them on the other.
    const up = oldest.base + 0.25 * oldest.trace;
    expect(r.locate(0.5 - 0.3 * rows[0].scale, 1 - up)).toBeCloseTo(0.2, 9);
    expect(r.locate(0.5 + 0.3 * oldest.scale, 1 - up)).toBeCloseTo(0.8, 9);
  });

  it('keeps the stored rows clickable when a wider frame widens the ring', () => {
    const f = webglFixture(1200, 1200);
    const r = new StackedTraceRenderer(f.canvas, options, settings);
    const narrow = frame(100, 0); narrow.fill(80, 20, 30);
    r.pushRow(narrow, options, 0);
    r.pushRow(frame(1000, 0), options, 100);
    r.pushRow(frame(1000, 0), options, 200);
    const rows = placedRows(f, r, 60_000, 1200);
    const oldest = rows[rows.length - 1], up = oldest.base + 0.5 * oldest.trace;
    expect(r.locate(0.5 + (0.25 - 0.5) * oldest.scale, 1 - up)).toBeCloseTo(0.25, 9);
    // The same height off the signal is no longer on that row.
    expect(r.locate(0.5 + (0.6 - 0.5) * oldest.scale, 1 - up)).not.toBeCloseTo(0.6, 3);
  });

  it('answers by the floor before any frame, and by the front row in the label strip', () => {
    const f = webglFixture(400, 200);
    const r = new StackedTraceRenderer(f.canvas, options);
    expect(r.locate(0.3, 0.5)).toBe(0.3);
    r.draw(0);
    const s = defaultStackedTraceSettings, point = { depth: 0, fraction: 0 };
    unprojectStackedFloor(0.4, 0.3 - s.frontInsetPx / 200, s, point);
    expect(r.locate(0.4, 0.7)).toBeCloseTo(point.fraction, 12);
    expect(r.locate(0.3, 0.99)).toBeCloseTo(0.3, 12);
    // Outside the floor the answer stays inside the view.
    expect(r.locate(0.02, 0.5)).toBe(0);
    expect(r.locate(0.98, 0.3)).toBe(1);
  });

  it('follows the pixel scale: the front baseline of a click moves with the inset', () => {
    const f = webglFixture(400, 200);
    const r = new StackedTraceRenderer(f.canvas, options);
    r.setPixelScale(2);
    r.draw(0);
    const s = defaultStackedTraceSettings, point = { depth: 0, fraction: 0 };
    unprojectStackedFloor(0.4, 0.3 - 2 * s.frontInsetPx / 200, s, point);
    expect(r.locate(0.4, 0.7)).toBeCloseTo(point.fraction, 12);
  });
});

describe('MOR-4463 stacked-trace markers in the scene', () => {
  const colors = { tune: Float32Array.of(1, 0, 0, 0.75), bandFill: Float32Array.of(0, 0, 1, 0.15), bandEdge: Float32Array.of(0, 0, 1, 0.4) };

  it('hands the marker, the passband and the coverage to the floor and to every row', () => {
    const f = webglFixture(400, 200);
    const r = new StackedTraceRenderer(f.canvas, options, settings);
    r.pushRow(frame(), options, 0); r.pushRow(frame(), options, 100);
    r.setMarkers({ tune: 0.37, bandLo: 0.37, bandHi: 0.394, coverLo: 0.1, coverHi: 0.9 }, colors);
    f.calls.length = 0;
    r.draw(100);
    // Once for the floor program and once for the row program.
    const marks = f.named('uniform3f').filter(call => call.args[0] === 'uMark');
    expect(marks.map(call => call.args.slice(1))).toEqual([[0.37, 0.37, 0.394], [0.37, 0.37, 0.394]]);
    expect(f.named('uniform2f').find(call => call.args[0] === 'uHatch')!.args.slice(1)).toEqual([0.1, 0.9]);
    for (const [name, value] of [['uTuneColor', colors.tune], ['uBandFill', colors.bandFill], ['uBandEdge', colors.bandEdge]] as const) {
      const sent = f.named('uniform4fv').filter(call => call.args[0] === name);
      expect(sent).toHaveLength(2);
      expect(sent[0].args[1]).toBe(value);
    }
    expect(f.named('uniform1f').find(call => call.args[0] === 'uLine')!.args[1]).toBe(1.25);
  });

  it('draws no marker and no passband until it is given them', () => {
    const f = webglFixture(400, 200);
    const r = new StackedTraceRenderer(f.canvas, options, settings);
    r.pushRow(frame(), options, 0);
    f.calls.length = 0;
    r.draw(0);
    const marks = f.named('uniform3f').filter(call => call.args[0] === 'uMark');
    expect(marks).toHaveLength(2);
    for (const mark of marks) {
      expect(mark.args[1]).toBeLessThan(0);
      expect(mark.args[3]).toBeLessThanOrEqual(mark.args[2] as number);
    }
    expect(f.named('uniform4fv')).toHaveLength(0);
  });
});
