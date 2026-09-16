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

    // `pixels: null` here is deliberate, not incidental — it isolates
    // this describe block's original (label-only) finding from the
    // separate, more severe crash the second describe block below
    // documents and fixes.
    //
    // MOR-1409 A12 follow-up (coordinator adjudication addendum, comment
    // 5246612628): the guard scope EXPANDED from "placeholder the label"
    // to "skip the entire filter-overlay geometry" — so a non-finite
    // filterWidth now draws NO Filter-prefixed label at all (not even a
    // placeholder), superseding this describe block's original two
    // "does not draw a NaN substring" / "draws the placeholder" tests
    // (the former is now vacuous — there is no label call to inspect —
    // and is replaced by this single "no label at all" pin).
    it('draws no Filter-prefixed label at all for a non-finite filterWidth (guard scope expanded, comment 5246612628)', () => {
      const rs = new AudioSpectrumRendererState();
      const { ctx, fillText } = mockCtxWithFillTextSpy();
      const state = { ...baseState, filterWidth: Number.NaN, pixels: null };
      renderAudioSpectrum(ctx, 400, 160, state, rs);
      const filterLabelCall = fillText.mock.calls.find((call) =>
        String(call[0]).startsWith('Filter:'),
      );
      expect(filterLabelCall).toBeUndefined();
    });

    /**
     * A12 follow-up (MOR-1409, Core #2317, coordinator adjudication
     * addendum comment 5246612628, extending 5246487510). The prior
     * pin here documented that populated `pixels` + a non-finite
     * `filterWidth` threw `RangeError: Invalid array length` — that was
     * a real, more severe defect (not just a label glitch) newly
     * reachable through A12's honest sentinel, out of the original
     * label-only grant. The grant was expanded: the guard now skips the
     * ENTIRE filter-overlay geometry (label, trapezoid, contour, notch,
     * and the trapezoid-clipped spectrum line) when `filterWidth`/
     * `animFilterWidth` is non-finite, so this same populated-pixels
     * case must render without throwing and without any overlay.
     */
    it('renders without throwing for populated pixels + a non-finite filterWidth (was: RangeError)', () => {
      const rs = new AudioSpectrumRendererState();
      const { ctx } = mockCtxWithFillTextSpy();
      const state = { ...baseState, filterWidth: Number.NaN };
      expect(() => renderAudioSpectrum(ctx, 400, 160, state, rs)).not.toThrow();
    });

    it('draws no Filter-overlay label for populated pixels + a non-finite filterWidth', () => {
      const rs = new AudioSpectrumRendererState();
      const { ctx, fillText } = mockCtxWithFillTextSpy();
      const state = { ...baseState, filterWidth: Number.NaN };
      renderAudioSpectrum(ctx, 400, 160, state, rs);
      const filterLabelCall = fillText.mock.calls.find((call) =>
        String(call[0]).startsWith('Filter:'),
      );
      expect(filterLabelCall).toBeUndefined();
    });

    it('still draws the frequency-grid "0" center label for populated pixels + a non-finite filterWidth (the rest of the spectrum renders normally)', () => {
      const rs = new AudioSpectrumRendererState();
      const { ctx, fillText } = mockCtxWithFillTextSpy();
      const state = { ...baseState, filterWidth: Number.NaN };
      renderAudioSpectrum(ctx, 400, 160, state, rs);
      const zeroLabelCall = fillText.mock.calls.find((call) => call[0] === '0');
      expect(zeroLabelCall).not.toBeUndefined();
    });

    it('finite-value regression pin: the overlay is still drawn (Filter label present) for a finite filterWidth', () => {
      const rs = new AudioSpectrumRendererState();
      const { ctx, fillText } = mockCtxWithFillTextSpy();
      const state = { ...baseState, filterWidth: 2400 };
      renderAudioSpectrum(ctx, 400, 160, state, rs);
      const filterLabelCall = fillText.mock.calls.find((call) =>
        String(call[0]).startsWith('Filter:'),
      );
      expect(filterLabelCall?.[0]).toBe('Filter: 2400 Hz');
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

  /**
   * MOR-2475 PR-3 — with a published `notchFreqDomain` the marker is placed
   * by the decoded frequency over the drawn passband (0..filterHz across
   * the trapezoid top tl..tr), clamped to the edge outside the span; with
   * no domain the raw 0-255 placement is byte-for-byte today's. Geometry
   * constants below mirror the renderer's own expressions for width=400,
   * height=160, filterWidth=2400, filterWidthMax=3600, centered PBT.
   */
  describe('manual-notch marker placement (MOR-2475 PR-3)', () => {
    const FTX1_NOTCH_DISPLAY_DOMAIN = { min: 10, max: 3200, step: 10, origin: 10 };

    const TRAP_TOP = 18;
    const TRAP_H = 160 - 18 - 16;
    const FILTER_RATIO = Math.min(1, 2400 / 3600) * 0.75;
    const TOP_HALF_W = (400 * 0.45 - TRAP_H * 0.35) * FILTER_RATIO;
    const TL = 200 - TOP_HALF_W;
    const TR = 200 + TOP_HALF_W;
    const APEX_Y = TRAP_TOP + TRAP_H * 0.55;

    function mockCtxWithPaths() {
      const ctx = mockCtx();
      const paths: [number, number][][] = [];
      let current: [number, number][] = [];
      Object.defineProperty(ctx, 'beginPath', {
        value: () => { current = []; paths.push(current); },
      });
      Object.defineProperty(ctx, 'moveTo', {
        value: (x: number, y: number) => { current.push([x, y]); },
      });
      Object.defineProperty(ctx, 'lineTo', {
        value: (x: number, y: number) => { current.push([x, y]); },
      });
      return { ctx, paths };
    }

    function notchApexX(state: SpectrumState): number {
      const rs = new AudioSpectrumRendererState();
      const { ctx, paths } = mockCtxWithPaths();
      renderAudioSpectrum(ctx, 400, 160, state, rs);
      // The notch triangle is the only path with a point at the apex depth.
      const notch = paths.find((p) => p.some(([, y]) => Math.abs(y - APEX_Y) < 1e-9));
      expect(notch).toBeDefined();
      return notch![1][0];
    }

    it('places a domain-published notch by decoded frequency over the drawn passband', () => {
      const x = notchApexX({
        ...baseState, pixels: null, manualNotch: true,
        notchFreq: 1600, notchFreqDomain: FTX1_NOTCH_DISPLAY_DOMAIN,
      });
      expect(x).toBeCloseTo(TL + (1600 / 2400) * (TR - TL), 9);
    });

    it('keeps the raw 0-255 placement when no domain is published', () => {
      const x = notchApexX({
        ...baseState, pixels: null, manualNotch: true, notchFreq: 100,
      });
      expect(x).toBeCloseTo(TL + (100 / 255) * (TR - TL), 9);
    });

    it('clamps a notch frequency beyond the drawn passband to the trapezoid edge', () => {
      const x = notchApexX({
        ...baseState, pixels: null, manualNotch: true,
        notchFreq: 3200, notchFreqDomain: FTX1_NOTCH_DISPLAY_DOMAIN,
      });
      expect(x).toBeCloseTo(TR, 9);
    });
  });
});
