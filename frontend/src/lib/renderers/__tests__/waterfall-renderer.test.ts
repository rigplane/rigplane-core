import { describe, it, expect } from 'vitest';
import { COLOR_SCHEMES, WaterfallRenderer, defaultWaterfallOptions } from '../waterfall-renderer';
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
  it('uploads the producer buffer at its declared offset without a crop allocation', () => {
    const f = webglFixture(); const r = new WaterfallRenderer(f.canvas,
      { ...defaultWaterfallOptions, sampleOffset: 2, sampleAmplitudeMax: 160 });
    const pixels = new Uint8Array([255, 255, 40, 160]); r.pushRow(pixels);
    expect(f.named('texSubImage2D').at(-1)!.args.slice(4)).toEqual([2, 1, f.gl.RED, f.gl.UNSIGNED_BYTE, pixels, 2]);
  });
});
