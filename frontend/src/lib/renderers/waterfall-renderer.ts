import type { View } from '../runtime/scope-view';

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
  coverage?: View;
  colorScheme: ColorSchemeName;
  refLevel: number;  // -30 to +30 dB brightness offset
  speed: number;     // rows scrolled per pushRow call (1 = normal)
  centerHz: number;  // displayed viewport center in Hz (MOR-2464: animated)
  sampleSpanHz?: number;
  sampleOffset?: number;
  sampleWindow?: Readonly<{ start: number; end: number }>;
  sampleAmplitudeMax?: number;
  spanHz: number;    // frequency span in Hz
  // Viewport center minus sample-window center, Hz (MOR-2464). Samples the
  // NEW row only; history is projected from its retained frequency coordinates.
  panoramaShiftHz: number;
  // MOR-4383: temporal per-bin IIR (alpha 0.5 per new frame) on the
  // waterfall rows. Default on; the spatial [1 2 1]/4 and the 3-row blended
  // groups are part of the single render path and always apply.
  temporalSmoothing?: boolean;
}

export const defaultWaterfallOptions: WaterfallOptions = {
  colorScheme: 'classic',
  refLevel: 0,
  speed: 1,
  centerHz: 0,
  spanHz: 0,
  panoramaShiftHz: 0,
  temporalSmoothing: true,
};

/** Frame identity for the per-frame dedupe (MOR-4383): one row group per NEW
 * frame. A repeated or stale identity is a registration replay, never a new
 * row; a new epoch restarts the smoothing state like a geometry change.
 * Field names match the ScopeFrame contract the delivery maps from. */
export interface WaterfallFrameIdentity {
  readonly seq: number;
  readonly epoch: number;
}

export class WaterfallRenderError extends Error {
  readonly code = 'webgl-required';
  constructor() { super('WebGL required for the waterfall'); }
}

const VERTEX = `#version 300 es
void main() {
  vec2 p = vec2(float(gl_VertexID & 1), float(gl_VertexID >> 1));
  gl_Position = vec4(p * 2.0 - 1.0, 0.0, 1.0);
}`;

function fragmentShader(height: number, blockRows: number): string {
  const blocks = Math.ceil(height / blockRows);
  let declarations = '', lookup = '';
  for (let i = 0; i < blocks; i++) {
    declarations += `layout(std140) uniform Rows${i} { vec4 rows${i}[${blockRows * 3}]; };\n`;
    lookup += `if (row < ${(i + 1) * blockRows}) return rows${i}[(row - ${i * blockRows}) * 3 + part];\n`;
  }
  return `#version 300 es
precision highp float;
precision highp int;
uniform highp sampler2D uRing;
uniform vec2 uView;
uniform vec2 uSize;
uniform int uHead, uCount, uRows;
uniform float uRef, uViewLow;
uniform vec3 uColors[6];
uniform float uStops[6];
uniform int uColorCount;
${declarations}
vec4 rowInfo(int row, int part) { ${lookup} return vec4(0.0); }
out vec4 color;
// MOR-4383: every screen column samples the bin-space position of its
// centre, coordinate * bins - 0.5, and linearly interpolates the adjacent
// bins (edge bins repeat); each bin is calibrated before the lerp. One
// interpolation rule for fractional and resting columns alike.
float profile(float column, vec4 grid, vec4 extra, int row) {
  float coordinate = grid.y + column / grid.x * grid.z;
  if (coordinate < 0.0 || coordinate > 1.0) return 0.0;
  float pos = clamp(coordinate * extra.z - 0.5, 0.0, extra.z - 1.0);
  float left = floor(pos + 0.0001);
  float fraction = clamp(pos - left, 0.0, 1.0);
  int right = int(left) + 1 < int(extra.z) ? int(left) + 1 : int(extra.z) - 1;
  float v0 = texelFetch(uRing, ivec2(int(left) + int(extra.w), row), 0).r * 255.0;
  float v1 = texelFetch(uRing, ivec2(right + int(extra.w), row), 0).r * 255.0;
  float q0 = extra.y == 80.0 ? v0 : floor(min(v0, extra.y) * 80.0 / extra.y + 0.5);
  float q1 = extra.y == 80.0 ? v1 : floor(min(v1, extra.y) * 80.0 / extra.y + 0.5);
  return mix(q0, q1, fraction);
}
void main() {
  int age = int(floor(uSize.y - gl_FragCoord.y));
  if (age >= uCount) { color = vec4(0.0, 16.0/255.0, 32.0/255.0, 1.0); return; }
  int row = (uHead - age + uRows) % uRows;
  vec4 info = rowInfo(row, 0), extra = rowInfo(row, 1);
  float delta = (uView.x - info.x) + (uViewLow - info.y);
  float localHz = delta + floor(gl_FragCoord.x) / uSize.x * uView.y;
  float coordinate = localHz / info.z;
  // MOR-4383 B1: no hatch pattern anywhere in the waterfall. Columns this
  // row does not cover (older rows after a span widen, band edges) fill
  // with the empty-history background; the row keeps its truthful coverage
  // in the metadata. Matches the RigDeck presenter (uncovered columns
  // carry amplitude 0) and the 2D reference model (background fill).
  if (coordinate < 0.0 || coordinate >= 1.0 || localHz < info.w || localHz >= extra.x) {
    color = vec4(0.0, 16.0/255.0, 32.0/255.0, 1.0);
    return;
  }
  vec4 grid = rowInfo(row, 2);
  // The bin-space rule samples the pixel CENTRE (RigDeck: (2x+1)*n/(2W)-0.5).
  float spanScale = uView.y == info.z ? 1.0 : uView.y / info.z;
  float pixelScale = grid.x == uSize.x ? 1.0 : grid.x / uSize.x;
  float column = (delta / info.z - grid.y) / grid.z * grid.x
    + (floor(gl_FragCoord.x) + 0.5) * (spanScale / grid.z) * pixelScale;
  float sampleValue = profile(column, grid, extra, row);
  float t = floor(sqrt(clamp((sampleValue + uRef * (40.0/60.0)) / 80.0, 0.0, 1.0)) * 255.0) / 255.0;
  vec3 rgb = uColors[0];
  for (int i = 1; i < 6; i++) {
    if (i >= uColorCount) break;
    if (t >= uStops[i-1]) rgb = mix(uColors[i-1], uColors[i], clamp((t-uStops[i-1])/(uStops[i]-uStops[i-1]), 0.0, 1.0));
  }
  color = vec4(floor(rgb + 0.5) / 255.0, 1.0);
}`;
}

export class WaterfallRenderer {
  private readonly gl: WebGL2RenderingContext;
  private readonly options: WaterfallOptions;
  private program: WebGLProgram | null = null;
  private texture: WebGLTexture | null = null;
  private readonly blocks: WebGLBuffer[] = [];
  private readonly uniforms = new Map<string, WebGLUniformLocation | null>();
  private readonly row = new Float32Array(12);
  private metadata = new Float32Array(0);
  private readonly colors = new Float32Array(18);
  private readonly stops = new Float32Array(6);
  private palette: ColorSchemeName | null = null;
  private blockRows = 0;
  private width = 0;
  private height = 0;
  private rows = 0;
  private textureWidth = 0;
  private head = -1;
  private count = 0;
  private readonly originHz: number;
  private viewCenter: number;
  private viewSpan: number;
  private viewDirty = true;
  private sizeDirty = true;
  private appearanceDirty = true;
  private sentHead = -2;
  private sentCount = -1;
  private destroyed = false;
  // MOR-4383 smoothing state, ported from the RigDeck presenter: temporal
  // per-bin IIR, spatial [1 2 1]/4, and one blended 3-row group per new
  // frame. Reset by geometry change (bins, span, window, sample centre)
  // or clear().
  private smoothState = new Float32Array(0);
  private filtered = new Float32Array(0);
  private currentRow = new Uint8Array(0);
  private previousRow = new Uint8Array(0);
  private rowUpload = new Uint8Array(0);
  private smoothBins = 0;
  private smoothSpan = 0;
  private smoothStart = 0;
  private smoothRange = 1;
  private smoothCentre = 0;
  private previousValid = false;
  // MOR-4383 frame dedupe: the last delivered identity (mirrors the 2D
  // reference's frameEpoch/lastSeq, both starting at zero = none seen).
  private frameEpoch = 0;
  private lastSeq = 0;

  constructor(private readonly canvas: HTMLCanvasElement, options: WaterfallOptions) {
    const gl = canvas.getContext('webgl2', { alpha: false, antialias: false, depth: false, stencil: false });
    if (!gl) throw new WaterfallRenderError();
    this.gl = gl;
    this.options = { ...options };
    this.originHz = options.centerHz;
    this.viewCenter = options.centerHz;
    this.viewSpan = options.spanHz > 0 ? options.spanHz : 1;
    try { this.resize(canvas.width, canvas.height); }
    catch (error) { this.destroy(); throw error; }
  }

  private shader(type: number, source: string): WebGLShader {
    const gl = this.gl, shader = gl.createShader(type);
    if (!shader) throw new WaterfallRenderError();
    gl.shaderSource(shader, source); gl.compileShader(shader);
    if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
      gl.deleteShader(shader); throw new WaterfallRenderError();
    }
    return shader;
  }

  private releaseProgram(): void {
    const gl = this.gl;
    gl.deleteProgram(this.program); this.program = null;
    for (const buffer of this.blocks) gl.deleteBuffer(buffer);
    this.blocks.length = 0; this.uniforms.clear();
  }

  private configureRows(): void {
    const gl = this.gl;
    if (this.program && this.blocks.length === Math.ceil(this.rows / this.blockRows)) {
      this.uploadMetadata(); return;
    }
    this.releaseProgram();
    const program = gl.createProgram();
    if (!program) throw new WaterfallRenderError();
    this.program = program;
    const vertex = this.shader(gl.VERTEX_SHADER, VERTEX);
    let fragment: WebGLShader | null = null;
    try {
      fragment = this.shader(gl.FRAGMENT_SHADER, fragmentShader(this.rows, this.blockRows));
      gl.attachShader(program, vertex); gl.attachShader(program, fragment); gl.linkProgram(program);
      if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new WaterfallRenderError();
    } finally { gl.deleteShader(vertex); gl.deleteShader(fragment); }
    gl.useProgram(program);
    for (const name of ['uRing', 'uView', 'uViewLow', 'uSize', 'uHead', 'uCount', 'uRows',
      'uRef', 'uColors', 'uStops', 'uColorCount']) {
      this.uniforms.set(name, gl.getUniformLocation(program, name));
    }
    gl.uniform1i(this.uniforms.get('uRing')!, 0);
    this.viewDirty = this.sizeDirty = this.appearanceDirty = true;
    this.sentHead = -2; this.sentCount = -1;
    for (let i = 0; i < Math.ceil(this.rows / this.blockRows); i++) {
      const buffer = gl.createBuffer();
      if (!buffer) throw new WaterfallRenderError();
      this.blocks.push(buffer);
      gl.bindBuffer(gl.UNIFORM_BUFFER, buffer);
      gl.bufferData(gl.UNIFORM_BUFFER, this.blockRows * 48, gl.DYNAMIC_DRAW);
      gl.bindBufferBase(gl.UNIFORM_BUFFER, i, buffer);
      gl.uniformBlockBinding(program, gl.getUniformBlockIndex(program, `Rows${i}`), i);
    }
    this.uploadMetadata();
  }

  private uploadMetadata(): void {
    const gl = this.gl;
    for (let i = 0; i < this.blocks.length; i++) {
      const offset = i * this.blockRows * 12;
      gl.bindBuffer(gl.UNIFORM_BUFFER, this.blocks[i]);
      gl.bufferSubData(gl.UNIFORM_BUFFER, 0, this.metadata, offset, Math.min(this.blockRows * 12, this.metadata.length - offset));
    }
  }

  private createTexture(width: number, height: number): WebGLTexture {
    const gl = this.gl;
    if (width > gl.getParameter(gl.MAX_TEXTURE_SIZE)) throw new WaterfallRenderError();
    const texture = gl.createTexture();
    if (!texture) throw new WaterfallRenderError();
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, texture);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    gl.texImage2D(gl.TEXTURE_2D, 0, gl.R8, width, height, 0, gl.RED, gl.UNSIGNED_BYTE, null);
    return texture;
  }

  private copyTexture(source: WebGLTexture, copy: () => void): void {
    const gl = this.gl, framebuffer = gl.createFramebuffer();
    if (!framebuffer) throw new WaterfallRenderError();
    gl.bindFramebuffer(gl.FRAMEBUFFER, framebuffer);
    try {
      gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, source, 0);
      if (gl.checkFramebufferStatus(gl.FRAMEBUFFER) !== gl.FRAMEBUFFER_COMPLETE) throw new WaterfallRenderError();
      copy();
    } finally { gl.bindFramebuffer(gl.FRAMEBUFFER, null); gl.deleteFramebuffer(framebuffer); }
  }

  resize(width: number, height: number): void {
    if (this.destroyed || (width === this.width && height === this.height)) return;
    if (this.canvas.width !== width) this.canvas.width = width;
    if (this.canvas.height !== height) this.canvas.height = height;
    this.width = width; this.height = height;
    this.sizeDirty = true;
    if (width <= 0 || height <= 0 || height === this.rows) return;
    const gl = this.gl;
    this.blockRows = Math.floor(gl.getParameter(gl.MAX_UNIFORM_BLOCK_SIZE) / 48);
    if (height > gl.getParameter(gl.MAX_TEXTURE_SIZE)
      || Math.ceil(height / this.blockRows) > gl.getParameter(gl.MAX_FRAGMENT_UNIFORM_BLOCKS)) throw new WaterfallRenderError();
    const metadata = new Float32Array(height * 12), retained = Math.min(this.count, height);
    const texture = this.createTexture(Math.max(1, this.textureWidth), height);
    try {
      if (this.texture && retained > 0) this.copyTexture(this.texture, () => {
        const oldest = (this.head - retained + 1 + this.rows) % this.rows;
        const first = Math.min(retained, this.rows - oldest), second = retained - first;
        gl.copyTexSubImage2D(gl.TEXTURE_2D, 0, 0, 0, 0, oldest, this.textureWidth, first);
        metadata.set(this.metadata.subarray(oldest * 12, (oldest + first) * 12));
        if (second > 0) {
          gl.copyTexSubImage2D(gl.TEXTURE_2D, 0, 0, first, 0, 0, this.textureWidth, second);
          metadata.set(this.metadata.subarray(0, second * 12), first * 12);
        }
      });
    } catch (error) { gl.deleteTexture(texture); throw error; }
    gl.deleteTexture(this.texture); this.texture = texture;
    this.textureWidth = Math.max(1, this.textureWidth);
    this.metadata = metadata; this.rows = height; this.head = retained - 1; this.count = retained;
    this.configureRows();
  }

  private growTexture(width: number): void {
    const gl = this.gl, texture = this.createTexture(width, this.rows);
    try {
      if (this.texture && this.count > 0) this.copyTexture(this.texture, () => {
        gl.copyTexSubImage2D(gl.TEXTURE_2D, 0, 0, 0, 0, 0, this.textureWidth, this.rows);
      });
    } catch (error) { gl.deleteTexture(texture); throw error; }
    gl.deleteTexture(this.texture); this.texture = texture; this.textureWidth = width;
  }

  pushRow(data: Uint8Array, identity?: WaterfallFrameIdentity): void {
    if (this.destroyed || !this.program || this.width <= 0 || this.height <= 0) return;
    const offset = this.options.sampleOffset ?? 0, samples = data.length - offset;
    if (samples <= 0) return;
    // One group per NEW frame: a repeated or older identity is a
    // registration replay — no rows, no draw. A new epoch restarts the
    // smoothing state exactly like a geometry change (keep goes false
    // below via smoothBins/previousValid). Calls without an identity keep
    // the legacy always-process behaviour.
    if (identity !== undefined) {
      if (identity.epoch !== this.frameEpoch) {
        this.frameEpoch = identity.epoch;
        this.lastSeq = 0;
        this.smoothBins = 0;
        this.previousValid = false;
      }
      if (identity.seq <= this.lastSeq) return;
      this.lastSeq = identity.seq;
    }
    if (data.length > this.textureWidth) this.growTexture(data.length);
    const gl = this.gl;
    const o = this.options, span = o.sampleSpanHz || o.spanHz || 1;
    const start = o.sampleWindow?.start ?? 0, range = (o.sampleWindow?.end ?? 1) - start;
    const centre = o.centerHz - o.panoramaShiftHz;
    const keep = this.smoothBins === samples && this.smoothSpan === span
      && this.smoothStart === start && this.smoothRange === range && this.smoothCentre === centre;
    if (this.smoothState.length !== samples) {
      this.smoothState = new Float32Array(samples);
      this.filtered = new Float32Array(samples);
      this.currentRow = new Uint8Array(samples);
      this.previousRow = new Uint8Array(samples);
    }
    if (this.rowUpload.length < data.length) this.rowUpload = new Uint8Array(data.length);
    for (let i = 0; i < offset; i++) this.rowUpload[i] = data[i];
    const temporal = o.temporalSmoothing !== false && keep;
    for (let i = 0; i < samples; i++) {
      const value = data[offset + i];
      this.smoothState[i] = temporal ? (this.smoothState[i] + value) / 2 : value;
    }
    for (let i = 0; i < samples; i++) {
      const left = this.smoothState[i > 0 ? i - 1 : 0];
      const right = this.smoothState[i + 1 < samples ? i + 1 : samples - 1];
      this.filtered[i] = (left + 2 * this.smoothState[i] + right) / 4;
      this.currentRow[i] = Math.round(this.filtered[i]);
    }
    const sampleStart = centre - span / 2 - start * span / range;
    const delta = sampleStart - this.originHz;
    this.row[0] = Math.fround(delta); this.row[1] = delta - this.row[0]; this.row[2] = span / range;
    this.row[3] = (o.coverage?.startHz ?? Math.min(sampleStart, sampleStart + span / range)) - sampleStart;
    this.row[4] = (o.coverage?.endHz ?? Math.max(sampleStart, sampleStart + span / range)) - sampleStart;
    this.row[5] = o.sampleAmplitudeMax ?? 80; this.row[6] = samples; this.row[7] = offset;
    this.row[8] = this.width; this.row[9] = start; this.row[10] = range;
    const group = Math.min(3, this.rows);
    const blend = this.previousValid && keep;
    for (let k = 1; k <= group; k++) {
      if (blend && k < group) {
        for (let i = 0; i < samples; i++) {
          this.rowUpload[offset + i] = Math.round(
            (this.previousRow[i] * (group - k) + this.currentRow[i] * k) / group);
        }
      } else this.rowUpload.set(this.currentRow, offset);
      this.head = (this.head + 1) % this.rows;
      this.count = Math.min(this.rows, this.count + 1);
      this.metadata.set(this.row, this.head * 12);
      gl.bindBuffer(gl.UNIFORM_BUFFER, this.blocks[Math.floor(this.head / this.blockRows)]);
      gl.bufferSubData(gl.UNIFORM_BUFFER, (this.head % this.blockRows) * 48, this.row);
      gl.bindTexture(gl.TEXTURE_2D, this.texture);
      gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, this.head, data.length, 1, gl.RED, gl.UNSIGNED_BYTE, this.rowUpload, 0);
    }
    this.previousRow.set(this.currentRow);
    this.previousValid = true;
    this.smoothBins = samples; this.smoothSpan = span;
    this.smoothStart = start; this.smoothRange = range; this.smoothCentre = centre;
    this.draw();
  }

  /** Repaints history without mutating either the texture or its row geometry. */
  draw(): void {
    if (this.destroyed || !this.program || this.width <= 0 || this.height <= 0) return;
    const gl = this.gl, o = this.options;
    if (this.viewDirty) {
      const viewStart = this.viewCenter - this.viewSpan / 2 - this.originHz, viewHigh = Math.fround(viewStart);
      gl.uniform2f(this.uniforms.get('uView')!, viewHigh, this.viewSpan);
      gl.uniform1f(this.uniforms.get('uViewLow')!, viewStart - viewHigh);
      this.viewDirty = false;
    }
    if (this.sizeDirty) {
      gl.viewport(0, 0, this.width, this.height);
      gl.uniform2f(this.uniforms.get('uSize')!, this.width, this.height);
      gl.uniform1i(this.uniforms.get('uRows')!, this.rows);
      this.sizeDirty = false;
    }
    if (this.sentHead !== this.head) {
      gl.uniform1i(this.uniforms.get('uHead')!, this.head); this.sentHead = this.head;
    }
    if (this.sentCount !== this.count) {
      gl.uniform1i(this.uniforms.get('uCount')!, this.count); this.sentCount = this.count;
    }
    if (this.appearanceDirty) {
      gl.uniform1f(this.uniforms.get('uRef')!, o.refLevel);
      const palette = COLOR_SCHEMES[o.colorScheme];
      if (this.palette !== o.colorScheme) {
        this.palette = o.colorScheme;
        for (let i = 0; i < palette.length; i++) {
          this.stops[i] = palette[i].stop;
          const value = Number.parseInt(palette[i].color.slice(1), 16);
          this.colors[i * 3] = value >> 16;
          this.colors[i * 3 + 1] = (value >> 8) & 255;
          this.colors[i * 3 + 2] = value & 255;
        }
      }
      gl.uniform3fv(this.uniforms.get('uColors')!, this.colors);
      gl.uniform1fv(this.uniforms.get('uStops')!, this.stops);
      gl.uniform1i(this.uniforms.get('uColorCount')!, palette.length);
      this.appearanceDirty = false;
    }
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  }

  updateOptions(options: Partial<WaterfallOptions>): boolean {
    if ((options.colorScheme !== undefined && options.colorScheme !== this.options.colorScheme)
      || (options.refLevel !== undefined && options.refLevel !== this.options.refLevel)) this.appearanceDirty = true;
    Object.assign(this.options, options);
    if (this.options.spanHz > 0 && Number.isFinite(this.options.centerHz)) {
      if (this.viewCenter !== this.options.centerHz || this.viewSpan !== this.options.spanHz) this.viewDirty = true;
      this.viewCenter = this.options.centerHz; this.viewSpan = this.options.spanHz;
    }
    return this.viewDirty || this.appearanceDirty;
  }
  get paintedView(): View {
    return { startHz: this.viewCenter - this.viewSpan / 2, endHz: this.viewCenter + this.viewSpan / 2 };
  }
  pixelToFreq(x: number): number {
    return this.width > 0 && this.options.spanHz > 0
      ? this.options.centerHz - this.options.spanHz / 2 + x / this.width * this.options.spanHz : this.options.centerHz;
  }
  clear(): void {
    this.head = -1; this.count = 0;
    this.smoothBins = 0; this.previousValid = false;
    this.draw();
  }
  destroy(): void {
    if (!this.destroyed) {
      this.destroyed = true; this.releaseProgram(); this.gl.deleteTexture(this.texture); this.texture = null;
    }
  }
}
