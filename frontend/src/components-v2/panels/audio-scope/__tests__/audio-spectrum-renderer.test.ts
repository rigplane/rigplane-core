import { describe, it, expect, vi } from 'vitest';
import {
  pbtRawToHz,
  resetSmoothing,
  renderAudioSpectrum,
  AudioSpectrumRendererState,
  type SpectrumState,
} from '../audio-spectrum-renderer';

// ── pbtRawToHz ───────────────────────────────────────────────────────────────

describe('pbtRawToHz', () => {
  it('returns 0 for center value (128)', () => {
    expect(pbtRawToHz(128)).toBe(0);
  });

  it('returns positive Hz for values > 128', () => {
    expect(pbtRawToHz(200)).toBe(675);
  });

  it('returns negative Hz for values < 128', () => {
    expect(pbtRawToHz(56)).toBe(-675);
  });

  it('returns max Hz at raw=255', () => {
    expect(pbtRawToHz(255)).toBe(1191);
  });

  it('returns -max Hz at raw=0', () => {
    expect(pbtRawToHz(0)).toBe(-1200);
  });

  it('supports custom center and max', () => {
    expect(pbtRawToHz(64, 64, 600)).toBe(0);
    expect(pbtRawToHz(128, 64, 600)).toBe(600);
  });
});

// ── resetSmoothing ───────────────────────────────────────────────────────────

describe('resetSmoothing', () => {
  it('does not throw', () => {
    expect(() => resetSmoothing()).not.toThrow();
  });

  it('can be called multiple times', () => {
    resetSmoothing();
    resetSmoothing();
  });
});

// ── renderAudioSpectrum ──────────────────────────────────────────────────────

describe('renderAudioSpectrum', () => {
  function mockCtx(): CanvasRenderingContext2D {
    const noop = () => {};
    return {
      clearRect: noop,
      fillRect: noop,
      fillText: noop,
      beginPath: noop,
      moveTo: noop,
      lineTo: noop,
      closePath: noop,
      stroke: noop,
      fill: noop,
      clip: noop,
      save: noop,
      restore: noop,
      quadraticCurveTo: noop,
      createLinearGradient: () => ({ addColorStop: noop }),
      set fillStyle(_: any) {},
      set strokeStyle(_: any) {},
      set lineWidth(_: any) {},
      set font(_: any) {},
      set textAlign(_: any) {},
    } as unknown as CanvasRenderingContext2D;
  }

  const baseState: SpectrumState = {
    pixels: new Uint8Array(100).fill(40),
    bandwidth: 3600,
    filterWidth: 2400,
    filterWidthMax: 3600,
    pbtInner: 128,
    pbtOuter: 128,
    manualNotch: false,
    notchFreq: 128,
    contour: 0,
    contourFreq: 128,
  };

  it('renders without throwing for valid state', () => {
    resetSmoothing();
    expect(() => renderAudioSpectrum(mockCtx(), 400, 160, baseState)).not.toThrow();
  });

  it('renders without throwing for null pixels', () => {
    resetSmoothing();
    const state = { ...baseState, pixels: null };
    expect(() => renderAudioSpectrum(mockCtx(), 400, 160, state)).not.toThrow();
  });

  it('renders without throwing for empty pixels', () => {
    resetSmoothing();
    const state = { ...baseState, pixels: new Uint8Array(0) };
    expect(() => renderAudioSpectrum(mockCtx(), 400, 160, state)).not.toThrow();
  });

  it('renders with PBT active', () => {
    resetSmoothing();
    const state = { ...baseState, pbtInner: 200, pbtOuter: 56 };
    expect(() => renderAudioSpectrum(mockCtx(), 400, 160, state)).not.toThrow();
  });

  it('renders with manual notch', () => {
    resetSmoothing();
    const state = { ...baseState, manualNotch: true, notchFreq: 100 };
    expect(() => renderAudioSpectrum(mockCtx(), 400, 160, state)).not.toThrow();
  });

  it('renders with contour active', () => {
    resetSmoothing();
    const state = { ...baseState, contour: 128, contourFreq: 100 };
    expect(() => renderAudioSpectrum(mockCtx(), 400, 160, state)).not.toThrow();
  });

  it('handles very small canvas', () => {
    resetSmoothing();
    expect(() => renderAudioSpectrum(mockCtx(), 10, 10, baseState)).not.toThrow();
  });

  it('handles max amplitude pixels', () => {
    resetSmoothing();
    const state = { ...baseState, pixels: new Uint8Array(100).fill(160) };
    expect(() => renderAudioSpectrum(mockCtx(), 400, 160, state)).not.toThrow();
  });

  it('handles narrow filter', () => {
    resetSmoothing();
    const state = { ...baseState, filterWidth: 200, filterWidthMax: 3600 };
    expect(() => renderAudioSpectrum(mockCtx(), 400, 160, state)).not.toThrow();
  });

  it('handles wide bandwidth', () => {
    resetSmoothing();
    const state = { ...baseState, bandwidth: 48000 };
    expect(() => renderAudioSpectrum(mockCtx(), 400, 160, state)).not.toThrow();
  });

  /**
   * A12 (MOR-1409, Core #2317, coordinator adjudication comment
   * 5246487510) — a connected receiver that has never reported
   * `filterWidth` (optional field) reaches this renderer as `NaN`
   * (panel-props.ts's `toAudioSpectrumProps` no longer fabricates
   * `?? 2400`). Unguarded, the Filter label draw call renders the literal
   * "Filter: NaN Hz" on the desktop AUDIO SCOPE canvas (verifier-executed
   * probe on the unguarded candidate, `audio-spectrum-renderer.ts:152`).
   * The guard must suppress/placeholder the label instead.
   */
  describe('Filter label — no "NaN" leak for a non-finite filterWidth (MOR-1409 A12)', () => {
    function mockCtxWithFillTextSpy() {
      const ctx = mockCtx();
      const fillText = vi.fn();
      Object.defineProperty(ctx, 'fillText', { value: fillText, writable: true });
      return { ctx, fillText };
    }

    // `pixels: null` here is deliberate, not incidental: with populated
    // `pixels` (this file's `baseState`), a non-finite `filterWidth`
    // propagates into the trapezoid geometry (`bl`/`br` become NaN) and
    // crashes the spectrum-line block below with `RangeError: Invalid
    // array length` (`new Array(numPoints)`, numPoints itself NaN) —
    // independently discovered while adding this test, BEFORE reaching
    // the label draw call's own guard. That crash is a real, separate
    // defect in the trapezoid/spectrum-line geometry math, out of this
    // gate's restricted grant (label-guard only, no further
    // behavior/logic changes) — flagged for the coordinator, not fixed
    // here. `pixels: null` (a real, common state — before the first FFT
    // frame arrives) isolates the label guard this gate DOES own from
    // that separate crash.
    it('does not draw a "NaN" substring in the Filter label for a non-finite filterWidth', () => {
      const rs = new AudioSpectrumRendererState();
      const { ctx, fillText } = mockCtxWithFillTextSpy();
      const state = { ...baseState, filterWidth: Number.NaN, pixels: null };
      renderAudioSpectrum(ctx, 400, 160, state, rs);
      const filterLabelCall = fillText.mock.calls.find((call) =>
        String(call[0]).startsWith('Filter:'),
      );
      expect(filterLabelCall?.[0]).not.toMatch(/NaN/);
    });

    it('draws the established "---"-family placeholder for a non-finite filterWidth', () => {
      const rs = new AudioSpectrumRendererState();
      const { ctx, fillText } = mockCtxWithFillTextSpy();
      const state = { ...baseState, filterWidth: Number.NaN, pixels: null };
      renderAudioSpectrum(ctx, 400, 160, state, rs);
      const filterLabelCall = fillText.mock.calls.find((call) =>
        String(call[0]).startsWith('Filter:'),
      );
      expect(filterLabelCall?.[0]).toBe('Filter: ---');
    });

    it('documents the separate, out-of-grant crash: a non-finite filterWidth WITH populated pixels throws (not this gate\'s fix)', () => {
      const rs = new AudioSpectrumRendererState();
      const { ctx } = mockCtxWithFillTextSpy();
      const state = { ...baseState, filterWidth: Number.NaN };
      expect(() => renderAudioSpectrum(ctx, 400, 160, state, rs)).toThrow(/Invalid array length/);
    });

    it('still draws the real formatted width for a finite filterWidth', () => {
      const rs = new AudioSpectrumRendererState();
      const { ctx, fillText } = mockCtxWithFillTextSpy();
      const state = { ...baseState, filterWidth: 2400 };
      renderAudioSpectrum(ctx, 400, 160, state, rs);
      const filterLabelCall = fillText.mock.calls.find((call) =>
        String(call[0]).startsWith('Filter:'),
      );
      expect(filterLabelCall?.[0]).toBe('Filter: 2400 Hz');
    });
  });
});
