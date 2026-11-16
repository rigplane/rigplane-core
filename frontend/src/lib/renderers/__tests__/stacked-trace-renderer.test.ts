import { afterEach, describe, expect, it, vi } from 'vitest';
import { defaultWaterfallOptions } from '../waterfall-renderer';
import { StackedTraceRenderer, StackedTraceRenderError } from '../stacked-trace-renderer';
import { defaultStackedTraceSettings, projectStackedDepth } from '../stacked-trace-geometry';
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
    // Frame 0 commits slot 0. Commits follow a 100 ms grid: the frame at
    // 120 ms (grid 100) commits slot 1, the frame at 210 ms (grid 200)
    // commits slot 2; the frames between rewrite the live slot.
    expect(slots).toEqual([0, 1, 1, 1, 1, 2, 2, 2, 3, 3]);
    expect(r.historyRows).toBe(3);
    expect(f.getContext).toHaveBeenCalledWith('webgl2', expect.any(Object));
  });

  it('keeps at most the configured number of history rows and reuses the ring', () => {
    const f = webglFixture(200, 100);
    const r = new StackedTraceRenderer(f.canvas, options, settings);
    f.calls.length = 0;
    for (let i = 0; i < 12; i++) r.pushRow(frame(), options, i * 100);
    expect(r.historyRows).toBe(4);
    expect(f.named('texSubImage2D').map(call => call.args[3])).toEqual([0, 1, 2, 3, 4, 0, 1, 2, 3, 4, 0, 1]);
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
      const placed = f.named('uniform3f').filter(call => call.args[0] === 'uPlace').map(call => call.args[1] as number);
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
    const taps = f.named('uniform3f').filter(call => call.args[0] === 'uPlace').map(call => call.args[2] as number);
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
  const bases = f.named('uniform3f').filter(call => call.args[0] === 'uPlace').map(call => call.args[1] as number);
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
    r.pushRow(Uint8Array.of(1, 9, 3, 4, 7), options, 0);
    const upload = f.named('texSubImage2D')[0];
    expect(upload.args[4]).toBe(11);
    expect(Array.from((upload.args[8] as Uint8Array).subarray(0, 11))).toEqual([1, 9, 3, 4, 7, 9, 4, 7, 9, 7, 9]);
  });

  it('skips the sample offset when it builds the levels', () => {
    const f = webglFixture(200, 100);
    const r = new StackedTraceRenderer(f.canvas, options);
    f.calls.length = 0;
    r.pushRow(Uint8Array.of(77, 77, 2, 5, 3), { ...options, sampleOffset: 2 }, 0);
    const upload = f.named('texSubImage2D')[0];
    expect(Array.from((upload.args[8] as Uint8Array).subarray(0, upload.args[4] as number))).toEqual([2, 5, 3, 5, 3, 5]);
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

describe('MOR-4463 stacked-trace resources', () => {
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
      return f.named('uniform2f').find(call => call.args[0] === 'uFloor')!.args[1];
    };
    expect(floor()).toBe(16);
    r.setPixelScale(2);
    expect(floor()).toBe(32);
  });
});
