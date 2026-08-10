/**
 * Audio spectrum renderer — draws FFT data INSIDE a filter trapezoid.
 *
 * The trapezoid represents the filter passband. Spectrum line + gradient fill
 * are clipped to the trapezoid shape. Outside the passband nothing is shown.
 */

// ── Types ────────────────────────────────────────────────────────────────────

export interface SpectrumState {
  /** FFT bin amplitudes (0-160 range) */
  pixels: Uint8Array | null;
  /** Effective bandwidth of the FFT data in Hz */
  bandwidth: number;
  /** Filter passband width in Hz */
  filterWidth: number;
  /** Max filter width in Hz (for normalization) */
  filterWidthMax: number;
  /** PBT inner raw value (0-255, center=128) */
  pbtInner: number;
  /** PBT outer raw value (0-255, center=128) */
  pbtOuter: number;
  /** Manual notch active */
  manualNotch: boolean;
  /** Manual notch frequency (0-255 raw) */
  notchFreq: number;
  /** Contour level (0=off, >0=active) */
  contour: number;
  /** Contour center frequency offset (0-255 raw) */
  contourFreq: number;
}

// ── Constants ────────────────────────────────────────────────────────────────

const MAX_AMPLITUDE = 160;
const TOP_LABEL_H = 18;    // "Filter: XXXX Hz" at top
const BOTTOM_LABEL_H = 16; // frequency ticks at bottom

const SPECTRUM_STROKE = 'rgba(0, 220, 220, 0.9)';
const TRAPEZOID_STROKE = 'rgba(240, 240, 240, 0.8)';
const TRAPEZOID_FILL = 'rgba(40, 100, 140, 0.08)';
const NOTCH_COLOR = 'rgba(255, 80, 40, 0.5)';
const CONTOUR_COLOR = 'rgba(180, 140, 255, 0.4)';
const GRID_LINE_COLOR = 'rgba(255, 255, 255, 0.06)';
const GRID_LABEL_COLOR = 'rgba(255, 255, 255, 0.35)';
const FILTER_LABEL_COLOR = 'rgba(180, 220, 255, 0.7)';

// Smoothing — fast attack shows transients, moderate decay avoids flicker
const ATTACK = 0.55;
const DECAY = 0.25;

/** Convert PBT raw (0-255, center=128) to Hz offset. */
export function pbtRawToHz(raw: number, center = 128, maxHz = 1200): number {
  return Math.round((raw - center) * (maxHz / center));
}

/** Per-instance renderer state (avoids module-level singleton sharing). */
export class AudioSpectrumRendererState {
  smoothed: Float32Array | null = null;
  animFilterWidth = 0;

  reset(): void {
    this.smoothed = null;
    this.animFilterWidth = 0;
  }
}

/** @deprecated Use AudioSpectrumRendererState.reset() instead */
export function resetSmoothing(): void {
  // no-op — kept for backward compat; callers should migrate to instance
}

// ── Frequency label helpers ──────────────────────────────────────────────────

function formatHz(hz: number): string {
  const abs = Math.abs(hz);
  const sign = hz < 0 ? '−' : '+';
  if (abs >= 1000) {
    const k = abs / 1000;
    return `${sign}${k % 1 === 0 ? k.toFixed(0) : k.toFixed(1)}k`;
  }
  return `${sign}${abs}`;
}

// Default singleton for backward compat (single-instance usage)
const _defaultState = new AudioSpectrumRendererState();

function chooseGridStep(halfBw: number): number {
  const steps = [100, 200, 500, 1000, 2000, 5000, 10000];
  for (const s of steps) {
    const n = halfBw / s;
    if (n >= 2 && n <= 8) return s;
  }
  return steps[steps.length - 1];
}

// ── Main render ──────────────────────────────────────────────────────────────

export function renderAudioSpectrum(
  ctx: CanvasRenderingContext2D,
  width: number,
  height: number,
  state: SpectrumState,
  rs?: AudioSpectrumRendererState,
): void {
  // Use provided instance state, or fall back to a default singleton for compat
  if (!rs) {
    rs = _defaultState;
  }
  const { pixels, bandwidth, filterWidth, filterWidthMax, pbtInner, pbtOuter,
          manualNotch, notchFreq, contour, contourFreq } = state;

  // Clear
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = 'rgba(8, 12, 18, 0.95)';
  ctx.fillRect(0, 0, width, height);

  const trapTop = TOP_LABEL_H;
  const trapBottom = height - BOTTOM_LABEL_H;
  const trapH = trapBottom - trapTop;
  if (trapH < 10) return;

  // ── Animate filter width ──
  if (rs.animFilterWidth === 0) rs.animFilterWidth = filterWidth;
  const fwDiff = filterWidth - rs.animFilterWidth;
  rs.animFilterWidth += fwDiff * (Math.abs(fwDiff) > 200 ? 0.5 : 0.15);
  if (Math.abs(fwDiff) <= 1) rs.animFilterWidth = filterWidth;

  // ── Trapezoid geometry ──
  const totalHalfW = width * 0.45;
  const whiskerLeft = width / 2 - totalHalfW;
  const whiskerRight = width / 2 + totalHalfW;

  const avgPbtHz = pbtRawToHz(Math.round((pbtInner + pbtOuter) / 2));
  const shiftRef = Math.max(rs.animFilterWidth, filterWidthMax * 0.5);
  const cx = width / 2 + (avgPbtHz / shiftRef) * totalHalfW * 0.6;

  const slopeExtra = trapH * 0.35;
  const filterRatio = Math.max(0.05, Math.min(1, rs.animFilterWidth / Math.max(1, filterWidthMax))) * 0.75;
  const maxTopHalfW = totalHalfW - slopeExtra;
  const topHalfW = Math.max(trapH * 0.08, maxTopHalfW * filterRatio);

  const tl = cx - topHalfW;
  const tr = cx + topHalfW;
  const bl = cx - topHalfW - slopeExtra;
  const br = cx + topHalfW + slopeExtra;

  // ── Filter label (top) ──
  // MOR-1409 A12 (coordinator adjudication, Core #2317, comment
  // 5246487510): a connected receiver that has never reported
  // `filterWidth` (optional field) still reaches this draw call as `NaN`
  // (panel-props.ts's `toAudioSpectrumProps` no longer fabricates `?? 2400`)
  // — `rs.animFilterWidth` latches to `NaN` and stays there (the animation
  // step's own `Math.abs(fwDiff) > 200` check is itself `false` for a NaN
  // diff, so it never recovers), and `Math.round(NaN)` renders the literal
  // "Filter: NaN Hz" on the canvas. Suppress with a placeholder, matching
  // the established dash convention (FilterPanel.svelte's `'--- Hz'`).
  ctx.font = '11px system-ui, sans-serif';
  ctx.textAlign = 'center';
  ctx.fillStyle = FILTER_LABEL_COLOR;
  const filterLabel = Number.isFinite(rs.animFilterWidth)
    ? `Filter: ${Math.round(rs.animFilterWidth)} Hz`
    : 'Filter: ---';
  ctx.fillText(filterLabel, width / 2, TOP_LABEL_H - 5);

  // ── Frequency grid labels (bottom) ──
  const halfBw = bandwidth / 2;
  const step = chooseGridStep(halfBw);
  ctx.font = '9px system-ui, sans-serif';
  ctx.textAlign = 'center';
  ctx.fillStyle = GRID_LABEL_COLOR;

  // Center label
  ctx.fillText('0', width / 2, height - 3);
  ctx.strokeStyle = GRID_LINE_COLOR;
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(cx, trapTop);
  ctx.lineTo(cx, trapBottom);
  ctx.stroke();

  for (let hz = step; hz <= halfBw; hz += step) {
    const frac = hz / halfBw;
    const xPlus = width / 2 + frac * (width / 2);
    const xMinus = width / 2 - frac * (width / 2);
    ctx.fillText(formatHz(hz), xPlus, height - 3);
    ctx.fillText(formatHz(-hz), xMinus, height - 3);
    // Grid lines (subtle)
    ctx.beginPath();
    ctx.moveTo(xPlus, trapTop);
    ctx.lineTo(xPlus, trapBottom);
    ctx.stroke();
    ctx.beginPath();
    ctx.moveTo(xMinus, trapTop);
    ctx.lineTo(xMinus, trapBottom);
    ctx.stroke();
  }

  // ── Draw trapezoid outline + whiskers ──
  const pbtActive = pbtInner !== 128 || pbtOuter !== 128;

  if (pbtActive) {
    // Twin PBT: draw two separate trapezoids with distinct colors
    const innerHz = pbtRawToHz(pbtInner);
    const outerHz = pbtRawToHz(pbtOuter);

    // Inner PBT trapezoid (cyan/blue)
    const innerCx = width / 2 + (innerHz / shiftRef) * totalHalfW * 0.6;
    const iTl = innerCx - topHalfW;
    const iTr = innerCx + topHalfW;
    const iBl = innerCx - topHalfW - slopeExtra;
    const iBr = innerCx + topHalfW + slopeExtra;

    ctx.strokeStyle = 'rgba(80, 180, 255, 0.7)';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(whiskerLeft, trapBottom);
    ctx.lineTo(iBl, trapBottom);
    ctx.lineTo(iTl, trapTop);
    ctx.lineTo(iTr, trapTop);
    ctx.lineTo(iBr, trapBottom);
    ctx.lineTo(whiskerRight, trapBottom);
    ctx.stroke();

    // Outer PBT trapezoid (orange)
    const outerCx = width / 2 + (outerHz / shiftRef) * totalHalfW * 0.6;
    const oTl = outerCx - topHalfW;
    const oTr = outerCx + topHalfW;
    const oBl = outerCx - topHalfW - slopeExtra;
    const oBr = outerCx + topHalfW + slopeExtra;

    ctx.strokeStyle = 'rgba(255, 160, 60, 0.7)';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(oBl, trapBottom);
    ctx.lineTo(oTl, trapTop);
    ctx.lineTo(oTr, trapTop);
    ctx.lineTo(oBr, trapBottom);
    ctx.stroke();
  } else {
    // No PBT: single white trapezoid
    ctx.strokeStyle = TRAPEZOID_STROKE;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(whiskerLeft, trapBottom);
    ctx.lineTo(bl, trapBottom);
    ctx.lineTo(tl, trapTop);
    ctx.lineTo(tr, trapTop);
    ctx.lineTo(br, trapBottom);
    ctx.lineTo(whiskerRight, trapBottom);
    ctx.stroke();
  }

  // Light fill inside trapezoid
  ctx.fillStyle = TRAPEZOID_FILL;
  ctx.beginPath();
  ctx.moveTo(bl, trapBottom);
  ctx.lineTo(tl, trapTop);
  ctx.lineTo(tr, trapTop);
  ctx.lineTo(br, trapBottom);
  ctx.closePath();
  ctx.fill();

  // ── Passband fraction (used for bin mapping below) ──
  const filterHz = Math.round(rs.animFilterWidth);
  const passbandFrac = Math.min(1, filterHz / halfBw);

  // ── Spectrum: gradient fill + line, clipped to trapezoid ──
  if (pixels && pixels.length > 0) {
    const botLeft = Math.max(0, Math.floor(bl));
    const botRight = Math.min(width, Math.ceil(br));
    const numPoints = botRight - botLeft;
    if (numPoints <= 0) return;

    if (!rs.smoothed || rs.smoothed.length !== numPoints) {
      rs.smoothed = new Float32Array(numPoints);
    }

    // Build spectrum line points (only inside trapezoid)
    const points: number[] = new Array(numPoints);
    const dcIdx = Math.floor(pixels.length / 2);
    const positiveLen = pixels.length - dcIdx;

    for (let i = 0; i < numPoints; i++) {
      const x = botLeft + i;

      // Map x position to passband FFT bin
      const barFrac = i / numPoints;
      const pixIdx = dcIdx + Math.floor(barFrac * passbandFrac * positiveLen);
      const clamped = Math.max(dcIdx, Math.min(pixels.length - 1, pixIdx));
      const rawAmp = Math.min(pixels[clamped], MAX_AMPLITUDE) / MAX_AMPLITUDE;

      // Smooth
      const prev = rs.smoothed[i];
      rs.smoothed[i] = rawAmp > prev
        ? prev + (rawAmp - prev) * ATTACK
        : prev + (rawAmp - prev) * DECAY;

      // Clip to trapezoid height at this x
      const distFromCenter = Math.abs(x - cx);
      let maxFrac: number;
      if (distFromCenter <= topHalfW) {
        maxFrac = 1;
      } else if (distFromCenter <= topHalfW + slopeExtra) {
        maxFrac = 1 - (distFromCenter - topHalfW) / slopeExtra;
      } else {
        maxFrac = 0;
      }

      const amp = Math.min(rs.smoothed[i], maxFrac);
      points[i] = trapBottom - amp * trapH;
    }

    // Clip rendering to trapezoid shape
    ctx.save();
    ctx.beginPath();
    ctx.moveTo(bl, trapBottom);
    ctx.lineTo(tl, trapTop);
    ctx.lineTo(tr, trapTop);
    ctx.lineTo(br, trapBottom);
    ctx.closePath();
    ctx.clip();

    // Gradient fill under spectrum line
    ctx.beginPath();
    ctx.moveTo(botLeft, trapBottom);
    for (let i = 0; i < numPoints; i++) {
      ctx.lineTo(botLeft + i, points[i]);
    }
    ctx.lineTo(botRight, trapBottom);
    ctx.closePath();

    const grad = ctx.createLinearGradient(0, trapTop, 0, trapBottom);
    grad.addColorStop(0, 'rgba(0, 220, 180, 0.45)');
    grad.addColorStop(0.4, 'rgba(0, 180, 220, 0.25)');
    grad.addColorStop(1, 'rgba(0, 120, 200, 0.05)');
    ctx.fillStyle = grad;
    ctx.fill();

    // Spectrum line on top
    ctx.beginPath();
    ctx.moveTo(botLeft, points[0]);
    for (let i = 1; i < numPoints; i++) {
      ctx.lineTo(botLeft + i, points[i]);
    }
    ctx.strokeStyle = SPECTRUM_STROKE;
    ctx.lineWidth = 1.5;
    ctx.stroke();

    ctx.restore(); // remove clip

  }

  // ── Contour (inside trapezoid) ──
  if (contour > 0) {
    const contourX = tl + (contourFreq / 255) * (tr - tl);
    const depth = (contour / 255) * trapH * 0.4;
    const cWidth = (tr - tl) * 0.25;

    ctx.strokeStyle = CONTOUR_COLOR;
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(contourX - cWidth, trapTop);
    ctx.quadraticCurveTo(contourX, trapTop + depth * 2, contourX + cWidth, trapTop);
    ctx.stroke();
  }

  // ── Manual notch (inside trapezoid) ──
  if (manualNotch) {
    const notchX = tl + (notchFreq / 255) * (tr - tl);
    const depth = trapH * 0.55;
    const nHalfW = (tr - tl) * 0.06;

    ctx.fillStyle = NOTCH_COLOR;
    ctx.beginPath();
    ctx.moveTo(notchX - nHalfW, trapTop);
    ctx.lineTo(notchX, trapTop + depth);
    ctx.lineTo(notchX + nHalfW, trapTop);
    ctx.closePath();
    ctx.fill();
  }
}
