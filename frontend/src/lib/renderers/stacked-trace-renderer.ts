// Stacked-trace ("3D") panadapter renderer (MOR-4463): the recent spectrum
// history drawn as traces receding in depth, newest in front. It is fed the
// same frames and the same WaterfallOptions as the waterfall and keeps each
// row's own frequency placement, so history stays on its frequencies while
// the view retunes or zooms.

import { COLOR_SCHEMES, type ColorSchemeName, type WaterfallOptions } from './waterfall-renderer';
import {
  defaultStackedTraceSettings, projectStackedDepth, projectStackedRow, stackedColumnCount,
  stackedGlidePhase, stackedRowGeometry,
  type StackedDepth, type StackedRowGeometry, type StackedRowView, type StackedTraceSettings,
} from './stacked-trace-geometry';

export class StackedTraceRenderError extends Error {
  readonly code = 'webgl-required';
  constructor() { super('WebGL2 required for the stacked-trace view'); }
}

const BACKGROUND_VERTEX = `#version 300 es
void main() {
  vec2 p = vec2(float(gl_VertexID & 1), float(gl_VertexID >> 1));
  gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}`;

// The floor between the front and the last baseline carries ten frequency
// divisions, strongest at the front.
const BACKGROUND_FRAGMENT = `#version 300 es
precision highp float;
uniform vec2 uSize;
uniform vec2 uFloor;
out vec4 color;
void main() {
  vec3 base = vec3(0.0, 16.0, 32.0) / 255.0;
  float y = gl_FragCoord.y;
  float onFloor = step(uFloor.x, y) * step(y, uFloor.y);
  float t = clamp((y - uFloor.x) / max(uFloor.y - uFloor.x, 1.0), 0.0, 1.0);
  float cell = uSize.x / 10.0;
  float offset = abs(mod(gl_FragCoord.x + cell * 0.5, cell) - cell * 0.5);
  float grid = (1.0 - smoothstep(0.5, 1.5, offset)) * onFloor * mix(0.1, 0.02, t);
  color = vec4(base + vec3(0.03, 0.045, 0.065) * onFloor * (1.0 - t) + grid, 1.0);
}`;

// One triangle strip per row: two vertices per column, baseline and crest.
// A column narrower than a bin interpolates the two adjacent bins about the
// bin centres; a column spanning several bins takes their peak (up to 64
// bins), so a one-bin carrier keeps its height on a dense source.
const ROW_VERTEX = `#version 300 es
precision highp float;
precision highp int;
uniform highp sampler2D uRing;
uniform vec2 uMap;
uniform vec2 uCover;
uniform int uRow;
uniform vec3 uSamples;
uniform vec3 uPlace;
uniform vec4 uFrame;
uniform float uRef;
out float vLevel;
out float vY;
out float vCrest;
out float vSlope;
out float vCovered;
float amplitude(float bin) {
  float value = texelFetch(uRing, ivec2(int(bin) + int(uSamples.y), uRow), 0).r * 255.0;
  return uSamples.z == 80.0 ? value : min(value, uSamples.z) * 80.0 / uSamples.z;
}
float sampleAt(float coordinate) {
  float position = clamp(coordinate * uSamples.x - 0.5, 0.0, uSamples.x - 1.0);
  float bin = floor(position);
  return mix(amplitude(bin), amplitude(min(bin + 1.0, uSamples.x - 1.0)), position - bin);
}
float peakAt(float coordinate, float width) {
  if (uPlace.y <= 1.0) return sampleAt(coordinate);
  float first = floor((coordinate - 0.5 * abs(width)) * uSamples.x);
  float count = min(ceil(uPlace.y) + 1.0, 64.0);
  float peak = 0.0;
  for (int i = 0; i < 64; i++) {
    if (float(i) >= count) break;
    peak = max(peak, amplitude(clamp(first + float(i), 0.0, uSamples.x - 1.0)));
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
  vSlope = uPlace.y <= 1.0 ? (level(sampleAt(coordinate + width)) - vLevel) * uPlace.z / uFrame.y : 0.0;
  vCovered = x >= uCover.x && x < uCover.y && coordinate >= 0.0 && coordinate <= 1.0 ? 1.0 : 0.0;
  vY = top * (vCrest + uFrame.w * 0.5 + 1.0);
  gl_Position = vec4(x * 2.0 - 1.0, uPlace.x + vY * uFrame.z, 0.0, 1.0);
}`;

// The body is opaque, so a nearer row hides what lies behind it; it is
// coloured by the level each height stands for, and the crest line by the
// level of the signal itself — both through the waterfall palette. Weak
// crests are drawn dim, so the noise floor stays quiet under the signals.
const ROW_FRAGMENT = `#version 300 es
precision highp float;
uniform vec3 uPlace;
uniform vec4 uFrame;
uniform vec2 uShade;
uniform vec3 uColors[6];
uniform float uStops[6];
uniform int uColorCount;
in float vLevel;
in float vY;
in float vCrest;
in float vSlope;
in float vCovered;
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
  color = vec4(rgb, clamp(edge + 1.0, 0.0, 1.0));
}`;

const BACKGROUND_UNIFORMS = ['uSize', 'uFloor'] as const;
const ROW_UNIFORMS = ['uRing', 'uMap', 'uCover', 'uRow', 'uSamples', 'uPlace', 'uFrame', 'uRef', 'uShade',
  'uColors', 'uStops', 'uColorCount'] as const;
const MAX_GLIDE_MS = 1000;

export class StackedTraceRenderer {
  private readonly gl: WebGL2RenderingContext;
  private readonly options: WaterfallOptions;
  private readonly slots: number;
  private readonly rows: StackedRowGeometry[];
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
  private liveValid = false;
  private committedAt = 0;
  private glideMs: number;
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
    this.glideMs = settings.rowPeriodMs;
    this.rows = Array.from({ length: this.slots }, () => ({
      startHz: 0, spanHz: 1, coverStartHz: 0, coverEndHz: 0, samples: 0, sampleOffset: 0, amplitudeMax: 80,
    }));
    try {
      this.backgroundProgram = this.program(BACKGROUND_VERTEX, BACKGROUND_FRAGMENT, BACKGROUND_UNIFORMS, this.background);
      this.rowProgram = this.program(ROW_VERTEX, ROW_FRAGMENT, ROW_UNIFORMS, this.row);
      gl.useProgram(this.rowProgram);
      gl.uniform1i(this.row.get('uRing')!, 0);
      gl.blendFunc(gl.SRC_ALPHA, gl.ONE_MINUS_SRC_ALPHA);
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
    const vertex = this.shader(gl.VERTEX_SHADER, vertexSource);
    let fragment: WebGLShader | null = null;
    try {
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
    if (width > gl.getParameter(gl.MAX_TEXTURE_SIZE)) throw new StackedTraceRenderError();
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
   * Stores one delivered frame. Every frame replaces the live front trace;
   * a history row is committed once per `rowPeriodMs`, so the depth of the
   * stack is a time span that does not depend on the source frame rate.
   * `frame` carries the options the frame was delivered under.
   */
  pushRow(data: Uint8Array, frame: WaterfallOptions, now: number): void {
    if (this.destroyed) return;
    const live = (this.head + 1) % this.slots;
    if (!stackedRowGeometry(frame, data.length, this.rows[live])) return;
    if (data.length > this.textureWidth) this.growTexture(data.length);
    const gl = this.gl;
    gl.bindTexture(gl.TEXTURE_2D, this.texture);
    gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, live, data.length, 1, gl.RED, gl.UNSIGNED_BYTE, data, 0);
    this.liveValid = true;
    const elapsed = now - this.committedAt, period = this.settings.rowPeriodMs;
    if (this.count === 0 || elapsed >= period) {
      this.glideMs = this.count === 0 ? period : Math.min(elapsed, MAX_GLIDE_MS);
      this.head = live;
      this.count = Math.min(this.settings.rows, this.count + 1);
      this.committedAt = now;
      this.liveValid = false;
    }
  }

  private syncSize(): void {
    const { width, height } = this.canvas;
    if (width === this.width && height === this.height) return;
    this.width = width; this.height = height;
    this.columns = stackedColumnCount(width);
    const cssWidth = this.canvas.clientWidth;
    this.pixelScale = cssWidth > 0 ? width / cssWidth : 1;
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
    gl.uniform3f(this.row.get('uSamples')!, row.samples, row.sampleOffset, row.amplitudeMax);
    gl.uniform3f(this.row.get('uPlace')!, basePx / this.height * 2 - 1,
      Math.abs(view.scale) * row.samples / (this.columns - 1), s.traceHeight * placed.scale * this.height);
    gl.uniform2f(this.row.get('uShade')!, placed.fog, lineGain);
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, this.columns * 2);
  }

  /**
   * Paints the stack for `now`. Returns true while committed rows are still
   * gliding back, i.e. while the caller should paint the next display frame.
   */
  draw(now: number): boolean {
    if (this.destroyed) return false;
    this.syncSize();
    if (this.width <= 0 || this.height <= 0) return false;
    const gl = this.gl, s = this.settings;
    gl.disable(gl.BLEND);
    gl.useProgram(this.backgroundProgram);
    gl.uniform2f(this.background.get('uSize')!, this.width, this.height);
    const floorPx = s.frontInsetPx * this.pixelScale;
    gl.uniform2f(this.background.get('uFloor')!, floorPx, floorPx + s.floorRise * this.height);
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
    const phase = stackedGlidePhase(now, this.committedAt, this.glideMs);
    for (let k = this.count - 1; k >= 0; k--) {
      const depth = (k + phase) / s.rows;
      if (depth < 1) this.drawRow((this.head - k + this.slots) % this.slots, depth, false);
    }
    this.drawRow(this.liveValid ? (this.head + 1) % this.slots : this.head, 0, true);
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

  clear(): void { this.head = -1; this.count = 0; this.liveValid = false; }

  destroy(): void {
    if (this.destroyed) return;
    this.destroyed = true;
    const gl = this.gl;
    gl.deleteProgram(this.backgroundProgram); gl.deleteProgram(this.rowProgram); gl.deleteTexture(this.texture);
    this.backgroundProgram = this.rowProgram = this.texture = null;
  }
}
