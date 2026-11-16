import { afterEach, describe, expect, it, vi } from 'vitest';
import { defaultWaterfallOptions } from '../waterfall-renderer';
import { StackedTraceRenderer, StackedTraceRenderError } from '../stacked-trace-renderer';
import { defaultStackedTraceSettings } from '../stacked-trace-geometry';
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
    // Frame 0 commits slot 0; frames at 30..90 rewrite the live slot 1, the
    // frame at 120 commits it; then slot 2 until the frame at 240 commits it.
    expect(slots).toEqual([0, 1, 1, 1, 1, 2, 2, 2, 2, 3]);
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
    const allocations = f.named('texImage2D').map(call => call.args[3]);
    expect(allocations).toEqual([1, 475, 2048]);
    expect(f.named('copyTexSubImage2D').map(call => call.args[6])).toEqual([1, 475]);
    f.calls.length = 0;
    r.draw(200);
    expect(f.named('uniform3f').filter(call => call.args[0] === 'uSamples').map(call => call.args.slice(1)))
      .toEqual([[475, 0, 80], [2048, 0, 80], [100, 1, 160], [100, 1, 160]]);
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
