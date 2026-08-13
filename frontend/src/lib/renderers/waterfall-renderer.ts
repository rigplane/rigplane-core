// Framework-agnostic waterfall renderer for radio scope data.
// Maintains a scrolling bitmap: each pushRow() adds a new line at the top
// and shifts existing content down — no full redraw needed.

interface ColorStop {
  stop: number;
  color: string;
}

export const COLOR_SCHEMES = {
  classic: [
    { stop: 0.0, color: '#001020' }, // dark blue
    { stop: 0.2, color: '#0040A0' }, // blue
    { stop: 0.4, color: '#00C0C0' }, // cyan
    { stop: 0.6, color: '#00FF00' }, // green
    { stop: 0.8, color: '#FFFF00' }, // yellow
    { stop: 1.0, color: '#FF0000' }, // red
  ],
  thermal: [
    { stop: 0.0, color: '#000000' },
    { stop: 0.3, color: '#800080' }, // purple
    { stop: 0.5, color: '#FF0000' }, // red
    { stop: 0.7, color: '#FF8000' }, // orange
    { stop: 1.0, color: '#FFFF00' }, // yellow
  ],
  grayscale: [
    { stop: 0.0, color: '#000000' },
    { stop: 1.0, color: '#FFFFFF' },
  ],
} satisfies Record<string, ColorStop[]>;

export type ColorSchemeName = keyof typeof COLOR_SCHEMES;

export interface WaterfallOptions {
  colorScheme: ColorSchemeName;
  refLevel: number;  // -30 to +30 dB brightness offset
  speed: number;     // rows scrolled per pushRow call (1 = normal)
  centerHz: number;  // center frequency in Hz
  spanHz: number;    // frequency span in Hz
}

export const defaultWaterfallOptions: WaterfallOptions = {
  colorScheme: 'classic',
  refLevel: 0,
  speed: 1,
  centerHz: 0,
  spanHz: 0,
};

function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace('#', '');
  return [
    parseInt(h.slice(0, 2), 16),
    parseInt(h.slice(2, 4), 16),
    parseInt(h.slice(4, 6), 16),
  ];
}

// Build a 256-entry RGB lookup table from gradient color stops.
function buildColorLut(scheme: ColorSchemeName): Uint8Array {
  const lut = new Uint8Array(256 * 3);
  const palette = COLOR_SCHEMES[scheme];
  const stops = palette.map(({ stop, color }) => ({ stop, rgb: hexToRgb(color) }));

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

    const i = v * 3;
    lut[i] = r;
    lut[i + 1] = g;
    lut[i + 2] = b;
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
  private rowData: Uint8ClampedArray | null = null;
  private destroyed = false;
  // Last confirmed (non-zero) spanHz we've rendered rows under. Used to
  // detect a genuine SPAN change (MOR-1479) vs. a same-value re-observation
  // or the initial 0→real transition (first frame / reconnect), neither of
  // which should clear the backlog. Kept separate from `options.spanHz` so
  // a transient 0 (disconnect / not-yet-observed) doesn't get treated as a
  // "real" span and doesn't erase the baseline used for comparison.
  private lastConfirmedSpanHz: number | null = null;

  constructor(canvas: HTMLCanvasElement, options: WaterfallOptions) {
    const ctx = canvas.getContext('2d');
    if (!ctx) throw new Error('Cannot get 2d context from waterfall canvas');
    this.ctx = ctx;
    this.options = { ...options };
    this.lut = buildColorLut(options.colorScheme);
    this.width = canvas.width;
    this.height = canvas.height;
    if (this.width > 0 && this.height > 0) {
      this._initBuffers();
      this.clear();
    }
  }

  private _initBuffers(): void {
    this.rowBuf = this.ctx.createImageData(this.width, 1);
    this.rowData = this.rowBuf.data;
  }

  /** Add a new scope data row at the top; shift existing content down. */
  pushRow(data: Uint8Array): void {
    if (this.destroyed) return;
    const ctx = this.ctx;
    if (!ctx) return;
    const canvas = ctx.canvas;
    const w = canvas.width;
    const h = canvas.height;
    const n = data.length;
    if (!n || w <= 0 || h <= 0) return;

    // Shift existing waterfall content down by 1 row (no full redraw)
    ctx.drawImage(canvas, 0, 0, w, h - 1, 0, 1, w, h - 1);

    // Reuse or allocate rowBuf if size changed
    if (!this.rowBuf || this.rowBuf.width !== w) {
      this.rowBuf = ctx.createImageData(w, 1);
      this.rowData = this.rowBuf.data;
    }
    const rowData = this.rowData!;

    // Build the new top row using the color LUT
    const lut = this.lut;
    // Ref level: maps -30..+30 dB → ±20 on 0-80 scale
    const refAdjust = (this.options.refLevel / 60) * 40;
    for (let x = 0; x < w; x++) {
      const p = data[Math.min(n - 1, Math.floor((x / w) * n))];
      // Gain boost: map 0-80 → 0-255 with sqrt curve for better contrast
      // at low signal levels (IC-7610 scope data peaks at ~55)
      const adjusted = Math.min(80, Math.max(0, p + refAdjust));
      const norm = adjusted / 80;
      const v = Math.floor(Math.sqrt(norm) * 255);
      const li = v * 3;
      const pi = x * 4;
      rowData[pi] = lut[li];
      rowData[pi + 1] = lut[li + 1];
      rowData[pi + 2] = lut[li + 2];
      rowData[pi + 3] = 255;
    }
    ctx.putImageData(this.rowBuf, 0, 0);
  }

  /** Resize the waterfall canvas, preserving existing content scaled to new dimensions. */
  resize(width: number, height: number): void {
    if (this.destroyed) return;
    // Capture current content before resize clears the canvas
    let oldCanvas: HTMLCanvasElement | null = null;
    const prevW = this.ctx.canvas.width;
    const prevH = this.ctx.canvas.height;
    if (prevW > 0 && prevH > 0) {
      oldCanvas = document.createElement('canvas');
      oldCanvas.width = prevW;
      oldCanvas.height = prevH;
      const offCtx = oldCanvas.getContext('2d');
      if (offCtx) {
        offCtx.drawImage(this.ctx.canvas, 0, 0);
      } else {
        oldCanvas = null;
      }
    }

    this.width = width;
    this.height = height;
    this.rowBuf = null;
    this.rowData = null;
    if (width > 0 && height > 0) {
      this.ctx.canvas.width = width;
      this.ctx.canvas.height = height;
      this._initBuffers();
      if (oldCanvas) {
        // Restore preserved content scaled to new dimensions
        this.ctx.drawImage(oldCanvas, 0, 0, width, height);
      } else {
        this.clear();
      }
    }
  }

  /** Update rendering options (e.g. colorScheme, refLevel, centerHz, spanHz). */
  updateOptions(opts: Partial<WaterfallOptions>): void {
    this.options = { ...this.options, ...opts };
    if (opts.colorScheme !== undefined) {
      this.lut = buildColorLut(this.options.colorScheme);
      // NOTE: Existing canvas pixels retain the old color mapping. A proper fix
      // requires a ring buffer of raw amplitude rows to re-render with the new LUT.
      // For now, new rows will use the new scheme and old rows will fade out naturally.
    }
    // MOR-1479: SPAN change remaps every row's pixel→frequency (pixelToFreq
    // reads options.spanHz directly), so rows drawn under the old span would
    // bend/jump at the seam once a new span applies. Owner ruling: clear the
    // backlog on a genuine SPAN change so every visible row shares the
    // current mapping. `opts.spanHz` here is the CONFIRMED value the caller
    // (SpectrumPanel) derives from the actual scope frame's startFreq/
    // endFreq — the same value pixelToFreq uses — not a pending/optimistic
    // one, so this can't double-clear on armed-then-confirmed span changes.
    //
    // A momentary 0 (no data yet / disconnected) is not a "real" span and is
    // ignored on both sides of the comparison: it neither triggers a clear
    // nor overwrites the last confirmed span, so the first real observation
    // and a reconnect-to-the-same-span both stay quiet.
    if (opts.spanHz !== undefined && opts.spanHz > 0) {
      if (this.lastConfirmedSpanHz !== null && this.lastConfirmedSpanHz !== opts.spanHz) {
        this.clear();
      }
      this.lastConfirmedSpanHz = opts.spanHz;
    }
  }

  /** Map a canvas x-pixel to the corresponding frequency in Hz. */
  pixelToFreq(x: number): number {
    const { centerHz, spanHz } = this.options;
    if (spanHz <= 0 || this.width <= 0) return centerHz;
    return centerHz - spanHz / 2 + (x / this.width) * spanHz;
  }

  /** Fill the canvas with the background color. */
  clear(): void {
    if (this.destroyed || this.width <= 0 || this.height <= 0) return;
    this.ctx.fillStyle = '#001020';
    this.ctx.fillRect(0, 0, this.width, this.height);
  }

  destroy(): void {
    this.destroyed = true;
    this.rowBuf = null;
    this.rowData = null;
    this.ctx = null as unknown as CanvasRenderingContext2D;
  }
}
