// TEST-ONLY 2D reference for the MOR-4383 waterfall spec; never imported by
// product code. It mirrors the approved RigDeck presenter behaviour
// (tmp/lane/rd_presenter.c, render_console) on a plain 2D canvas so the
// browser tests can compare the WebGL renderer against an independent
// implementation of the same rules:
//   * temporal IIR per bin, alpha 0.5 per NEW frame (optional);
//   * spatial [1 2 1]/4 across bins with repeated edges, always;
//   * one row group per new frame, row_px = 3 ring rows, blended from the
//     previous filtered frame as k/3 (1/3, 2/3, 1), the last row being the
//     current frame; without a previous frame of the same geometry every
//     group row is the current frame;
//   * every screen column samples the bin-space position of its centre,
//     (p + 0.5) * n / W - 0.5, and linearly interpolates the adjacent bins
//     (edge bins repeat); per-bin amplitude calibration happens before the
//     interpolation;
//   * level mapping: v' = min(v, amplitudeMax) * 80 / amplitudeMax (rounded
//     per bin), t = sqrt(clamp((v' + ref * 40/60) / 80, 0, 1)).

interface ColorStop {
  stop: number;
  color: string;
}

export const COLOR_SCHEMES = {
  classic: [
    { stop: 0.0, color: '#001020' },
    { stop: 0.2, color: '#0040A0' },
    { stop: 0.4, color: '#00C0C0' },
    { stop: 0.6, color: '#00FF00' },
    { stop: 0.8, color: '#FFFF00' },
    { stop: 1.0, color: '#FF0000' },
  ],
  thermal: [
    { stop: 0.0, color: '#000000' },
    { stop: 0.3, color: '#800080' },
    { stop: 0.5, color: '#FF0000' },
    { stop: 0.7, color: '#FF8000' },
    { stop: 1.0, color: '#FFFF00' },
  ],
  grayscale: [
    { stop: 0.0, color: '#000000' },
    { stop: 1.0, color: '#FFFFFF' },
  ],
} satisfies Record<string, ColorStop[]>;

export type ColorSchemeName = keyof typeof COLOR_SCHEMES;

export interface WaterfallOptions {
  colorScheme: ColorSchemeName;
  refLevel: number;
  speed: number;
  centerHz: number;
  sampleSpanHz?: number;
  sampleOffset?: number;
  sampleWindow?: Readonly<{ start: number; end: number }>;
  sampleAmplitudeMax?: number;
  spanHz: number;
  panoramaShiftHz: number;
  temporalSmoothing?: boolean;
}

export const defaultWaterfallOptions: WaterfallOptions = {
  colorScheme: 'classic',
  refLevel: 0,
  speed: 1,
  centerHz: 0,
  spanHz: 0,
  panoramaShiftHz: 0,
};

export interface WaterfallFrameId {
  seq: number;
  epoch: number;
}

const GROUP_ROWS = 3;

function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace('#', '');
  return [parseInt(h.slice(0, 2), 16), parseInt(h.slice(2, 4), 16), parseInt(h.slice(4, 6), 16)];
}

function buildColorLut(scheme: ColorSchemeName): Uint8Array {
  const lut = new Uint8Array(256 * 3);
  const stops = COLOR_SCHEMES[scheme].map(({ stop, color }) => ({ stop, rgb: hexToRgb(color) }));
  for (let v = 0; v < 256; v++) {
    const t = v / 255;
    let r = 0, g = 0, b = 0;
    if (t <= stops[0].stop) {
      [r, g, b] = stops[0].rgb;
    } else if (t >= stops[stops.length - 1].stop) {
      [r, g, b] = stops[stops.length - 1].rgb;
    } else {
      for (let i = 0; i < stops.length - 1; i++) {
        if (t >= stops[i].stop && t <= stops[i + 1].stop) {
          const range = stops[i + 1].stop - stops[i].stop;
          const frac = range > 0 ? (t - stops[i].stop) / range : 0;
          r = Math.round(stops[i].rgb[0] + frac * (stops[i + 1].rgb[0] - stops[i].rgb[0]));
          g = Math.round(stops[i].rgb[1] + frac * (stops[i + 1].rgb[1] - stops[i].rgb[1]));
          b = Math.round(stops[i].rgb[2] + frac * (stops[i + 1].rgb[2] - stops[i].rgb[2]));
          break;
        }
      }
    }
    lut[v * 3] = r;
    lut[v * 3 + 1] = g;
    lut[v * 3 + 2] = b;
  }
  return lut;
}

export class WaterfallRenderer {
  private ctx: CanvasRenderingContext2D;
  private options: WaterfallOptions;
  private lut: Uint8Array;
  private width: number;
  private height: number;
  private rowBuf: ImageData | null = null;
  private destroyed = false;
  // Smoothing state (MOR-4383), kept in bin space.
  private state = new Float32Array(0);
  private filtered = new Float32Array(0);
  private current = new Uint8Array(0);
  private previous = new Uint8Array(0);
  private smoothBins = 0;
  private smoothSpan = 0;
  private smoothCentre = 0;
  private previousValid = false;
  private frameEpoch = 0;
  private lastSeq = 0;

  constructor(canvas: HTMLCanvasElement, options: WaterfallOptions) {
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('Cannot get 2d context from waterfall canvas');
    this.ctx = ctx;
    this.options = { ...options };
    this.lut = buildColorLut(options.colorScheme);
    this.width = canvas.width;
    this.height = canvas.height;
  }

  /** Calibrated value of one stored bin (rounded per bin, before lerp). */
  private calibrated(value: number): number {
    const max = this.options.sampleAmplitudeMax ?? 80;
    return max === 80 ? value : Math.round(Math.min(value, max) * 80 / max);
  }

  pushRow(data: Uint8Array, frame?: WaterfallFrameId): void {
    if (this.destroyed || this.width <= 0 || this.height <= 0) return;
    const offset = this.options.sampleOffset ?? 0;
    const n = data.length - offset;
    if (n <= 0) return;
    if (frame) {
      if (frame.epoch !== this.frameEpoch) {
        this.frameEpoch = frame.epoch;
        this.lastSeq = 0;
        this.smoothBins = 0;
        this.previousValid = false;
      }
      if (frame.seq <= this.lastSeq) return;
      this.lastSeq = frame.seq;
    }
    const span = this.options.sampleSpanHz || this.options.spanHz || 1;
    const centre = this.options.centerHz - this.options.panoramaShiftHz;
    const keep = this.smoothBins === n && this.smoothSpan === span && this.smoothCentre === centre;
    const iir = (this.options.temporalSmoothing ?? true) && keep;
    if (this.state.length !== n) {
      this.state = new Float32Array(n);
      this.filtered = new Float32Array(n);
      this.current = new Uint8Array(n);
      this.previous = new Uint8Array(n);
    }
    for (let i = 0; i < n; i++) {
      const value = data[offset + i];
      this.state[i] = iir ? (this.state[i] + value) / 2 : value;
    }
    for (let i = 0; i < n; i++) {
      const left = this.state[i > 0 ? i - 1 : 0];
      const right = this.state[i + 1 < n ? i + 1 : n - 1];
      this.filtered[i] = (left + 2 * this.state[i] + right) / 4;
      this.current[i] = Math.round(this.filtered[i]);
    }
    const group = Math.min(GROUP_ROWS, this.height);
    const blend = this.previousValid && keep;
    for (let k = 1; k <= group; k++) {
      const row = k === group || !blend
        ? this.current
        : this.current.map((value, i) => Math.round((this.previous[i] * (group - k) + value * k) / group));
      this.paint(offset, n, row);
    }
    this.previous.set(this.current);
    this.previousValid = true;
    this.smoothBins = n;
    this.smoothSpan = span;
    this.smoothCentre = centre;
  }

  private paint(offset: number, n: number, row: Uint8Array | number[]): void {
    const ctx = this.ctx, w = this.width, h = this.height;
    ctx.drawImage(ctx.canvas, 0, 0, w, h - 1, 0, 1, w, h - 1);
    if (!this.rowBuf || this.rowBuf.width !== w) this.rowBuf = ctx.createImageData(w, 1);
    const out = this.rowBuf.data;
    const window_ = this.options.sampleWindow;
    const start = window_?.start ?? 0, range = (window_?.end ?? 1) - start;
    const scale = this.options.sampleSpanHz && this.options.sampleSpanHz > 0
      ? this.options.spanHz / this.options.sampleSpanHz : 1;
    const shiftPx = (this.options.panoramaShiftHz / (this.options.sampleSpanHz || this.options.spanHz)) * w;
    const refAdjust = (this.options.refLevel / 60) * 40;
    for (let x = 0; x < w; x++) {
      const p = w / 2 + (x - w / 2) * scale + shiftPx;
      const pi = x * 4;
      out[pi + 3] = 255;
      if (p < 0 || p > w - 1) {
        out[pi] = 0x00; out[pi + 1] = 0x10; out[pi + 2] = 0x20;
        continue;
      }
      const c = start + (p + 0.5) / w * range;
      let sample: number;
      if (c < 0 || c > 1) sample = 0;
      else {
        const pos = Math.min(n - 1, Math.max(0, c * n - 0.5));
        const b0 = Math.floor(pos), f = pos - b0;
        const a0 = this.calibrated(row[b0]);
        const a1 = this.calibrated(row[Math.min(b0 + 1, n - 1)]);
        sample = f === 0 ? a0 : a0 * (1 - f) + a1 * f;
      }
      const adjusted = Math.min(80, Math.max(0, sample + refAdjust));
      const v = Math.floor(Math.sqrt(adjusted / 80) * 255);
      out[pi] = this.lut[v * 3];
      out[pi + 1] = this.lut[v * 3 + 1];
      out[pi + 2] = this.lut[v * 3 + 2];
    }
    ctx.putImageData(this.rowBuf, 0, 0);
  }

  destroy(): void {
    this.destroyed = true;
    this.rowBuf = null;
  }
}
