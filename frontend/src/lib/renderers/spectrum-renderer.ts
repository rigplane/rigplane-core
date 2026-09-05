// Framework-agnostic spectrum renderer for radio scope data.
// Data: Uint8Array of amplitude values (0–160), mapped to canvas width.
// SpectrumRenderer class adds stateful averaging and peak hold on top.

import { getPassbandGeometry } from '../../components/spectrum/passband-geometry';

export interface SpectrumOptions {
  showRfOverlays?: boolean;
  bgColor: string;
  lineColor: string;
  fillColor: string;
  gridColor: string;
  textColor: string;
  refLevel: number;   // dB reference (reserved for future dB axis labels)
  spanHz: number;     // frequency span in Hz
  centerHz: number;   // center frequency in Hz
  lineWidth: number;
  // Passband overlay
  tuneHz: number;     // current tuned frequency (0 = hide)
  passbandHz: number; // filter passband width in Hz (0 = hide)
  passbandShiftHz: number; // IF/PBT-derived passband offset from carrier
  mode: string;       // current mode (USB/LSB/CW/AM/FM) — affects passband placement
  scopeMode: number;  // 0=CTR, 1=FIX, 2=SCROLL-C, 3=SCROLL-F
}

export const defaultSpectrumOptions: SpectrumOptions = {
  bgColor: 'transparent',
  lineColor: 'rgba(210,220,230,0.85)',
  fillColor: 'rgba(30,58,138,0.30)',
  gridColor: 'rgba(255,255,255,0.15)',
  textColor: 'rgba(180,200,220,0.6)',
  refLevel: 0,
  spanHz: 0,
  centerHz: 0,
  lineWidth: 1.2,
  tuneHz: 0,
  passbandHz: 0,
  passbandShiftHz: 0,
  mode: '',
  scopeMode: 0,
};

function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}

const SPECTRUM_AMPLITUDE_MAX = 80;

export function spectrumDisplayAmplitude(sample: number, refLevel: number): number {
  const refAdjust = (refLevel / 60) * 40;
  const adjusted = clamp(sample + refAdjust, 0, SPECTRUM_AMPLITUDE_MAX);
  return Math.sqrt(adjusted / SPECTRUM_AMPLITUDE_MAX);
}

type GradCache = { grad: CanvasGradient; height: number; fillColor: string };

/**
 * Render a spectrum line chart onto an existing 2D canvas context.
 *
 * The caller is responsible for DPR scaling: set canvas physical dimensions
 * to width*dpr × height*dpr, call ctx.setTransform(dpr,0,0,dpr,0,0) once,
 * then pass CSS logical width/height here.
 *
 * Optimization note: the $effect in SpectrumCanvas triggers on every data change;
 * for smoother perf consider throttling to requestAnimationFrame (already done via scheduleRender).
 *
 * @param gradCache - optional per-instance cache object; pass a {current: null} ref to avoid
 *   cross-instance invalidation when multiple SpectrumCanvas components exist at different heights.
 */
export function renderSpectrum(
  ctx: CanvasRenderingContext2D,
  data: Uint8Array,
  width: number,
  height: number,
  options: SpectrumOptions,
  gradCache?: { current: GradCache | null },
): void {
  const n = data.length;
  if (!n || width <= 0 || height <= 0) return;

  const { bgColor, lineColor, fillColor, gridColor, textColor, spanHz, centerHz, lineWidth, tuneHz, passbandHz, passbandShiftHz } =
    options;

  ctx.clearRect(0, 0, width, height);

  if (bgColor !== 'transparent') {
    ctx.fillStyle = bgColor;
    ctx.fillRect(0, 0, width, height);
  }

  // Grid
  const H_LINES = 5;
  const V_LINES = 10;
  ctx.strokeStyle = gridColor;
  ctx.lineWidth = 0.6;
  for (let i = 0; i <= H_LINES; i++) {
    const y = (i / H_LINES) * height;
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(width, y);
    ctx.stroke();
  }
  for (let i = 0; i <= V_LINES; i++) {
    const x = (i / V_LINES) * width;
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, height);
    ctx.stroke();
  }

  // Frequency labels
  if (options.showRfOverlays !== false && spanHz > 0 && centerHz > 0) {
    const startHz = centerHz - spanHz / 2;
    ctx.fillStyle = textColor;
    ctx.font = '9px monospace';
    ctx.textAlign = 'center';
    for (let i = 0; i <= V_LINES; i++) {
      const hz = startHz + (i / V_LINES) * spanHz;
      const x = (i / V_LINES) * width;
      ctx.fillText((hz / 1e6).toFixed(3), clamp(x, 20, width - 20), height - 4);
    }
  }

  // Map amplitude data to canvas y-coordinates
  // Gain boost: map 0-80 → full height with sqrt curve for better contrast
  // at low signal levels (IC-7610 scope data typically peaks at ~55)
  // Ref level: -30..+30 dB → ±40 on 0-80 scale (same mapping as waterfall)
  const yPoints = new Float32Array(width);
  for (let x = 0; x < width; x++) {
    const idx = Math.min(n - 1, Math.floor((x / width) * n));
    const amplitude = spectrumDisplayAmplitude(data[idx], options.refLevel);
    yPoints[x] = height * (1 - amplitude);
  }

  // Filled area under spectrum (gradient cached per-instance; recreated only when height or fillColor changes)
  const cache = gradCache ?? { current: null };
  if (!cache.current || cache.current.height !== height || cache.current.fillColor !== fillColor) {
    const grad = ctx.createLinearGradient(0, 0, 0, height);
    grad.addColorStop(0, fillColor);
    grad.addColorStop(1, 'rgba(30,58,138,0.02)');
    cache.current = { grad, height, fillColor };
  }
  ctx.fillStyle = cache.current.grad;
  ctx.beginPath();
  ctx.moveTo(0, height);
  for (let x = 0; x < width; x++) {
    ctx.lineTo(x, yPoints[x]);
  }
  ctx.lineTo(width, height);
  ctx.closePath();
  ctx.fill();

  // Spectrum line
  ctx.strokeStyle = lineColor;
  ctx.lineWidth = lineWidth;
  ctx.beginPath();
  for (let x = 0; x < width; x++) {
    if (x === 0) ctx.moveTo(0, yPoints[x]);
    else ctx.lineTo(x, yPoints[x]);
  }
  ctx.stroke();

  // Tuning indicator + passband overlay
  if (options.showRfOverlays !== false && spanHz > 0) {
    // scopeMode: 0=CTR, 1=FIX, 2=SCROLL-C, 3=SCROLL-F
    const isFixedScope = options.scopeMode === 1 || options.scopeMode === 3;
    const startHz = centerHz - spanHz / 2;
    const tunePx = (isFixedScope && tuneHz > 0)
      ? clamp(((tuneHz - startHz) / spanHz) * width, 0, width)
      : width / 2;

    // Draw passband rectangle
    if (passbandHz > 0) {
      const geometry = getPassbandGeometry(options.mode ?? '', passbandHz, passbandShiftHz, spanHz, width, tunePx);

      if (geometry && geometry.widthPx > 0) {
        const pbLeft = geometry.leftPx;
        const pbRight = geometry.rightPx;

        ctx.fillStyle = 'rgba(59,130,246,0.15)';
        ctx.fillRect(pbLeft, 0, pbRight - pbLeft, height);

        // Passband edges
        ctx.strokeStyle = 'rgba(59,130,246,0.4)';
        ctx.lineWidth = 1;
        ctx.setLineDash([3, 3]);
        ctx.beginPath();
        ctx.moveTo(pbLeft, 0);
        ctx.lineTo(pbLeft, height);
        ctx.moveTo(pbRight, 0);
        ctx.lineTo(pbRight, height);
        ctx.stroke();
        ctx.setLineDash([]);
      }
    }

    // Center frequency line (carrier)
    ctx.strokeStyle = 'rgba(239,68,68,0.75)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.moveTo(tunePx, 0);
    ctx.lineTo(tunePx, height);
    ctx.stroke();
  }
}

// --- Stateful renderer class (averaging + peak hold) ---

const AVG_DEPTH = 15;  // ~500ms window at 30 fps
const PEAK_DECAY_MS = 3000;

export class SpectrumRenderer {
  private frameHistory: Uint8Array[] = [];
  private peakValues: number[] = [];
  private peakTimestamps: number[] = [];
  private avgEnabled = true;
  private peakHoldEnabled = true;
  private readonly _gradCache: { current: GradCache | null } = { current: null };

  setAvgEnabled(enabled: boolean): void {
    this.avgEnabled = enabled;
    if (!enabled) this.frameHistory = [];
  }

  setPeakHoldEnabled(enabled: boolean): void {
    this.peakHoldEnabled = enabled;
    if (!enabled) {
      this.peakValues = [];
      this.peakTimestamps = [];
    }
  }

  render(
    ctx: CanvasRenderingContext2D,
    data: Uint8Array,
    width: number,
    height: number,
    options: SpectrumOptions,
  ): void {
    const now = performance.now();

    // --- Moving average ---
    let displayData = data;
    if (this.avgEnabled) {
      this.frameHistory.push(new Uint8Array(data));
      if (this.frameHistory.length > AVG_DEPTH) this.frameHistory.shift();
      const averaged = new Uint8Array(data.length);
      for (let i = 0; i < data.length; i++) {
        let sum = 0;
        for (const frame of this.frameHistory) sum += frame[i];
        averaged[i] = Math.round(sum / this.frameHistory.length);
      }
      displayData = averaged;
    }

    // --- Peak hold update ---
    if (this.peakHoldEnabled) {
      const n = data.length;
      if (this.peakValues.length !== n) {
        this.peakValues = new Array(n).fill(0);
        this.peakTimestamps = new Array(n).fill(now);
      }
      for (let i = 0; i < n; i++) {
        if (data[i] > this.peakValues[i]) {
          this.peakValues[i] = data[i];
          this.peakTimestamps[i] = now;
        } else if (now - this.peakTimestamps[i] > PEAK_DECAY_MS) {
          this.peakValues[i] = data[i];
          this.peakTimestamps[i] = now;
        }
      }
    }

    // Draw main spectrum first
    renderSpectrum(ctx, displayData, width, height, options, this._gradCache);

    // Draw peak hold fill on top (subtle overlay above spectrum)
    if (this.peakHoldEnabled) {
      this._drawPeakHold(ctx, displayData, this.peakValues, width, height, options);
    }
  }

  private _drawPeakHold(
    ctx: CanvasRenderingContext2D,
    currentData: Uint8Array,
    peaks: number[],
    width: number,
    height: number,
    options: SpectrumOptions,
  ): void {
    // Draw peak hold as subtle gray fill between current spectrum and peak line
    const n = currentData.length;
    
    ctx.fillStyle = 'rgba(200, 200, 200, 0.25)';
    ctx.beginPath();
    
    // Start from left, draw current spectrum line
    for (let x = 0; x < width; x++) {
      const idx = Math.min(n - 1, Math.floor((x / width) * n));
      const amplitude = spectrumDisplayAmplitude(currentData[idx], options.refLevel);
      const y = height * (1 - amplitude);
      if (x === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    
    // Draw back along peak line (right to left)
    for (let i = peaks.length - 1; i >= 0; i--) {
      const x = (i / peaks.length) * width;
      const amplitude = spectrumDisplayAmplitude(peaks[i], options.refLevel);
      const y = height * (1 - amplitude);
      ctx.lineTo(x, y);
    }
    
    ctx.closePath();
    ctx.fill();
  }
}
