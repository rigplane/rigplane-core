// Stacked-trace ("3D") panadapter renderer (MOR-4463): the recent spectrum
// history drawn as traces receding in depth, newest in front. It is fed the
// same frames and the same WaterfallOptions as the waterfall and keeps each
// row's own frequency placement, so history stays on its frequencies while
// the view retunes or zooms.

import { COLOR_SCHEMES, type ColorSchemeName, type WaterfallOptions } from './waterfall-renderer';
import {
  defaultStackedTraceSettings, projectStackedDepth, projectStackedRow, stackedColumnCount,
  stackedGlidePhase, stackedRowGeometry, unprojectStackedFloor,
  type StackedDepth, type StackedFloorPoint, type StackedRowGeometry, type StackedRowView,
  type StackedTraceMarkers, type StackedTraceSettings,
} from './stacked-trace-geometry';

/** Colours of the tuning marker and the passband, RGBA 0..1. */
export interface StackedTraceMarkerColors {
  tune: Float32Array;
  bandFill: Float32Array;
  bandEdge: Float32Array;
}

export class StackedTraceRenderError extends Error {
  readonly code = 'webgl-required';
  constructor() { super('WebGL2 required for the stacked-trace view'); }
}

const BACKGROUND_VERTEX = `#version 300 es
void main() {
  vec2 p = vec2(float(gl_VertexID & 1), float(gl_VertexID >> 1));
  gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}`;

// What both shaders paint over a point of the scene that stands for view
// fraction `f`: the passband, its edges and the tuning marker. `pixel` is
// the width of one view fraction in backing pixels at that depth.
const MARKERS = `
uniform vec3 uMark;
uniform vec4 uTuneColor;
uniform vec4 uBandFill;
uniform vec4 uBandEdge;
vec3 marked(vec3 rgb, float f, float pixel, float line) {
  if (uMark.z > uMark.y) {
    float inside = step(uMark.y, f) * step(f, uMark.z);
    rgb = mix(rgb, uBandFill.rgb, uBandFill.a * inside);
    float edge = min(abs(f - uMark.y), abs(f - uMark.z)) * pixel;
    rgb = mix(rgb, uBandEdge.rgb, uBandEdge.a * (1.0 - smoothstep(line * 0.5, line * 0.5 + 1.0, edge)));
  }
  if (uMark.x >= 0.0) {
    float tune = abs(f - uMark.x) * pixel;
    rgb = mix(rgb, uTuneColor.rgb, uTuneColor.a * (1.0 - smoothstep(line * 0.5, line * 0.5 + 1.0, tune)));
  }
  return rgb;
}`;

// The floor is the plane of the row baselines, drawn in the same perspective
// as the rows: ten frequency divisions converging on the vanishing point,
// the markers, and a hatch where the current view holds no samples. Below
// the front baseline the label strip continues the front row.
const BACKGROUND_FRAGMENT = `#version 300 es
precision highp float;
uniform vec2 uSize;
uniform vec3 uFloor;
uniform vec2 uHatch;
uniform float uLine;
${MARKERS}
out vec4 color;
void main() {
  vec3 base = vec3(0.0, 16.0, 32.0) / 255.0;
  float t = (gl_FragCoord.y - uFloor.x) / uFloor.y;
  float z = t <= 0.0 ? 0.0 : t / max(1.0 - uFloor.z * t, 1e-6);
  float scale = 1.0 / (1.0 + uFloor.z * z);
  float f = 0.5 + (gl_FragCoord.x / uSize.x - 0.5) / scale;
  if (z > 1.0 || t * (1.0 + uFloor.z) >= 1.0 || f < 0.0 || f > 1.0) { color = vec4(base, 1.0); return; }
  float pixel = uSize.x * scale;
  vec3 rgb = base;
  if (t > 0.0) {
    float cell = abs(f * 10.0 - floor(f * 10.0 + 0.5)) * pixel / 10.0;
    rgb += vec3(0.03, 0.045, 0.065) * (1.0 - z) + (1.0 - smoothstep(0.5, 1.5, cell)) * mix(0.1, 0.02, z);
    if (f < uHatch.x || f >= uHatch.y) {
      float stripe = abs(fract(f * uSize.x / 12.0 + z * 24.0) - 0.5);
      rgb = mix(rgb, vec3(20.0, 30.0, 40.0) / 255.0, 0.65) + step(stripe, 0.08) * 0.09;
    }
  }
  color = vec4(marked(rgb, f, pixel, uLine), 1.0);
}`;

// One triangle strip per row: two vertices per column, baseline and crest.
// A column narrower than a bin interpolates the two adjacent bins about the
// bin centres. A wider column takes the peak of every bin it spans: each ring
// row stores the samples followed by their pairwise-maximum levels, and the
// column reads the first level at which it spans at most eight entries.
const ROW_VERTEX = `#version 300 es
precision highp float;
precision highp int;
uniform highp sampler2D uRing;
uniform vec2 uMap;
uniform vec2 uCover;
uniform int uRow;
uniform vec2 uSamples;
uniform vec4 uPlace;
uniform vec4 uFrame;
uniform float uRef;
out float vLevel;
out float vX;
out float vY;
out float vCrest;
out float vSlope;
out float vCovered;
float amplitude(float index) {
  float value = texelFetch(uRing, ivec2(int(index), uRow), 0).r * 255.0;
  return uSamples.y == 80.0 ? value : min(value, uSamples.y) * 80.0 / uSamples.y;
}
float sampleAt(float coordinate) {
  float position = clamp(coordinate * uSamples.x - 0.5, 0.0, uSamples.x - 1.0);
  float bin = floor(position);
  return mix(amplitude(bin), amplitude(min(bin + 1.0, uSamples.x - 1.0)), position - bin);
}
float peakAt(float coordinate, float width) {
  if (uPlace.y <= 1.0) return sampleAt(coordinate);
  float low = (coordinate - 0.5 * abs(width)) * uSamples.x;
  float high = (coordinate + 0.5 * abs(width)) * uSamples.x;
  float start = 0.0, count = uSamples.x, scale = 1.0;
  for (int i = 0; i < 24; i++) {
    if ((high - low) / scale <= 8.0 || count <= 1.0) break;
    start += count; count = ceil(count / 2.0); scale *= 2.0;
  }
  float first = clamp(floor(low / scale), 0.0, count - 1.0);
  float last = clamp(floor(high / scale), 0.0, count - 1.0);
  float peak = 0.0;
  for (int i = 0; i < 10; i++) {
    float bin = first + float(i);
    if (bin > last) break;
    peak = max(peak, amplitude(start + bin));
  }
  return peak;
}
// Height is linear in the calibrated amplitude, so signals stand clear of
// the noise floor; colour takes the square root, the waterfall's own curve.
float level(float amplitude80) {
  return clamp((amplitude80 + uRef * (40.0 / 60.0)) / 80.0, 0.0, 1.0);
}
void main() {
  float column = float(gl_VertexID >> 1), top = float(gl_VertexID & 1);
  float x = column / (uFrame.x - 1.0);
  float coordinate = uMap.y + uMap.x * x;
  float width = uMap.x / (uFrame.x - 1.0);
  vLevel = level(peakAt(coordinate, width));
  vCrest = vLevel * uPlace.z;
  vSlope = uPlace.y <= 1.0 ? (level(sampleAt(coordinate + width)) - vLevel) * uPlace.z / (uFrame.y * uPlace.w) : 0.0;
  vCovered = x >= uCover.x && x < uCover.y && coordinate >= 0.0 && coordinate <= 1.0 ? 1.0 : 0.0;
  vY = top * (vCrest + uFrame.w * 0.5 + 1.0);
  vX = x;
  gl_Position = vec4((x * 2.0 - 1.0) * uPlace.w, uPlace.x + vY * uFrame.z, 0.0, 1.0);
}`;

// The body is opaque, so a nearer row hides what lies behind it; it is
// coloured by the level each height stands for, and the crest line by the
// level of the signal itself — both through the waterfall palette. Weak
// crests are drawn dim, so the noise floor stays quiet under the signals.
// Every row carries the markers at its own depth, so they run over the
// surface of the stack and stay on their frequency on every row.
const ROW_FRAGMENT = `#version 300 es
precision highp float;
uniform vec4 uPlace;
uniform vec4 uFrame;
uniform vec2 uShade;
${MARKERS}
uniform vec3 uColors[6];
uniform float uStops[6];
uniform int uColorCount;
in float vLevel;
in float vY;
in float vCrest;
in float vSlope;
in float vCovered;
in float vX;
out vec4 color;
vec3 palette(float t) {
  vec3 rgb = uColors[0];
  for (int i = 1; i < 6; i++) {
    if (i >= uColorCount) break;
    if (t >= uStops[i - 1]) rgb = mix(uColors[i - 1], uColors[i], clamp((t - uStops[i - 1]) / (uStops[i] - uStops[i - 1]), 0.0, 1.0));
  }
  return rgb / 255.0;
}
void main() {
  if (vCovered < 0.5) discard;
  float edge = vCrest - vY + uFrame.w * 0.5;
  float lineWidth = uFrame.w * min(sqrt(1.0 + vSlope * vSlope), 6.0);
  float line = (1.0 - smoothstep(lineWidth - 1.0, lineWidth, edge)) * uShade.y;
  float local = clamp(vY / max(uPlace.z, 1.0), 0.0, 1.0);
  vec3 body = palette(sqrt(local)) * mix(0.3, 0.75, local);
  float strength = sqrt(vLevel);
  vec3 crest = mix(palette(strength) * mix(0.4, 1.0, strength), vec3(1.0), 0.3 * vLevel);
  vec3 rgb = mix(mix(body, crest, line), vec3(0.0, 16.0, 32.0) / 255.0, uShade.x);
  rgb = marked(rgb, vX, uFrame.y * (uFrame.x - 1.0) * uPlace.w, uFrame.w);
  color = vec4(rgb, clamp(edge + 1.0, 0.0, 1.0));
}`;

const MARKER_UNIFORMS = ['uMark', 'uTuneColor', 'uBandFill', 'uBandEdge'] as const;
const BACKGROUND_UNIFORMS = ['uSize', 'uFloor', 'uHatch', 'uLine', ...MARKER_UNIFORMS] as const;
const ROW_UNIFORMS = ['uRing', 'uMap', 'uCover', 'uRow', 'uSamples', 'uPlace', 'uFrame', 'uRef', 'uShade',
  'uColors', 'uStops', 'uColorCount', ...MARKER_UNIFORMS] as const;
/** Longest gap between two frames that still counts as stream time. */
const MAX_GAP_MS = 1000;

export class StackedTraceRenderer {
  private readonly gl: WebGL2RenderingContext;
  private readonly options: WaterfallOptions;
  private readonly slots: number;
  private readonly rows: StackedRowGeometry[];
  /** Stream time at which each ring slot was committed. */
  private readonly times: Float64Array;
  private levels = new Uint8Array(0);
  /** Per-bin maximum of the frames since the last committed row, and their placement. */
  private peak = new Uint8Array(0);
  private readonly peakRow: StackedRowGeometry = {
    startHz: 0, spanHz: 1, coverStartHz: 0, coverEndHz: 0, samples: 0, sampleOffset: 0, amplitudeMax: 80,
  };
  private peakValid = false;
  /** The samples of every ring slot, `stride` apart: what a click is tested against. */
  private held = new Uint8Array(0);
  private stride = 0;
  private readonly markers: StackedTraceMarkers = { tune: -1, bandLo: 0, bandHi: 0, coverLo: 0, coverHi: 1 };
  private markerColors: StackedTraceMarkerColors | null = null;
  private readonly floorPoint: StackedFloorPoint = { depth: 0, fraction: 0 };
  private lastDrawAt = 0;
  private maxTextureWidth = 0;
  private readonly rowView: StackedRowView = { scale: 1, offset: 0, coverLo: 0, coverHi: 1 };
  private readonly depth: StackedDepth = { baseline: 0, scale: 1, fog: 0 };
  private readonly colors = new Float32Array(18);
  private readonly stops = new Float32Array(6);
  private readonly background = new Map<string, WebGLUniformLocation | null>();
  private readonly row = new Map<string, WebGLUniformLocation | null>();
  private backgroundProgram: WebGLProgram | null = null;
  private rowProgram: WebGLProgram | null = null;
  private texture: WebGLTexture | null = null;
  private textureWidth = 0;
  private palette: ColorSchemeName | null = null;
  private width = 0;
  private height = 0;
  private pixelScale = 1;
  private columns = 64;
  private head = -1;
  private count = 0;
  // Stream time runs only while frames arrive (gaps count up to MAX_GAP_MS),
  // so a paused source keeps its picture instead of ageing out.
  private streamTime = 0;
  private lastPushAt = 0;
  private lastInterval: number;
  private nextCommitAt = 0;
  private pushed = false;
  private viewCenter: number;
  private viewSpan: number;
  private destroyed = false;

  constructor(
    private readonly canvas: HTMLCanvasElement, options: WaterfallOptions,
    private readonly settings: StackedTraceSettings = defaultStackedTraceSettings,
  ) {
    const gl = canvas.getContext('webgl2', { alpha: false, antialias: true, depth: false, stencil: false });
    if (!gl) throw new StackedTraceRenderError();
    this.gl = gl;
    this.options = { ...options };
    this.viewCenter = options.centerHz;
    this.viewSpan = options.spanHz > 0 ? options.spanHz : 1;
    this.slots = settings.rows + 1;
    this.lastInterval = settings.rowPeriodMs;
    this.times = new Float64Array(this.slots);
    this.rows = Array.from({ length: this.slots }, () => ({
      startHz: 0, spanHz: 1, coverStartHz: 0, coverEndHz: 0, samples: 0, sampleOffset: 0, amplitudeMax: 80,
    }));
    try {
      this.backgroundProgram = this.program(BACKGROUND_VERTEX, BACKGROUND_FRAGMENT, BACKGROUND_UNIFORMS, this.background);
      this.rowProgram = this.program(ROW_VERTEX, ROW_FRAGMENT, ROW_UNIFORMS, this.row);
      gl.useProgram(this.rowProgram);
      gl.uniform1i(this.row.get('uRing')!, 0);
      gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
      this.maxTextureWidth = gl.getParameter(gl.MAX_TEXTURE_SIZE);
      this.texture = this.createTexture(1);
      this.textureWidth = 1;
    } catch (error) { this.destroy(); throw error; }
  }

  /** History rows currently held behind the live trace. */
  get historyRows(): number { return this.count; }

  private shader(type: number, source: string): WebGLShader {
    const gl = this.gl, shader = gl.createShader(type);
    if (!shader) throw new StackedTraceRenderError();
    gl.shaderSource(shader, source); gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      gl.deleteShader(shader); throw new StackedTraceRenderError();
    }
    return shader;
  }

  private program(vertexSource: string, fragmentSource: string, names: readonly string[],
    locations: Map<string, WebGLUniformLocation | null>): WebGLProgram {
    const gl = this.gl, program = gl.createProgram();
    if (!program) throw new StackedTraceRenderError();
    let vertex: WebGLShader | null = null, fragment: WebGLShader | null = null;
    try {
      vertex = this.shader(gl.VERTEX_SHADER, vertexSource);
      fragment = this.shader(gl.FRAGMENT_SHADER, fragmentSource);
      gl.attachShader(program, vertex); gl.attachShader(program, fragment); gl.linkProgram(program);
      if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new StackedTraceRenderError();
    } catch (error) { gl.deleteProgram(program); throw error; }
    finally { gl.deleteShader(vertex); gl.deleteShader(fragment); }
    for (const name of names) locations.set(name, gl.getUniformLocation(program, name));
    return program;
  }

  private createTexture(width: number): WebGLTexture {
    const gl = this.gl;
    if (width > this.maxTextureWidth) throw new StackedTraceRenderError();
    const texture = gl.createTexture();
    if (!texture) throw new StackedTraceRenderError();
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.R8, width, this.slots, 0, gl.RED, gl.UNSIGNED_BYTE, null);
    return texture;
  }

  /** A wider frame widens the ring and keeps the rows already stored. */
  private growTexture(width: number): void {
    const gl = this.gl, previous = this.texture, previousWidth = this.textureWidth;
    const texture = this.createTexture(width);
    const framebuffer = gl.createFramebuffer();
    try {
      if (!framebuffer) throw new StackedTraceRenderError();
      gl.bindFramebuffer(gl.FRAMEBUFFER, framebuffer);
      gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, previous, 0);
      if (gl.checkFramebufferStatus(gl.FRAMEBUFFER) !== gl.FRAMEBUFFER_COMPLETE) throw new StackedTraceRenderError();
      gl.copyTexSubImage2D(gl.TEXTURE_2D, 0, 0, 0, 0, 0, previousWidth, this.slots);
    } catch (error) { gl.deleteTexture(texture); gl.bindTexture(gl.TEXTURE_2D, previous); throw error; }
    finally { gl.bindFramebuffer(gl.FRAMEBUFFER, null); gl.deleteFramebuffer(framebuffer); }
    gl.deleteTexture(previous);
    this.texture = texture; this.textureWidth = width;
  }

  /**
   * Writes the row's samples (`data` from `offset`) and their
   * pairwise-maximum levels into `levels` and returns the length used. A
   * frame too dense for the device's texture width is first reduced by
   * pairwise maxima, and `row.samples` becomes the reduced count.
   */
  private buildLevels(data: Uint8Array, offset: number, row: StackedRowGeometry): number {
    const total = (count: number): number => {
      let sum = count;
      while (count > 1) { count = (count + 1) >> 1; sum += count; }
      return sum;
    };
    let samples = row.samples, reductions = 0;
    while (total(samples) > this.maxTextureWidth && samples > 1) { samples = (samples + 1) >> 1; reductions++; }
    const need = total(samples) + (reductions > 0 ? row.samples : 0);
    if (this.levels.length < need) this.levels = new Uint8Array(need);
    const levels = this.levels;
    // Reduce in the tail of the buffer, then lay the kept level at index 0.
    let length = row.samples, from = need - row.samples;
    if (reductions === 0) from = 0;
    for (let i = 0; i < length; i++) levels[from + i] = data[offset + i];
    for (let r = 0; r < reductions; r++) {
      const next = (length + 1) >> 1;
      for (let i = 0; i < next; i++) {
        const a = levels[from + 2 * i], b = 2 * i + 1 < length ? levels[from + 2 * i + 1] : 0;
        levels[from + i] = a > b ? a : b;
      }
      length = next;
    }
    if (from !== 0) for (let i = 0; i < length; i++) levels[i] = levels[from + i];
    row.samples = length;
    let source = 0, target = length;
    while (length > 1) {
      const next = (length + 1) >> 1;
      for (let i = 0; i < next; i++) {
        const a = levels[source + 2 * i], b = 2 * i + 1 < length ? levels[source + 2 * i + 1] : 0;
        levels[target + i] = a > b ? a : b;
      }
      source = target; target += next; length = next;
    }
    return target;
  }

  private upload(slot: number, data: Uint8Array, offset: number): void {
    const row = this.rows[slot], length = this.buildLevels(data, offset, row);
    if (length > this.textureWidth) this.growTexture(length);
    if (row.samples > this.stride) {
      const held = new Uint8Array(this.slots * row.samples);
      for (let i = 0; i < this.slots; i++) held.set(this.held.subarray(i * this.stride, (i + 1) * this.stride), i * row.samples);
      this.held = held; this.stride = row.samples;
    }
    for (let i = 0, at = slot * this.stride; i < row.samples; i++) this.held[at + i] = this.levels[i];
    const gl = this.gl;
    gl.bindTexture(gl.TEXTURE_2D, this.texture);
    gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, slot, length, 1, gl.RED, gl.UNSIGNED_BYTE, this.levels, 0);
  }

  /**
   * Folds a frame into the per-bin maximum since the last committed row.
   * A frame with another placement or sample count starts the maximum anew.
   */
  private accumulate(data: Uint8Array, row: StackedRowGeometry): void {
    const held = this.peakRow, samples = row.samples, offset = row.sampleOffset;
    if (this.peak.length < samples) { this.peak = new Uint8Array(samples); this.peakValid = false; }
    const peak = this.peak;
    if (this.peakValid && held.samples === samples && held.startHz === row.startHz && held.spanHz === row.spanHz
      && held.coverStartHz === row.coverStartHz && held.coverEndHz === row.coverEndHz
      && held.amplitudeMax === row.amplitudeMax) {
      for (let i = 0; i < samples; i++) { const value = data[offset + i]; if (value > peak[i]) peak[i] = value; }
      return;
    }
    for (let i = 0; i < samples; i++) peak[i] = data[offset + i];
    held.startHz = row.startHz; held.spanHz = row.spanHz; held.coverStartHz = row.coverStartHz;
    held.coverEndHz = row.coverEndHz; held.samples = samples; held.amplitudeMax = row.amplitudeMax;
    this.peakValid = true;
  }

  /**
   * Stores one delivered frame. Every frame becomes the live front trace.
   * A history row is committed on a `rowPeriodMs` grid of stream time and
   * holds the per-bin maximum of the frames since the previous row, so a
   * signal shorter than a row period still enters history. Each row keeps
   * its commit time: depth is elapsed time, and the stack spans
   * `rows * rowPeriodMs` whatever the source frame rate.
   * `frame` carries the options the frame was delivered under.
   */
  pushRow(data: Uint8Array, frame: WaterfallOptions, now: number): void {
    if (this.destroyed) return;
    let live = (this.head + 1) % this.slots;
    const row = this.rows[live];
    if (!stackedRowGeometry(frame, data.length, row)) return;
    this.accumulate(data, row);
    if (this.pushed) {
      const gap = Math.min(Math.max(now - this.lastPushAt, 0), MAX_GAP_MS);
      this.streamTime += gap;
      this.lastInterval = Math.max(gap, 1);
    }
    this.pushed = true;
    this.lastPushAt = now;
    const period = this.settings.rowPeriodMs;
    if (this.count === 0 || this.streamTime >= this.nextCommitAt) {
      const held = this.peakRow;
      row.startHz = held.startHz; row.spanHz = held.spanHz; row.coverStartHz = held.coverStartHz;
      row.coverEndHz = held.coverEndHz; row.samples = held.samples; row.amplitudeMax = held.amplitudeMax;
      row.sampleOffset = 0;
      this.upload(live, this.peak, 0);
      this.peakValid = false;
      this.head = live;
      this.count = Math.min(this.settings.rows, this.count + 1);
      this.times[live] = this.streamTime;
      this.nextCommitAt += period;
      if (this.nextCommitAt <= this.streamTime) this.nextCommitAt = this.streamTime + period;
      live = (this.head + 1) % this.slots;
      stackedRowGeometry(frame, data.length, this.rows[live]);
    }
    this.upload(live, data, this.rows[live].sampleOffset);
  }

  /** CSS-to-backing pixel ratio of the canvas; sizes the front inset and the line. */
  setPixelScale(scale: number): void {
    if (scale > 0) this.pixelScale = scale;
  }

  private syncSize(): void {
    const { width, height } = this.canvas;
    if (width === this.width && height === this.height) return;
    this.width = width; this.height = height;
    this.columns = stackedColumnCount(width);
    this.gl.viewport(0, 0, width, height);
  }

  private syncPalette(): void {
    const scheme = this.options.colorScheme;
    if (this.palette === scheme) return;
    this.palette = scheme;
    const palette = COLOR_SCHEMES[scheme];
    for (let i = 0; i < palette.length; i++) {
      this.stops[i] = palette[i].stop;
      const value = Number.parseInt(palette[i].color.slice(1), 16);
      this.colors[i * 3] = value >> 16;
      this.colors[i * 3 + 1] = (value >> 8) & 255;
      this.colors[i * 3 + 2] = value & 255;
    }
  }

  /** Where the tuning marker, the passband and the uncovered part of the view are drawn, and in which colours. */
  setMarkers(markers: StackedTraceMarkers, colors: StackedTraceMarkerColors): void {
    Object.assign(this.markers, markers);
    this.markerColors = colors;
  }

  private markerUniforms(locations: Map<string, WebGLUniformLocation | null>): void {
    const gl = this.gl, m = this.markers, colors = this.markerColors;
    gl.uniform3f(locations.get('uMark')!, colors ? m.tune : -1, m.bandLo, colors ? m.bandHi : m.bandLo);
    if (!colors) return;
    gl.uniform4fv(locations.get('uTuneColor')!, colors.tune);
    gl.uniform4fv(locations.get('uBandFill')!, colors.bandFill);
    gl.uniform4fv(locations.get('uBandEdge')!, colors.bandEdge);
  }

  /** Front baseline above the pane bottom, as a fraction of the pane height. */
  private frontInset(): number {
    return this.settings.frontInsetPx * this.pixelScale / this.height;
  }

  /** Crest of a stored row above its baseline at view fraction `fraction`, in front-row trace heights; -1 where the row has no samples. */
  private crestAt(slot: number, fraction: number): number {
    const row = this.rows[slot], view = this.rowView;
    projectStackedRow(row, this.viewCenter - this.viewSpan / 2, this.viewSpan, view);
    const coordinate = view.offset + view.scale * fraction;
    if (fraction < view.coverLo || fraction >= view.coverHi || coordinate < 0 || coordinate > 1) return -1;
    const half = Math.abs(view.scale) / (this.columns - 1) / 2, last = row.samples - 1;
    const from = Math.max(0, Math.min(last, Math.floor((coordinate - half) * row.samples)));
    const to = Math.max(0, Math.min(last, Math.floor((coordinate + half) * row.samples)));
    let peak = 0;
    for (let i = from, at = slot * this.stride; i <= to; i++) if (this.held[at + i] > peak) peak = this.held[at + i];
    const amplitude = Math.min(peak, row.amplitudeMax) * 80 / row.amplitudeMax;
    return Math.max(0, Math.min(1, (amplitude + this.options.refLevel * (40 / 60)) / 80));
  }

  /**
   * The view fraction (0..1) a pointer at pane point (`x`, `y`) refers to;
   * `x` and `y` are fractions of the pane, `y` downward from its top. It is
   * the frequency of what is drawn there: the nearest trace whose body
   * covers the point, at that trace's own depth. On the background it is
   * the floor point under the pointer, and above the last row that row.
   */
  locate(x: number, y: number): number {
    if (this.height <= 0) return x < 0 ? 0 : x > 1 ? 1 : x;
    const s = this.settings, placed = this.depth, up = 1 - y, inset = this.frontInset();
    const span = s.rows * s.rowPeriodMs, line = 1.25 * this.pixelScale / this.height;
    const clock = this.streamTime + stackedGlidePhase(this.lastDrawAt, this.lastPushAt, this.lastInterval) * this.lastInterval;
    for (let k = -1; k < this.count; k++) {
      const slot = (this.head - k + this.slots) % this.slots;
      const depth = k < 0 ? 0 : (clock - this.times[slot]) / span;
      if (depth >= 1 || this.count === 0) continue;
      projectStackedDepth(depth, s, placed);
      const fraction = 0.5 + (x - 0.5) / placed.scale, base = inset + placed.baseline;
      if (fraction < 0 || fraction > 1 || up < base) continue;
      const crest = this.crestAt(slot, fraction);
      if (crest >= 0 && up <= base + crest * s.traceHeight * placed.scale + line) return fraction;
    }
    unprojectStackedFloor(x, up - inset, s, this.floorPoint);
    const fraction = this.floorPoint.fraction;
    return fraction < 0 ? 0 : fraction > 1 ? 1 : fraction;
  }

  private drawRow(slot: number, depth: number, front: boolean): void {
    const gl = this.gl, row = this.rows[slot], view = this.rowView, placed = this.depth, s = this.settings;
    projectStackedRow(row, this.viewCenter - this.viewSpan / 2, this.viewSpan, view);
    projectStackedDepth(depth, s, placed);
    const basePx = s.frontInsetPx * this.pixelScale + placed.baseline * this.height;
    const linePx = 1.25 * this.pixelScale;
    // Rows closer together than a few line widths dim their crest lines so
    // the far end of the stack reads as a surface, not as a solid block.
    const spacingPx = s.floorRise * this.height * (1 + s.recede) * placed.scale * placed.scale / s.rows;
    const lineGain = front ? 1 : Math.max(0.25, Math.min(1, spacingPx / (2.5 * linePx)));
    gl.uniform2f(this.row.get('uMap')!, view.scale, view.offset);
    gl.uniform2f(this.row.get('uCover')!, view.coverLo, view.coverHi);
    gl.uniform1i(this.row.get('uRow')!, slot);
    gl.uniform2f(this.row.get('uSamples')!, row.samples, row.amplitudeMax);
    gl.uniform4f(this.row.get('uPlace')!, basePx / this.height * 2 - 1,
      Math.abs(view.scale) * row.samples / (this.columns - 1), s.traceHeight * placed.scale * this.height, placed.scale);
    gl.uniform2f(this.row.get('uShade')!, placed.fog, lineGain);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, this.columns * 2);
  }

  /**
   * Paints the stack for `now`. Between frames the rows keep gliding back
   * for one frame interval; returns true while they do, i.e. while the
   * caller should paint the next display frame.
   */
  draw(now: number): boolean {
    if (this.destroyed) return false;
    this.syncSize();
    if (this.width <= 0 || this.height <= 0) return false;
    this.lastDrawAt = now;
    const gl = this.gl, s = this.settings;
    gl.disable(gl.BLEND);
    gl.useProgram(this.backgroundProgram);
    gl.uniform2f(this.background.get('uSize')!, this.width, this.height);
    gl.uniform3f(this.background.get('uFloor')!, s.frontInsetPx * this.pixelScale,
      s.floorRise * (1 + s.recede) * this.height, s.recede);
    gl.uniform2f(this.background.get('uHatch')!, this.markers.coverLo, this.markers.coverHi);
    gl.uniform1f(this.background.get('uLine')!, 1.25 * this.pixelScale);
    this.markerUniforms(this.background);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
    if (this.count === 0) return false;
    this.syncPalette();
    gl.useProgram(this.rowProgram);
    gl.enable(gl.BLEND);
    gl.uniform4f(this.row.get('uFrame')!, this.columns, this.width / (this.columns - 1), 2 / this.height,
      1.25 * this.pixelScale);
    gl.uniform1f(this.row.get('uRef')!, this.options.refLevel);
    gl.uniform3fv(this.row.get('uColors')!, this.colors);
    gl.uniform1fv(this.row.get('uStops')!, this.stops);
    gl.uniform1i(this.row.get('uColorCount')!, COLOR_SCHEMES[this.options.colorScheme].length);
    this.markerUniforms(this.row);
    const phase = stackedGlidePhase(now, this.lastPushAt, this.lastInterval);
    const clock = this.streamTime + phase * this.lastInterval, span = s.rows * s.rowPeriodMs;
    for (let k = this.count - 1; k >= 0; k--) {
      const slot = (this.head - k + this.slots) % this.slots;
      const depth = (clock - this.times[slot]) / span;
      if (depth < 1) this.drawRow(slot, depth, false);
    }
    this.drawRow((this.head + 1) % this.slots, 0, true);
    return phase < 1;
  }

  /** Returns true when the change needs a repaint. */
  updateOptions(options: Partial<WaterfallOptions>): boolean {
    const o = this.options;
    let changed = (options.colorScheme !== undefined && options.colorScheme !== o.colorScheme)
      || (options.refLevel !== undefined && options.refLevel !== o.refLevel);
    Object.assign(o, options);
    if (o.spanHz > 0 && Number.isFinite(o.centerHz)) {
      if (this.viewCenter !== o.centerHz || this.viewSpan !== o.spanHz) changed = true;
      this.viewCenter = o.centerHz; this.viewSpan = o.spanHz;
    }
    return changed;
  }

  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    const gl = this.gl;
    gl.deleteProgram(this.backgroundProgram); gl.deleteProgram(this.rowProgram); gl.deleteTexture(this.texture);
    this.backgroundProgram = this.rowProgram = this.texture = null;
  }
}
