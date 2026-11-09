import { afterEach, describe, expect, it, vi } from 'vitest';
import { WaterfallRenderer, defaultWaterfallOptions } from '../waterfall-renderer';
import { webglFixture, countTypedArrays } from './webgl-fixture';

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('MOR-4272 texture ring', () => {
  const options = { ...defaultWaterfallOptions, centerHz: 14_050_000, spanHz: 100_000 };
  it('uploads exactly one R8 row and draws one quad per frame, wrapping by height', () => {
    const f = webglFixture(100, 3);
    const r = new WaterfallRenderer(f.canvas, options);
    const pixels = new Uint8Array(100).fill(40);
    r.pushRow(pixels);
    f.calls.length = 0;
    for (let i = 0; i < 10; i++) r.pushRow(pixels);
    const uploads = f.named('texSubImage2D');
    expect(uploads).toHaveLength(10);
    expect(uploads.map(call => call.args[3])).toEqual([1, 2, 0, 1, 2, 0, 1, 2, 0, 1]);
    for (const call of uploads) {
      expect(call.args.slice(4, 8)).toEqual([100, 1, f.gl.RED, f.gl.UNSIGNED_BYTE]);
      expect(call.args[8]).toBe(pixels);
    }
    expect(f.named('drawArrays')).toHaveLength(10);
    expect(f.getContext).toHaveBeenCalledWith('webgl2', expect.any(Object));
  });
  it('allocates no typed arrays over 1000 frames after warm-up', () => {
    const f = webglFixture();
    const r = new WaterfallRenderer(f.canvas, options);
    const pixels = new Uint8Array(100).fill(40);
    r.pushRow(pixels);
    const allocated = countTypedArrays();
    for (let i = 0; i < 1000; i++) r.pushRow(pixels);
    expect(allocated()).toBe(0);
  });
  it('repaints a changed View and appearance through uniforms without a texture mutation', () => {
    const f = webglFixture(); const r = new WaterfallRenderer(f.canvas, options);
    r.pushRow(new Uint8Array(100).fill(40));
    f.calls.length = 0;
    r.updateOptions({ centerHz: 14_051_000, spanHz: 200_000, colorScheme: 'thermal', refLevel: 10 });
    r.draw();
    expect(f.named('texSubImage2D')).toHaveLength(0);
    expect(f.named('texImage2D')).toHaveLength(0);
    expect(f.named('bufferSubData')).toHaveLength(0);
    expect(f.named('drawArrays')).toHaveLength(1);
    expect(f.named('uniform2f').some(call => call.args[0] === 'uView' && call.args[1] === -99_000 && call.args[2] === 200_000)).toBe(true);
    expect(f.named('uniform1f')).toContainEqual({ name: 'uniform1f', args: ['uRef', 10] });
    expect(f.named('uniform3fv').length).toBeGreaterThan(0);
  });
  it('carries coverage in row uniforms and keeps each row sample window through retunes', () => {
    const f = webglFixture(); const r = new WaterfallRenderer(f.canvas, options);
    r.updateOptions({ coverage: { startHz: 14_025_000, endHz: 14_075_000 } });
    r.pushRow(new Uint8Array(100).fill(80));
    const first = f.named('bufferSubData').at(-1)!;
    expect(Array.from(first.args[2] as Float32Array)).toEqual([-50_000, 100_000, -25_000, 25_000]);
    f.calls.length = 0;
    r.updateOptions({ centerHz: 14_060_000, panoramaShiftHz: 0 });
    r.pushRow(new Uint8Array(100).fill(80));
    expect(f.named('bufferSubData')[0].args[1]).toBe(16);
    expect(f.named('texSubImage2D')).toHaveLength(1);
  });
  it('reports WebGL unavailability as a typed error and never requests a 2D context', () => {
    const canvas = document.createElement('canvas');
    const getContext = vi.fn(() => null);
    Object.defineProperty(canvas, 'getContext', { value: getContext });
    let error: unknown;
    try { new WaterfallRenderer(canvas, options); } catch (caught) { error = caught; }
    expect(error).toMatchObject({ code: 'webgl-required', message: 'WebGL required for the waterfall' });
    expect(getContext.mock.calls).toEqual([['webgl2', expect.any(Object)]]);
  });
});
