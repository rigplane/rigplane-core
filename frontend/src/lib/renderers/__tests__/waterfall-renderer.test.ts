import { describe, it, expect } from 'vitest';
import {
  COLOR_SCHEMES, WaterfallRenderer, defaultWaterfallOptions,
  waterfallHatchOffsets, waterfallRowHatches,
} from '../waterfall-renderer';
import { webglFixture } from './webgl-fixture';

describe('waterfall renderer public operations', () => {
  it.each(Object.entries(COLOR_SCHEMES))('%s palette covers the unit interval', (_name, stops) => {
    expect(stops[0].stop).toBe(0); expect(stops.at(-1)!.stop).toBe(1);
  });
  it('keeps hit testing on the displayed View during a sample recenter and zoom', () => {
    const f = webglFixture(200, 50);
    const r = new WaterfallRenderer(f.canvas, { ...defaultWaterfallOptions, centerHz: 14_050_000, spanHz: 100_000 });
    expect(r.pixelToFreq(0)).toBe(14_000_000); expect(r.pixelToFreq(100)).toBe(14_050_000);
    r.updateOptions({ centerHz: 14_051_000, panoramaShiftHz: 1000 });
    expect(r.pixelToFreq(100)).toBe(14_051_000);
    r.updateOptions({ panoramaShiftHz: 0, spanHz: 200_000 });
    expect(r.pixelToFreq(0)).toBe(13_951_000);
    r.resize(400, 100); expect(r.pixelToFreq(200)).toBe(14_051_000);
  });
  it('skips empty or unsized frames and releases owned resources on destroy', () => {
    const f = webglFixture(0, 0); const r = new WaterfallRenderer(f.canvas, defaultWaterfallOptions);
    r.pushRow(new Uint8Array(20)); expect(f.named('texSubImage2D')).toHaveLength(0);
    r.resize(100, 20); r.pushRow(new Uint8Array(0));
    expect(f.named('texSubImage2D')).toHaveLength(0);
    r.pushRow(new Uint8Array(100)); r.destroy();
    const count = f.calls.length;
    r.pushRow(new Uint8Array(100)); r.resize(50, 50); r.clear(); r.draw(); r.destroy();
    expect(f.calls).toHaveLength(count);
    expect(f.named('deleteTexture').length).toBeGreaterThan(0);
    expect(f.named('deleteProgram').length).toBeGreaterThan(0);
  });
  // #4564: the waterfall hatches only where the stored panel decision says.
  // The row record carries the hatch ranges (absolute Hz offsets from the
  // row's sample start); the fragment test is mirrored by
  // waterfallRowHatches below because the GLSL itself is not unit-testable.
  describe('stored hatch decision (#4564)', () => {
    const VIEW = { startHz: 14_000_000, endHz: 14_100_000 };
    it('converts normalized panel ranges to sample-start-relative offsets', () => {
      expect(waterfallHatchOffsets([{ start: 0.9, end: 1 }],
        VIEW.startHz, 100_000, 14_000_000)).toEqual([90_000, 100_000, 0, 0]);
      expect(waterfallHatchOffsets(
        [{ start: 0, end: 0.1 }, { start: 0.9, end: 1 }],
        VIEW.startHz, 100_000, 14_010_000)).toEqual([-10_000, 0, 80_000, 90_000]);
    });
    it('stores no ranges without a panel decision, and clamps to two ranges', () => {
      expect(waterfallHatchOffsets(null, VIEW.startHz, 100_000, 14_000_000)).toEqual([0, 0, 0, 0]);
      expect(waterfallHatchOffsets(undefined, VIEW.startHz, 100_000, 14_000_000)).toEqual([0, 0, 0, 0]);
      expect(waterfallHatchOffsets([], VIEW.startHz, 100_000, 14_000_000)).toEqual([0, 0, 0, 0]);
      expect(waterfallHatchOffsets(
        [{ start: 0, end: 0.1 }, { start: 0.2, end: 0.3 }, { start: 0.4, end: 0.5 }],
        VIEW.startHz, 100_000, 14_000_000)).toEqual([0, 10_000, 20_000, 30_000]);
    });
    it('mirrors the shader range test: start-inclusive, end-exclusive, empty never hatches', () => {
      expect(waterfallRowHatches(90_000, [90_000, 100_000, 0, 0])).toBe(true);
      expect(waterfallRowHatches(99_999, [90_000, 100_000, 0, 0])).toBe(true);
      expect(waterfallRowHatches(100_000, [90_000, 100_000, 0, 0])).toBe(false);
      expect(waterfallRowHatches(89_999, [90_000, 100_000, 0, 0])).toBe(false);
      expect(waterfallRowHatches(25_000, [0, 10_000, 20_000, 30_000])).toBe(true);
      expect(waterfallRowHatches(15_000, [0, 10_000, 20_000, 30_000])).toBe(false);
      expect(waterfallRowHatches(5_000, [0, 0, 0, 0])).toBe(false);
      expect(waterfallRowHatches(-1, [0, 0, 0, 0])).toBe(false);
    });
    it('carries the pushed hatch ranges in the row record', () => {
      const f = webglFixture();
      const r = new WaterfallRenderer(f.canvas,
        { ...defaultWaterfallOptions, centerHz: 14_050_000, spanHz: 100_000 });
      r.updateOptions({ hatchRanges: [{ start: 0.9, end: 1 }] });
      r.pushRow(new Uint8Array(100).fill(64));
      const row = f.named('bufferSubData').at(-1)!.args[2] as Float32Array;
      expect(Array.from(row.slice(12, 16))).toEqual([90_000, 100_000, 0, 0]);
    });
    it('carries no hatch ranges when the panel decided none', () => {
      const f = webglFixture();
      const r = new WaterfallRenderer(f.canvas,
        { ...defaultWaterfallOptions, centerHz: 14_050_000, spanHz: 100_000 });
      r.updateOptions({ hatchRanges: null });
      r.pushRow(new Uint8Array(100).fill(64));
      const row = f.named('bufferSubData').at(-1)!.args[2] as Float32Array;
      expect(Array.from(row.slice(12, 16))).toEqual([0, 0, 0, 0]);
    });
  });
  it('uploads the full producer buffer and carries the sample offset in row uniforms', () => {
    const f = webglFixture(); const r = new WaterfallRenderer(f.canvas,
      { ...defaultWaterfallOptions, sampleOffset: 2, sampleAmplitudeMax: 160 });
    const pixels = new Uint8Array([255, 255, 40, 160]); r.pushRow(pixels);
    expect(f.named('texSubImage2D').at(-1)!.args.slice(4)).toEqual([4, 1, f.gl.RED, f.gl.UNSIGNED_BYTE, pixels, 0]);
    expect((f.named('bufferSubData').at(-1)!.args[2] as Float32Array)[7]).toBe(2);
  });
});
