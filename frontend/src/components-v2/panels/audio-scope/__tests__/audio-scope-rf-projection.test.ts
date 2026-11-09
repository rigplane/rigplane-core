import { webglFixture } from '$lib/renderers/__tests__/webgl-fixture';
import { describe, expect, it, vi } from 'vitest';
import { audioScopeGoldenNames, loadAudioScopeGolden } from '$lib/runtime/__tests__/scope-wire-fixture';
import { ingressScopeFrame } from '$lib/runtime/adapters/scope-adapter';
import { renderAudioSpectrum as rawRenderAudioSpectrum, AudioSpectrumRendererState, type SpectrumState } from '../audio-spectrum-renderer';
import { recordCanvasLayers } from '$lib/renderers/__tests__/canvas-layers-fixture';
function renderAudioSpectrum(ctx: CanvasRenderingContext2D, width: number, height: number, state: SpectrumState, rs = new AudioSpectrumRendererState()) {
  recordCanvasLayers(ctx); return rawRenderAudioSpectrum(ctx, width, height, state, rs);
}
import { renderSpectrum as rawRenderSpectrum, SpectrumRenderer as RawSpectrumRenderer, defaultSpectrumOptions } from '$lib/renderers/spectrum-renderer';
import { WaterfallRenderer, defaultWaterfallOptions } from '$lib/renderers/waterfall-renderer';

function context() {
  let style = '', path: number[][] = [];
  const strokes: { style: string; points: number[][] }[] = [];
  const fills: number[][][] = [];
  const ctx = new Proxy({
    canvas: { width: 480, height: 160 },
    beginPath() { path = []; }, moveTo(x: number, y: number) { path.push([x, y]); },
    lineTo(x: number, y: number) { path.push([x, y]); },
    stroke() { strokes.push({ style, points: [...path] }); },
    fill() { fills.push([...path]); },
    createLinearGradient: () => ({ addColorStop() {} }),
    createImageData: (width: number) => ({ width, height: 1, data: new Uint8ClampedArray(width * 4) }),
    putImageData: vi.fn(),
    set strokeStyle(value: string) { style = value; },
  }, { get: (target, key) => key in target ? Reflect.get(target, key) : () => {} }) as unknown as CanvasRenderingContext2D;
  return { ctx, strokes, fills };
}

describe.each(audioScopeGoldenNames)('actual PCM → hub RF frame %s', name => {
  const { buffer, expected } = loadAudioScopeGolden(name);
  const result = ingressScopeFrame(buffer, { source: 'audio_fft', sourceId: 'audio_fft', epoch: 1 });
  if (!result.ok) throw new Error(result.reason);
  const { frame, geometry } = result;
  const carrier = expected.audioPassband.tunedHz + expected.audioPassband.carrierOffsetHz;
  const sign = expected.audioPassband.sideband === 'lower' ? -1 : 1;
  const rfSpan = geometry.view.endHz - geometry.view.startHz;
  const projection = { zero: (carrier - geometry.view.startHz) / rfSpan, perHz: sign / rfSpan,
    spanHz: sign === 1 ? geometry.view.endHz - carrier : carrier - geometry.view.startHz };

  if (expected.input.mode === 'LSB' || expected.input.mode === 'CW-L') {
    it('returns right to left along the retained lower-sideband peak envelope', () => {
      const renderer = new SpectrumRenderer();
      renderer.setAvgEnabled(false);
      const options = { ...defaultSpectrumOptions, showRfOverlays: false,
        spanHz: projection.spanHz, centerHz: projection.spanHz / 2,
        sampleWindow: { start: projection.zero, end: projection.zero + projection.perHz * projection.spanHz },
        sampleAmplitudeMax: 160 };
      renderer.render(context().ctx, frame.pixels!, 480, 160, options);
      const { ctx, fills } = context();
      renderer.render(ctx, new Uint8Array(frame.pixels!.length), 480, 160, options);
      const polygon = fills.at(-1)!;
      const peakStart = polygon.findIndex(([, y]) => y < 159);
      expect(peakStart).toBeGreaterThan(0);
      const envelope = polygon.slice(peakStart);
      expect(envelope.length).toBeGreaterThan(50);
      expect(envelope.every(([x], i) => i === 0 || x <= envelope[i - 1][0])).toBe(true);
      expect(polygon[peakStart][0]).toBeGreaterThan(470);
      const peak = envelope.reduce((best, point) => point[1] < best[1] ? point : best);
      expect(peak[0] / 480 * projection.spanHz).toBeCloseTo(expected.input.toneHz, -2);
      expect(frame.pixels?.buffer).toBe(buffer);
    });
  }

  it('places the real 1kHz tone in the native AF trace without a transformed pixel buffer', () => {
    const { ctx, strokes } = context();
    renderAudioSpectrum(ctx, 480, 160, { pixels: frame.pixels, bandwidth: rfSpan,
      projection, filterWidth: 2400, filterWidthMax: 2400, ifShift: 0,
      pbtInner: 128, pbtOuter: 128, manualNotch: false, notchFreq: 128, contour: 0, contourFreq: 128 },
    new AudioSpectrumRendererState());
    const trace = strokes.find(stroke => stroke.style === 'rgba(0, 220, 220, 0.9)')!;
    const peak = trace.points.reduce((best, point) => point[1] < best[1] ? point : best);
    const slope = (160 - 18 - 16) * 0.35;
    const topHalf = (480 * 0.45 - slope) * 0.75;
    const left = Math.floor(240 - topHalf - slope), right = Math.ceil(240 + topHalf + slope);
    expect(Math.abs(peak[0] - (left + (right - left) * expected.input.toneHz / 2400))).toBeLessThan(10);
    expect(frame.pixels?.buffer).toBe(buffer);
  });

  it('maps the same RF pixels through the main spectrum and waterfall at the AF tone position', () => {
    const { ctx, strokes } = context();
    const sampleWindow = { start: projection.zero, end: projection.zero + projection.perHz * projection.spanHz };
    renderSpectrum(ctx, frame.pixels!, 480, 160, { ...defaultSpectrumOptions, showRfOverlays: false,
      spanHz: projection.spanHz, centerHz: projection.spanHz / 2, sampleWindow, sampleAmplitudeMax: 160 });
    const trace = strokes.find(stroke => stroke.points.length >= 480)!;
    const peak = trace.points.reduce((best, point) => point[1] < best[1] ? point : best);
    expect(peak[0] / 480 * projection.spanHz).toBeCloseTo(expected.input.toneHz, -2);
    const f = webglFixture(480, 160);
    const waterfall = new WaterfallRenderer(f.canvas,
      { ...defaultWaterfallOptions, spanHz: projection.spanHz, centerHz: projection.spanHz / 2,
        colorScheme: 'grayscale', sampleWindow, sampleAmplitudeMax: 160 });
    waterfall.pushRow(frame.pixels!);
    const upload = f.named('texSubImage2D').at(-1)!;
    expect(upload.args[8]).toBe(frame.pixels);
    const mapping = f.named('bufferSubData').at(-1)!.args[2] as Float32Array;
    const bin = (expected.input.toneHz - projection.spanHz / 2 - mapping[0]) / mapping[1];
    const peakBin = frame.pixels!.indexOf(Math.max(...frame.pixels!)) / frame.width;
    expect(bin).toBeCloseTo(peakBin, 1);
    waterfall.destroy();
  });
});

function renderSpectrum(...args: Parameters<typeof rawRenderSpectrum>) { recordCanvasLayers(args[0]); return rawRenderSpectrum(...args); }
class SpectrumRenderer extends RawSpectrumRenderer {
  override render(...args: Parameters<RawSpectrumRenderer['render']>) { recordCanvasLayers(args[0]); super.render(...args); }
}
