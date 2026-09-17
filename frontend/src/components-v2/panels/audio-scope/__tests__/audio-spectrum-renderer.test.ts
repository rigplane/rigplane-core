import { describe, it, expect, vi } from 'vitest';
import {
  resetSmoothing,
  renderAudioSpectrum,
  AudioSpectrumRendererState,
  type SpectrumState,
} from '../audio-spectrum-renderer';
import type { PbtRange } from '$lib/radio/filter-controls';

/** The range every PBT-publishing profile declares (ic705/ic7300/ic7610/
 *  ic9700: raw_center 128, display ±1200). The renderer consumes it only as
 *  the gate that a usable range is published; the Hz conversion itself is
 *  the measured lattice (MOR-2497), pinned in `filter-controls.test.ts`. */
const IC7610_PBT_RANGE: PbtRange = { rawCenter: 128, displayMin: -1200, displayMax: 1200 };

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
    const state = { ...baseState, pbtInner: 200, pbtOuter: 56, pbtRange: IC7610_PBT_RANGE };
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

    it('finite-value regression pin: the overlay is still drawn (Filter label present) for a finite filterWidth', () => {      const rs = new AudioSpectrumRendererState();
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

  /**
   * MOR-2475 PR-4 — the PBT overlay converts raw→Hz through the range handed
   * in `SpectrumState.pbtRange`, and only then. MOR-2497 (post-#3519): the
   * lattice step is likewise handed in as `SpectrumState.pbtStepHz` for the
   * current mode. Geometry mirrors the
   * renderer's own expressions for width=400, filterWidth=2400,
   * filterWidthMax=3600 (shiftRef = 2400, totalHalfW = 180): a passed-range
   * inner displacement of +675 Hz centers the inner trapezoid at
   * 200 + (675/2400)·180·0.6 = 230.375, the outer (−675 Hz) at 169.625.
   */
  describe('PBT overlay placement from the passed range (MOR-2475 PR-4)', () => {
    const TRAP_TOP = 18;
    const INNER_PBT_STROKE = 'rgba(80, 180, 255, 0.7)';
    const OUTER_PBT_STROKE = 'rgba(255, 160, 60, 0.7)';
    const PLAIN_STROKE = 'rgba(240, 240, 240, 0.8)';

    type Stroke = { style: string; points: [number, number][] };

    function mockCtxWithStrokes() {
      const ctx = mockCtx();
      const strokes: Stroke[] = [];
      let current: [number, number][] = [];
      let style = '';
      Object.defineProperty(ctx, 'beginPath', {
        value: () => { current = []; },
      });
      Object.defineProperty(ctx, 'moveTo', {
        value: (x: number, y: number) => { current.push([x, y]); },
      });
      Object.defineProperty(ctx, 'lineTo', {
        value: (x: number, y: number) => { current.push([x, y]); },
      });
      Object.defineProperty(ctx, 'stroke', {
        value: () => { strokes.push({ style, points: current }); },
      });
      Object.defineProperty(ctx, 'strokeStyle', {
        set: (v: string) => { style = v; },
      });
      return { ctx, strokes };
    }

    /** Center x of a trapezoid stroke: the midpoint of its top edge. */
    function centerXOf(strokes: Stroke[], color: string): number | undefined {
      const stroke = strokes.find((s) => s.style === color);
      if (!stroke) return undefined;
      const top = stroke.points.filter(([, y]) => y === TRAP_TOP);
      return (top[0][0] + top[top.length - 1][0]) / 2;
    }

    it('places the twin trapezoids by the passed range', () => {
      const { ctx, strokes } = mockCtxWithStrokes();
      renderAudioSpectrum(ctx, 400, 160, {
        ...baseState, pixels: null, pbtInner: 200, pbtOuter: 56,
        pbtRange: IC7610_PBT_RANGE, pbtStepHz: 50,
      }, new AudioSpectrumRendererState());
      // MOR-2497 step 2: placed off the MEASURED lattice at the 2400 Hz filter
      // this fixture declares, which has 2400/50 + 1 = 49 positions. Raw 200 is
      // nearest position 38 (+700 Hz) and raw 56 nearest position 10 (-700 Hz);
      // the centres are then 200 +/- (700/2400)*180*0.6 = 200 +/- 31.5. The
      // retired conversion read these raws as +/-675 Hz -- off the lattice, so
      // not offsets the radio can actually be at.
      expect(centerXOf(strokes, INNER_PBT_STROKE)).toBeCloseTo(231.5, 9);
      expect(centerXOf(strokes, OUTER_PBT_STROKE)).toBeCloseTo(168.5, 9);
    });

    it('draws no PBT overlay and no passband shift when no range is published', () => {
      const { ctx, strokes } = mockCtxWithStrokes();
      renderAudioSpectrum(ctx, 400, 160, {
        ...baseState, pixels: null, pbtInner: 200, pbtOuter: 56,
      }, new AudioSpectrumRendererState());
      expect(strokes.some((s) => s.style === INNER_PBT_STROKE)).toBe(false);
      expect(strokes.some((s) => s.style === OUTER_PBT_STROKE)).toBe(false);
      expect(centerXOf(strokes, PLAIN_STROKE)).toBeCloseTo(200, 9);
    });

    // MOR-2497 (post-#3519): the lattice step is per MODE and handed in as
    // `pbtStepHz` — 200 Hz in AM, 50 Hz in SSB/CW/RTTY. Width 2400, raw 200:
    // +800 Hz at the AM step (+700 at 50 Hz), centres 200 ± Hz·0.045.
    it('places the trapezoids by the passed per-mode step (AM 200 Hz differs from the 50 Hz SSB step)', () => {
      const { ctx, strokes } = mockCtxWithStrokes();
      renderAudioSpectrum(ctx, 400, 160, {
        ...baseState, pixels: null, pbtInner: 200, pbtOuter: 56,
        pbtRange: IC7610_PBT_RANGE, pbtStepHz: 200,
      }, new AudioSpectrumRendererState());
      expect(centerXOf(strokes, INNER_PBT_STROKE)).toBeCloseTo(236, 9);
      expect(centerXOf(strokes, OUTER_PBT_STROKE)).toBeCloseTo(164, 9);
    });

    // A mode without twin PBT (FM) declares no step: the props layer passes
    // none and the renderer has no honest raw→Hz conversion — the trapezoids
    // sit unshifted, exactly as a width that forms no lattice already behaves.
    it('draws the twin trapezoids unshifted when the range is passed without a step', () => {
      const { ctx, strokes } = mockCtxWithStrokes();
      renderAudioSpectrum(ctx, 400, 160, {
        ...baseState, pixels: null, pbtInner: 200, pbtOuter: 56,
        pbtRange: IC7610_PBT_RANGE,
      }, new AudioSpectrumRendererState());
      expect(centerXOf(strokes, INNER_PBT_STROKE)).toBeCloseTo(200, 9);
      expect(centerXOf(strokes, OUTER_PBT_STROKE)).toBeCloseTo(200, 9);
    });
  });
});
