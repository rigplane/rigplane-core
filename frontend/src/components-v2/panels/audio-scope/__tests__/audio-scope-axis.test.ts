/**
 * MOR-4604 — the AudioScope axis labels the scale the trace is actually drawn
 * at: audio 0..filterHz across the trapezoid, so a test tone sits under its
 * own label.
 */
import { describe, expect, it } from 'vitest';
import {
  renderAudioSpectrum as rawRenderAudioSpectrum,
  AudioSpectrumRendererState,
  type SpectrumState,
} from '../audio-spectrum-renderer';
import { recordCanvasLayers } from '$lib/renderers/__tests__/canvas-layers-fixture';
import type { ScopeAudioProjection } from '$lib/runtime/scope-frame';
import { toAudioSpectrumProps } from '$lib/runtime/props/panel-props';
import {
  FTX1_CAPABILITIES,
  FTX1_STATE,
} from '$lib/runtime/adapters/__tests__/fixtures/ftx1-profile';
import {
  IC7300_CAPABILITIES,
  IC7300_STATE,
} from '$lib/runtime/adapters/__tests__/fixtures/ic7300-profile';
import type { ServerState } from '$lib/types/state';

function renderAudioSpectrum(ctx: CanvasRenderingContext2D, width: number, height: number, state: SpectrumState, rs = new AudioSpectrumRendererState()) {
  recordCanvasLayers(ctx); return rawRenderAudioSpectrum(ctx, width, height, state, rs);
}

const TRACE_STYLE = 'rgba(0, 220, 220, 0.9)';

function captureCtx(width: number, height: number) {
  let style = '';
  let path: [number, number][] = [];
  const strokes: { style: string; points: [number, number][] }[] = [];
  const texts: { text: string; x: number; y: number }[] = [];
  const ctx = new Proxy({
    canvas: { width, height },
    beginPath() { path = []; },
    moveTo(x: number, y: number) { path.push([x, y]); },
    lineTo(x: number, y: number) { path.push([x, y]); },
    closePath() {},
    stroke() { strokes.push({ style, points: [...path] }); },
    fill() {},
    fillText(text: string, x: number, y: number) { texts.push({ text: String(text), x, y }); },
    createLinearGradient: () => ({ addColorStop() {} }),
    set strokeStyle(value: string) { style = value; },
  }, { get: (target, key) => key in target ? Reflect.get(target, key) : () => {} }) as unknown as CanvasRenderingContext2D;
  return { ctx, strokes, texts };
}

// ── Renderer geometry mirrors (width 400, height 160, 2400/3600, centered) ──
// The same expressions the notch/PBT placement tests pin.
const W = 400, H = 160;
const TRAP_H = H - 18 - 16;                 // 126
const SLOPE = TRAP_H * 0.35;                // 44.1
const FILTER_RATIO = Math.min(1, 2400 / 3600) * 0.75;
const TOP_HALF_W = (W * 0.45 - SLOPE) * FILTER_RATIO;
const BL = W / 2 - TOP_HALF_W - SLOPE;      // 87.95

/** Passband view [carrier−300, carrier+2700] for a 2400 Hz SSB filter. */
const USB_PROJECTION: ScopeAudioProjection = { zero: 0.1, perHz: 1 / 3000, spanHz: 2700 };
const LSB_PROJECTION: ScopeAudioProjection = { zero: 0.9, perHz: -1 / 3000, spanHz: 2700 };

function baseState(pixels: Uint8Array, projection: ScopeAudioProjection): SpectrumState {
  return {
    pixels, bandwidth: 3000, projection, filterWidth: 2400, filterWidthMax: 3600,
    ifShift: 0, pbtInner: 128, pbtOuter: 128, manualNotch: false, notchFreq: 128,
    contour: 0, contourFreq: 128,
  };
}

function tracePeakX(strokes: { style: string; points: [number, number][] }[]): number {
  const trace = strokes.find((stroke) => stroke.style === TRACE_STYLE);
  expect(trace).toBeDefined();
  return trace!.points.reduce((best, point) => (point[1] < best[1] ? point : best))[0];
}

describe('audio axis labels the drawn passband (MOR-4604)', () => {
  it.each([
    ['USB', USB_PROJECTION, 69],   // coordinate 0.1 + 1000/3000 → bin 69
    ['LSB', LSB_PROJECTION, 90],   // coordinate 0.9 − 1000/3000 → bin 90
  ] as const)('puts a 1 kHz tone under its label for %s', (_mode, projection, bin) => {
    const pixels = new Uint8Array(160).fill(8);
    pixels[bin] = 160;
    const { ctx, strokes, texts } = captureCtx(W, H);
    renderAudioSpectrum(ctx, W, H, baseState(pixels, projection), new AudioSpectrumRendererState());
    const peakX = tracePeakX(strokes);
    const label = texts.find((entry) => entry.text === '1k');
    expect(label).toBeDefined();
    expect(Math.abs(label!.x - peakX)).toBeLessThanOrEqual(2);
    // The axis runs 0..filterHz along the trapezoid bottom, not ±half-band
    // around the canvas centre.
    const zero = texts.find((entry) => entry.text === '0');
    expect(zero).toBeDefined();
    expect(Math.abs(zero!.x - BL)).toBeLessThan(1);
    expect(texts.some((entry) => entry.text === '+1k' || entry.text === '−1k')).toBe(false);
  });
  // The resolution acceptance (distinct bins across half the trapezoid) is
  // pinned on the real server grant, not a hand-sized frame:
  // RadioLayout.audio-fft.component.test.ts, 'MOR-4604 Audio Scope beside the
  // audio SpectrumPanel', replaying tests/test_audio_scope_passband_replay.py.
});

describe('narrow CW filters keep a usable audio axis (MOR-4604 gate P2)', () => {
  it.each([
    [50, ['0', '10', '20', '30', '40', '50']],
    [100, ['0', '20', '40', '60', '80', '100']],
    [150, ['0', '20', '40', '60', '80', '100', '120', '140']],
  ] as const)('labels a %i Hz filter with nonzero ticks', (filterWidth, labels) => {
    const pixels = new Uint8Array(64).fill(8);
    const { ctx, texts } = captureCtx(W, H);
    renderAudioSpectrum(ctx, W, H, { ...baseState(pixels, USB_PROJECTION), filterWidth });
    expect(texts.filter((entry) => entry.y === H - 3).map((entry) => entry.text)).toEqual(labels);
  });
});

describe('wide and both-sideband modes keep rendering (MOR-4604)', () => {
  it('AM on the FTX-1 (native IF-shift profile) draws trace and axis to 6 kHz', () => {
    const state: ServerState = structuredClone(FTX1_STATE);
    state.main.mode = 'AM'; state.main.filterWidth = 6000; state.main.ifShift = 0;
    const props = toAudioSpectrumProps(state, FTX1_CAPABILITIES);
    expect(props.filterWidth).toBe(6000);
    // AM passband view [carrier−750, carrier+6750]: span 7500.
    const projection: ScopeAudioProjection = { zero: 0.1, perHz: 1 / 7500, spanHz: 6750 };
    const pixels = new Uint8Array(160).fill(8);
    pixels[58] = 160;  // 2000 Hz: coordinate 0.1 + 2000/7500 → bin 58
    const { ctx, strokes, texts } = captureCtx(W, H);
    expect(() => renderAudioSpectrum(ctx, W, H, {
      pixels, bandwidth: 7500, projection, ...props,
    }, new AudioSpectrumRendererState())).not.toThrow();
    const peakX = tracePeakX(strokes);
    const label = texts.find((entry) => entry.text === '2k');
    expect(label).toBeDefined();
    expect(Math.abs(label!.x - peakX)).toBeLessThanOrEqual(2);
  });

  it('FM on the IC-7300 (twin-PBT profile, no FM lattice step) draws trace and axis to 12 kHz', () => {
    const state: ServerState = structuredClone(IC7300_STATE);
    state.main.mode = 'FM'; state.main.filterWidth = 12000;
    const props = toAudioSpectrumProps(state, IC7300_CAPABILITIES);
    expect(props.filterWidth).toBe(12000);
    expect(props.pbtStepHz).toBeUndefined();
    // FM passband view [carrier−1500, carrier+13500]: span 15000.
    const projection: ScopeAudioProjection = { zero: 0.1, perHz: 1 / 15000, spanHz: 13500 };
    const pixels = new Uint8Array(160).fill(8);
    pixels[74] = 160;  // 5500 Hz: coordinate 0.1 + 5500/15000 ≈ 0.4667 → bin 74
    const { ctx, strokes, texts } = captureCtx(W, H);
    expect(() => renderAudioSpectrum(ctx, W, H, {
      pixels, bandwidth: 15000, projection, ...props,
    }, new AudioSpectrumRendererState())).not.toThrow();
    expect(strokes.some((stroke) => stroke.style === TRACE_STYLE)).toBe(true);
    expect(texts.some((entry) => entry.text === '12k')).toBe(true);
  });
});
