import { afterEach, describe, expect, it, vi } from 'vitest';
import { SpectrumRenderer, defaultSpectrumOptions } from '../spectrum-renderer';
import { renderAudioSpectrum, AudioSpectrumRendererState, type SpectrumState } from '../../../components-v2/panels/audio-scope/audio-spectrum-renderer';
import { countTypedArrays } from './webgl-fixture';

function contexts() {
  const labels = vi.fn(); const measure = vi.fn(() => ({ width: 40 }));
  const draw = vi.fn(), line = vi.fn(); const gradient = vi.fn(() => ({ addColorStop() {} }));
  const make = (canvas: HTMLCanvasElement) => new Proxy({ canvas, fillText: labels, measureText: measure,
    drawImage: draw, lineTo: line, createLinearGradient: gradient }, {
    get: (target, key) => key in target ? Reflect.get(target, key) : () => {},
    set: (target, key, value) => Reflect.set(target, key, value),
  }) as unknown as CanvasRenderingContext2D;
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(function (this: HTMLCanvasElement) { return make(this); });
  vi.stubGlobal('Path2D', class { moveTo() {} lineTo() {} closePath() {} });
  const canvas = document.createElement('canvas'); canvas.width = 400; canvas.height = 160;
  return { ctx: make(canvas), labels, measure, draw, gradient, line };
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('MOR-4272 static layers and reused buffers', () => {
  it('reuses the trace geometry for the fill and peak area', () => {
    const { ctx, line } = contexts(); const r = new SpectrumRenderer();
    const pixels = new Uint8Array(100).fill(40);
    r.render(ctx, pixels, 400, 160, defaultSpectrumOptions);
    line.mockClear();
    r.render(ctx, pixels, 400, 160, defaultSpectrumOptions);
    expect(line.mock.calls.length).toBeLessThanOrEqual(pixels.length);
  });
  it('keeps spectrum typed-array allocation constant over 1000 frames including average and peak hold', () => {
    const { ctx } = contexts(); const r = new SpectrumRenderer();
    const pixels = new Uint8Array(100).fill(40);
    r.render(ctx, pixels, 400, 160, defaultSpectrumOptions);
    const allocated = countTypedArrays();
    for (let i = 0; i < 1000; i++) r.render(ctx, pixels, 400, 160, defaultSpectrumOptions);
    expect(allocated()).toBe(0);
  });
  it('reuses spectrum labels, grid and passband until geometry or backing scale changes', () => {
    const { ctx, labels, measure, draw } = contexts(); const r = new SpectrumRenderer();
    const pixels = new Uint8Array(100).fill(40);
    const options = { ...defaultSpectrumOptions, spanHz: 100_000, centerHz: 14_050_000, passbandHz: 2400, mode: 'USB' };
    r.render(ctx, pixels, 400, 160, options);
    const measured = measure.mock.calls.length, labelled = labels.mock.calls.length;
    expect(measured).toBeGreaterThan(0);
    for (let i = 0; i < 20; i++) r.render(ctx, pixels, 400, 160, { ...options });
    expect(measure).toHaveBeenCalledTimes(measured);
    expect(labels).toHaveBeenCalledTimes(labelled);
    expect(draw).toHaveBeenCalled();
    r.render(ctx, pixels, 400, 160, { ...options, centerHz: 14_060_000 });
    expect(measure.mock.calls.length).toBeGreaterThan(measured);
    const before = measure.mock.calls.length;
    ctx.canvas.width = 800; ctx.canvas.height = 320;
    r.render(ctx, pixels, 400, 160, options);
    expect(measure.mock.calls.length).toBeGreaterThan(before);
  });
  it('keeps the audio trapezoid, labels and gradient cached between geometry changes', () => {
    const { ctx, labels, gradient, draw } = contexts(); const rs = new AudioSpectrumRendererState();
    const state: SpectrumState = { pixels: new Uint8Array(100).fill(80), bandwidth: 12000,
      filterWidth: 2400, filterWidthMax: 4000, ifShift: 0, pbtInner: 128, pbtOuter: 128,
      manualNotch: false, notchFreq: 128, contour: 0, contourFreq: 128 };
    renderAudioSpectrum(ctx, 400, 160, state, rs);
    const labelled = labels.mock.calls.length, gradients = gradient.mock.calls.length;
    const allocated = countTypedArrays();
    for (let i = 0; i < 1000; i++) renderAudioSpectrum(ctx, 400, 160, state, rs);
    expect(labels).toHaveBeenCalledTimes(labelled);
    expect(gradient).toHaveBeenCalledTimes(gradients);
    expect(draw).toHaveBeenCalled();
    expect(allocated()).toBe(0);
    renderAudioSpectrum(ctx, 400, 160, { ...state, ifShift: 200 }, rs);
    expect(labels.mock.calls.length).toBeGreaterThan(labelled);
  });
});

it('invalidates the audio gradient on a geometry change without pixels', () => {
  const { ctx, gradient } = contexts(); const rs = new AudioSpectrumRendererState();
  const state: SpectrumState = { pixels: new Uint8Array(100).fill(80), bandwidth: 12000,
    filterWidth: 2400, filterWidthMax: 4000, ifShift: 0, pbtInner: 128, pbtOuter: 128,
    manualNotch: false, notchFreq: 128, contour: 0, contourFreq: 128 };
  renderAudioSpectrum(ctx, 400, 160, state, rs);
  ctx.canvas.height = 320;
  renderAudioSpectrum(ctx, 400, 320, { ...state, pixels: null }, rs);
  renderAudioSpectrum(ctx, 400, 320, state, rs);
  expect(gradient).toHaveBeenLastCalledWith(0, 18, 0, 304);
});
