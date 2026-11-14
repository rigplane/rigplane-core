// Framework-agnostic spectrum renderer for radio scope data.
// Data: Uint8Array of amplitude values (0–160), mapped to canvas width.
// SpectrumRenderer class adds stateful averaging and peak hold on top.

import { getPassbandGeometry } from '../../components/spectrum/passband-geometry';

/**
 * The colour vocabulary a host may restyle. `renderSpectrum` paints every
 * role on the canvas; `tuneLine`, `passbandFill` and `passbandEdge` are also
 * painted by SpectrumPanel's DOM overlay over the waterfall, which reads them
 * from the same resolved record through CSS custom properties.
 */
export interface SpectrumColorRoles {
  trace: string;
  /** Vertical gradient under the trace: `top` at y=0, `bottom` at y=height. */
  traceFill: { top: string; bottom: string };
  grid: string;
  axisText: string;
  tuneLine: string;
  passbandFill: string;
  passbandEdge: string;
}

export const defaultSpectrumColorRoles: SpectrumColorRoles = {
  trace: 'rgba(210,220,230,0.85)',
  traceFill: { top: 'rgba(30,58,138,0.30)', bottom: 'rgba(30,58,138,0.02)' },
  grid: 'rgba(255,255,255,0.15)',
  axisText: 'rgba(180,200,220,0.6)',
  tuneLine: 'rgba(239,68,68,0.75)',
  passbandFill: 'rgba(59,130,246,0.15)',
  passbandEdge: 'rgba(59,130,246,0.4)',
};

/**
 * Fills in every role a host left out. An override whose value is `undefined`
 * — including one under `traceFill` — resolves to the default, pinned by
 * `resolves an undefined-valued override to the default, nested fill included`.
 */
export function resolveSpectrumColorRoles(
  overrides?: Partial<SpectrumColorRoles>,
): SpectrumColorRoles {
  const fallback = defaultSpectrumColorRoles;
  const fill = overrides?.traceFill as Partial<SpectrumColorRoles['traceFill']> | undefined;
  return {
    trace: overrides?.trace ?? fallback.trace,
    traceFill: {
      top: fill?.top ?? fallback.traceFill.top,
      bottom: fill?.bottom ?? fallback.traceFill.bottom,
    },
    grid: overrides?.grid ?? fallback.grid,
    axisText: overrides?.axisText ?? fallback.axisText,
    tuneLine: overrides?.tuneLine ?? fallback.tuneLine,
    passbandFill: overrides?.passbandFill ?? fallback.passbandFill,
    passbandEdge: overrides?.passbandEdge ?? fallback.passbandEdge,
  };
}

export function spectrumColorRolesToOptions(roles: SpectrumColorRoles) {
  return {
    lineColor: roles.trace,
    fillColor: roles.traceFill.top,
    fillColorBottom: roles.traceFill.bottom,
    gridColor: roles.grid,
    textColor: roles.axisText,
    tuneLineColor: roles.tuneLine,
    passbandFillColor: roles.passbandFill,
    passbandEdgeColor: roles.passbandEdge,
  };
}

export interface SpectrumOptions {
  coverage?: Readonly<{ startHz: number; endHz: number }>;
  showRfOverlays?: boolean;
  showTuneIndicator?: boolean;
  bgColor: string;
  lineColor: string;
  fillColor: string;
  fillColorBottom: string;
  gridColor: string;
  textColor: string;
  tuneLineColor: string;
  passbandFillColor: string;
  passbandEdgeColor: string;
  refLevel: number;   // dB reference (reserved for future dB axis labels)
  sampleSpanHz?: number;
  sampleOffset?: number;
  sampleWindow?: Readonly<{ start: number; end: number }>;
  sampleAmplitudeMax?: number;
  spanHz: number;     // frequency span in Hz
  centerHz: number;   // center frequency in Hz
  lineWidth: number;
  // Passband overlay
  tuneHz: number;     // current tuned frequency (0 = hide)
  passbandHz: number; // filter passband width in Hz (0 = hide)
  passbandShiftHz: number; // IF/PBT-derived passband offset from carrier
  mode: string;       // current mode (USB/LSB/CW/AM/FM) — affects passband placement
  scopeMode: number;  // 0=CTR, 1=FIX, 2=SCROLL-C, 3=SCROLL-F
  // MOR-2464: viewport center minus sample-window center, Hz. Positive
  // shifts the sampled content left; positions outside stay blank.
  panoramaShiftHz: number;
  // SOURCE sample-window identity; a change resets sample-space state.
  geometryKey: string;
}

export const defaultSpectrumOptions: SpectrumOptions = {
  bgColor: 'transparent',
  ...spectrumColorRolesToOptions(defaultSpectrumColorRoles),
  refLevel: 0,
  spanHz: 0,
  centerHz: 0,
  lineWidth: 1.2,
  tuneHz: 0,
  passbandHz: 0,
  passbandShiftHz: 0,
  mode: '',
  scopeMode: 0,
  panoramaShiftHz: 0,
  geometryKey: '',
};

function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}

const SPECTRUM_AMPLITUDE_MAX = 80;

function panoramaShiftPixels(options: SpectrumOptions, width: number): number {
  const { spanHz, panoramaShiftHz } = options;
  if (!(spanHz > 0) || !Number.isFinite(panoramaShiftHz)) return 0;
  return (panoramaShiftHz / (options.sampleSpanHz || spanHz)) * width;
}

// Map data to canvas y-coordinates for the shifted viewport (MOR-2464):
// every column samples the bin-space position of its source-column centre,
// (p + 0.5) / width mapped through the sample window into bins, minus half a
// bin, and linearly interpolates the adjacent calibrated bins (edge bins
// repeat) — the same interpolation rule the waterfall uses (MOR-4383).
// Columns whose translated position leaves [0, width-1] stay uncovered in
// `covered`.
function mapShiftedPoints(
  data: Uint8Array,
  width: number,
  height: number,
  shiftPx: number,
  refLevel: number,
  yPoints: Float32Array,
  covered: Uint8Array,
  viewScale = 1,
  offset = 0,
  amplitudeMax = 80,
  sampleWindow?: Readonly<{ start: number; end: number }>,
  coverage?: SpectrumOptions['coverage'], centerHz = 0, spanHz = 0,
): void {
  const n = data.length - offset;
  const start = sampleWindow?.start ?? 0, range = (sampleWindow?.end ?? 1) - start;
  for (let x = 0; x < width; x++) {
    const hz = centerHz - spanHz / 2 + x / width * spanHz;
    if (coverage && (hz < coverage.startHz || hz >= coverage.endHz)) continue;
    const p = width / 2 + (x - width / 2) * viewScale + shiftPx;
    if (p < 0 || p > width - 1) continue;
    const c = start + (p + 0.5) / width * range;
    let sample: number;
    if (c < 0 || c > 1) sample = 0;
    else {
      const pos = Math.min(n - 1, Math.max(0, c * n - 0.5));
      const b0 = Math.floor(pos), fraction = pos - b0;
      const v0 = data[offset + b0], v1 = data[offset + Math.min(b0 + 1, n - 1)];
      const a0 = amplitudeMax === 80 ? v0 : Math.round(Math.min(v0, amplitudeMax) * 80 / amplitudeMax);
      const a1 = amplitudeMax === 80 ? v1 : Math.round(Math.min(v1, amplitudeMax) * 80 / amplitudeMax);
      sample = fraction === 0 ? a0 : a0 * (1 - fraction) + a1 * fraction;
    }
    yPoints[x] = height * (1 - spectrumDisplayAmplitude(sample, refLevel));
    covered[x] = 1;
  }
}

export function spectrumDisplayAmplitude(sample: number, refLevel: number): number {
  const refAdjust = (refLevel / 60) * 40;
  const adjusted = clamp(sample + refAdjust, 0, SPECTRUM_AMPLITUDE_MAX);
  return Math.sqrt(adjusted / SPECTRUM_AMPLITUDE_MAX);
}

type GradCache = {
  grad: CanvasGradient; height: number; fillColor: string; fillColorBottom: string;
};

const paintStates = new WeakMap<CanvasRenderingContext2D, SpectrumPaintState>();
const STATIC_KEYS: readonly (keyof SpectrumOptions)[] = ['bgColor', 'gridColor', 'textColor', 'spanHz', 'centerHz',
  'tuneHz', 'passbandHz', 'passbandShiftHz', 'mode', 'scopeMode', 'showRfOverlays', 'showTuneIndicator',
  'tuneLineColor', 'passbandFillColor', 'passbandEdgeColor', 'sampleSpanHz', 'panoramaShiftHz'];

class SpectrumPaintState {
  trace: Path2D | null = null;
  y = new Float32Array(0);
  covered = new Uint8Array(0);
  readonly background = document.createElement('canvas');
  readonly overlay = document.createElement('canvas');
  options: SpectrumOptions | null = null;
  backingW = 0; backingH = 0; width = 0; height = 0;
  readonly gradient: { current: GradCache | null } = { current: null };
}

function paintState(ctx: CanvasRenderingContext2D): SpectrumPaintState {
  let state = paintStates.get(ctx);
  if (!state) { state = new SpectrumPaintState(); paintStates.set(ctx, state); }
  return state;
}

function staticChanged(state: SpectrumPaintState, ctx: CanvasRenderingContext2D, width: number, height: number, options: SpectrumOptions): boolean {
  const old = state.options;
  if (!old || state.width !== width || state.height !== height
    || state.backingW !== ctx.canvas.width || state.backingH !== ctx.canvas.height
    || old.coverage?.startHz !== options.coverage?.startHz || old.coverage?.endHz !== options.coverage?.endHz) return true;
  for (const key of STATIC_KEYS) if (!Object.is(old[key], options[key])) return true;
  return false;
}

function layerContext(canvas: HTMLCanvasElement, ctx: CanvasRenderingContext2D, width: number, height: number): CanvasRenderingContext2D {
  canvas.width = ctx.canvas.width; canvas.height = ctx.canvas.height;
  const layer = canvas.getContext('2d')!;
  layer.setTransform(canvas.width / width, 0, 0, canvas.height / height, 0, 0);
  return layer;
}

export function renderSpectrum(
  ctx: CanvasRenderingContext2D, data: Uint8Array, width: number, height: number,
  options: SpectrumOptions, gradCache?: { current: GradCache | null },
): void {
  if (!data.length || width <= 0 || height <= 0) return;
  const state = paintState(ctx);
  if (staticChanged(state, ctx, width, height, options)) {
    const background = layerContext(state.background, ctx, width, height);
    drawSpectrumBackground(background, width, height, options);
    const overlay = layerContext(state.overlay, ctx, width, height);
    drawSpectrumOverlay(overlay, width, height, options);
    state.options = { ...options }; state.width = width; state.height = height;
    state.backingW = ctx.canvas.width; state.backingH = ctx.canvas.height;
    state.gradient.current = null;
  }
  ctx.clearRect(0, 0, width, height);
  ctx.drawImage(state.background, 0, 0, width, height);
  const { lineColor, lineWidth, fillColor, fillColorBottom } = options;
  // Map amplitude data to canvas y-coordinates
  // Gain boost: map 0-80 → full height with sqrt curve for better contrast
  // at low signal levels (IC-7610 scope data typically peaks at ~55)
  // Ref level: -30..+30 dB → ±40 on 0-80 scale (same mapping as waterfall)
  const shiftPx = panoramaShiftPixels(options, width);
  if (state.y.length !== width) { state.y = new Float32Array(width); state.covered = new Uint8Array(width); }
  const yPoints = state.y, covered = state.covered;
  covered.fill(0);
  mapShiftedPoints(data, width, height, shiftPx, options.refLevel, yPoints, covered,
    options.spanHz > 0 && options.sampleSpanHz && options.sampleSpanHz > 0 ? options.spanHz / options.sampleSpanHz : 1,
    options.sampleOffset ?? 0, options.sampleAmplitudeMax ?? 80, options.sampleWindow, options.coverage, options.centerHz, options.spanHz);

  // Filled area under spectrum (gradient cached per-instance; recreated only when height or either stop changes)
  const cache = gradCache ?? state.gradient;
  if (!cache.current || cache.current.height !== height
    || cache.current.fillColor !== fillColor || cache.current.fillColorBottom !== fillColorBottom) {
    const grad = ctx.createLinearGradient(0, 0, 0, height);
    grad.addColorStop(0, fillColor);
    grad.addColorStop(1, fillColorBottom);
    cache.current = { grad, height, fillColor, fillColorBottom };
  }
  const path = new Path2D();
  state.trace = path;
  let first = 0, last = width - 1;
  while (first < width && !covered[first]) first++;
  while (last >= first && !covered[last]) last--;
  if (first <= last) {
    path.moveTo(first, yPoints[first]);
    for (let x = first + 1; x <= last; x++) path.lineTo(x, yPoints[x]);
    const fill = new Path2D(path);
    fill.lineTo(last + 1, height); fill.lineTo(first, height); fill.closePath();
    ctx.fillStyle = cache.current.grad; ctx.fill(fill);
  }
  ctx.strokeStyle = lineColor; ctx.lineWidth = lineWidth; ctx.stroke(path);
  ctx.drawImage(state.overlay, 0, 0, width, height);
}

function drawSpectrumBackground(ctx: CanvasRenderingContext2D, width: number, height: number, options: SpectrumOptions): void {
  const { bgColor, gridColor, textColor, spanHz, centerHz } = options;
  if (bgColor !== 'transparent') { ctx.fillStyle = bgColor; ctx.fillRect(0, 0, width, height); }
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
    const texts: string[] = [];
    for (let i = 0; i <= V_LINES; i++) {
      texts.push(((startHz + (i / V_LINES) * spanHz) / 1e6).toFixed(3));
    }
    // MOR-3011: thin the labels to what the width holds. The stride is the
    // smallest k whose clamped label boxes clear each other by LABEL_GAP_PX;
    // the edge labels (0 and V_LINES) are always kept readable. Widths come
    // from measureText, never from hard-coded pixels. Grid lines are unchanged.
    const LABEL_GAP_PX = 4;
    const labelX = (i: number) => clamp((i / V_LINES) * width, 20, width - 20);
    // A real canvas always measures; without measureText there is nothing to
    // thin against, so keep the legacy full draw instead of guessing.
    const canMeasure = typeof ctx.measureText === 'function';
    const labelWidth = (text: string): number => {
      if (!canMeasure) return 0;
      const w = ctx.measureText(text).width;
      return typeof w === 'number' && Number.isFinite(w) ? w : 0;
    };
    const widths = texts.map(labelWidth);
    const strideIndices = (k: number): number[] => {
      const idx: number[] = [];
      for (let i = 0; i <= V_LINES; i += k) idx.push(i);
      if (idx[idx.length - 1] !== V_LINES) idx.push(V_LINES);
      return idx;
    };
    let stride = 1;
    if (canMeasure) {
      stride = V_LINES;
      for (let k = 1; k <= V_LINES; k++) {
        let prevRight = -Infinity;
        let fits = true;
        for (const i of strideIndices(k)) {
          const x = labelX(i);
          if (x - widths[i] / 2 < prevRight + LABEL_GAP_PX) { fits = false; break; }
          prevRight = x + widths[i] / 2;
        }
        if (fits) { stride = k; break; }
      }
    }
    for (const i of strideIndices(stride)) {
      ctx.fillText(texts[i], labelX(i), height - 4);
    }
  }

}

function drawSpectrumOverlay(ctx: CanvasRenderingContext2D, width: number, height: number, options: SpectrumOptions): void {
  const { spanHz, centerHz, tuneHz, passbandHz, passbandShiftHz } = options;
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

        ctx.fillStyle = options.passbandFillColor;
        ctx.fillRect(pbLeft, 0, pbRight - pbLeft, height);

        // Passband edges
        ctx.strokeStyle = options.passbandEdgeColor;
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

    if (options.showTuneIndicator !== false) {
      ctx.strokeStyle = options.tuneLineColor;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(tunePx, 0);
      ctx.lineTo(tunePx, height);
      ctx.stroke();
    }
  }
  const sampleSpan = options.sampleSpanHz || spanHz;
  if (spanHz > 0 && sampleSpan > 0) {
    const sourceCenter = centerHz - options.panoramaShiftHz;
    const viewStart = centerHz - spanHz / 2;
    const left = clamp(((options.coverage?.startHz ?? sourceCenter - sampleSpan / 2) - viewStart) / spanHz * width, 0, width);
    const right = clamp(((options.coverage?.endHz ?? sourceCenter + sampleSpan / 2) - viewStart) / spanHz * width, 0, width);
    if (left <= 0 && right >= width) return;
    ctx.save(); ctx.beginPath(); ctx.rect(0, 0, left, height); ctx.rect(right, 0, width - right, height); ctx.clip();
    ctx.fillStyle = 'rgba(20,30,40,0.65)'; ctx.fillRect(0, 0, width, height);
    ctx.strokeStyle = 'rgba(120,140,160,0.3)'; ctx.lineWidth = 1;
    ctx.beginPath();
    for (let x = -height; x < width; x += 8) { ctx.moveTo(x, 0); ctx.lineTo(x + height, height); }
    ctx.stroke(); ctx.restore();
  }
}

const AVG_DEPTH = 15;
const PEAK_DECAY_MS = 3000;

export class SpectrumRenderer {
  private history = new Uint8Array(0);
  private sums = new Uint32Array(0);
  private averaged = new Uint8Array(0);
  private peakValues = new Uint8Array(0);
  private peakTimestamps = new Float64Array(0);
  private cursor = 0;
  private frames = 0;
  private avgEnabled = true;
  private peakHoldEnabled = true;
  private lastGeometryKey: string | null = null;
  private readonly displayOptions = { ...defaultSpectrumOptions };

  setAvgEnabled(enabled: boolean): void {
    if (enabled !== this.avgEnabled) { this.frames = 0; this.cursor = 0; this.sums.fill(0); this.history.fill(0); }
    this.avgEnabled = enabled;
  }
  setPeakHoldEnabled(enabled: boolean): void {
    if (enabled !== this.peakHoldEnabled) { this.peakValues.fill(0); this.peakTimestamps.fill(0); }
    this.peakHoldEnabled = enabled;
  }
  render(ctx: CanvasRenderingContext2D, data: Uint8Array, width: number, height: number, options: SpectrumOptions): void {
    const offset = options.sampleOffset ?? 0, amplitudeMax = options.sampleAmplitudeMax ?? 80;
    const n = data.length - offset;
    if (n <= 0 || width <= 0 || height <= 0) return;
    if (this.averaged.length !== n) {
      this.history = new Uint8Array(n * AVG_DEPTH); this.sums = new Uint32Array(n);
      this.averaged = new Uint8Array(n); this.peakValues = new Uint8Array(n); this.peakTimestamps = new Float64Array(n);
      this.frames = 0; this.cursor = 0;
    }
    if (options.geometryKey !== this.lastGeometryKey) {
      this.lastGeometryKey = options.geometryKey; this.frames = 0; this.cursor = 0;
      this.sums.fill(0); this.history.fill(0); this.peakValues.fill(0); this.peakTimestamps.fill(0);
    }
    const now = performance.now(), base = this.cursor * n;
    if (this.avgEnabled) this.frames = Math.min(AVG_DEPTH, this.frames + 1);
    for (let i = 0; i < n; i++) {
      const sample = amplitudeMax === 80 ? data[offset + i] : Math.round(Math.min(data[offset + i], amplitudeMax) * 80 / amplitudeMax);
      if (this.avgEnabled) {
        this.sums[i] += sample - this.history[base + i]; this.history[base + i] = sample;
        this.averaged[i] = Math.round(this.sums[i] / this.frames);
      } else this.averaged[i] = sample;
      if (this.peakHoldEnabled && (sample > this.peakValues[i] || now - this.peakTimestamps[i] > PEAK_DECAY_MS)) {
        this.peakValues[i] = sample; this.peakTimestamps[i] = now;
      }
    }
    if (this.avgEnabled) this.cursor = (this.cursor + 1) % AVG_DEPTH;
    Object.assign(this.displayOptions, options);
    this.displayOptions.sampleOffset = 0; this.displayOptions.sampleAmplitudeMax = 80;
    renderSpectrum(ctx, this.averaged, width, height, this.displayOptions);
    if (this.peakHoldEnabled) this.drawPeakHold(ctx, width, height, this.displayOptions);
  }
  private drawPeakHold(ctx: CanvasRenderingContext2D, width: number, height: number, options: SpectrumOptions): void {
    const { trace, covered } = paintState(ctx);
    const shiftPx = panoramaShiftPixels(options, width);
    const peaks = this.peakValues;
    ctx.fillStyle = 'rgba(200, 200, 200, 0.25)';
    const area = new Path2D(trace!);
    let lineOpen = covered.some(Boolean);
    const window = options.sampleWindow;
    const reversed = window !== undefined && window.end < window.start;
    const scale = options.spanHz > 0 && options.sampleSpanHz && options.sampleSpanHz > 0 ? options.spanHz / options.sampleSpanHz : 1;
    for (let step = 0; step < peaks.length; step++) {
      const i = reversed ? step : peaks.length - 1 - step;
      const fraction = window ? (i / peaks.length - window.start) / (window.end - window.start) : i / peaks.length;
      const x = width / 2 + (fraction * width - width / 2 - shiftPx) / scale;
      if (x < 0 || x >= width || !covered[Math.floor(x)]) continue;
      const y = height * (1 - spectrumDisplayAmplitude(peaks[i], options.refLevel));
      if (lineOpen) area.lineTo(x, y); else { area.moveTo(x, y); lineOpen = true; }
    }
    area.closePath(); ctx.fill(area);
  }
}
