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
}

export const defaultWaterfallOptions: WaterfallOptions = {
  colorScheme: 'classic',
  refLevel: 0,
  speed: 1,
  centerHz: 0,
  spanHz: 0,
  panoramaShiftHz: 0,
};

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
float profile(float column, vec4 grid, vec4 extra, int row) {
  float coordinate = grid.y + column / grid.x * grid.z;
  if (coordinate < 0.0 || coordinate > 1.0) return 0.0;
  int bin = clamp(int(floor(coordinate * extra.z + 0.0001)), 0, int(extra.z) - 1);
  float value = texelFetch(uRing, ivec2(bin + int(extra.w), row), 0).r * 255.0;
  if (extra.y == 80.0) return value;
  return floor(min(value, extra.y) * 80.0 / extra.y + 0.5);
}
void main() {
  int age = int(floor(uSize.y - gl_FragCoord.y));
  if (age >= uCount) { color = vec4(0.0, 16.0/255.0, 32.0/255.0, 1.0); return; }
  int row = (uHead - age + uRows) % uRows;
  vec4 info = rowInfo(row, 0), extra = rowInfo(row, 1);
  float delta = (uView.x - info.x) + (uViewLow - info.y);
  float localHz = delta + floor(gl_FragCoord.x) / uSize.x * uView.y;
  float coordinate = localHz / info.z;
  if (coordinate < 0.0 || coordinate >= 1.0 || localHz < info.w || localHz >= extra.x) {
    float hatch = step(4.0, mod(floor(gl_FragCoord.x) + floor(uSize.y - gl_FragCoord.y), 8.0));
    color = vec4(mix(vec3(0.0, 16.0, 32.0), vec3(36.0, 48.0, 60.0), hatch) / 255.0, 1.0);
    return;
  }
  vec4 grid = rowInfo(row, 2);
  // rc3 pushRow interpolated the translated screen profile before the colour curve.
  float spanScale = uView.y == info.z ? 1.0 : uView.y / info.z;
  float pixelScale = grid.x == uSize.x ? 1.0 : grid.x / uSize.x;
  float column = (delta / info.z - grid.y) / grid.z * grid.x
    + floor(gl_FragCoord.x) * (spanScale / grid.z) * pixelScale;
  float left = floor(column + 0.0001), fraction = clamp(column - left, 0.0, 1.0);
  float sampleValue = mix(profile(left, grid, extra, row), profile(left + 1.0, grid, extra, row), fraction);
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

  pushRow(data: Uint8Array): void {
    if (this.destroyed || !this.program || this.width <= 0 || this.height <= 0) return;
    const offset = this.options.sampleOffset ?? 0, samples = data.length - offset;
    if (samples <= 0) return;
    if (data.length > this.textureWidth) this.growTexture(data.length);
    const gl = this.gl;
    this.head = (this.head + 1) % this.rows;
    this.count = Math.min(this.rows, this.count + 1);
    const o = this.options, span = o.sampleSpanHz || o.spanHz || 1;
    const start = o.sampleWindow?.start ?? 0, range = (o.sampleWindow?.end ?? 1) - start;
    const sampleStart = o.centerHz - o.panoramaShiftHz - span / 2 - start * span / range;
    const delta = sampleStart - this.originHz;
    this.row[0] = Math.fround(delta); this.row[1] = delta - this.row[0]; this.row[2] = span / range;
    this.row[3] = (o.coverage?.startHz ?? Math.min(sampleStart, sampleStart + span / range)) - sampleStart;
    this.row[4] = (o.coverage?.endHz ?? Math.max(sampleStart, sampleStart + span / range)) - sampleStart;
    this.row[5] = o.sampleAmplitudeMax ?? 80; this.row[6] = samples; this.row[7] = offset;
    this.row[8] = this.width; this.row[9] = start; this.row[10] = range;
    this.metadata.set(this.row, this.head * 12);
    gl.bindBuffer(gl.UNIFORM_BUFFER, this.blocks[Math.floor(this.head / this.blockRows)]);
    gl.bufferSubData(gl.UNIFORM_BUFFER, (this.head % this.blockRows) * 48, this.row);
    gl.bindTexture(gl.TEXTURE_2D, this.texture);
    gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, this.head, data.length, 1, gl.RED, gl.UNSIGNED_BYTE, data, 0);
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

  updateOptions(options: Partial<WaterfallOptions>): void {
    if ((options.colorScheme !== undefined && options.colorScheme !== this.options.colorScheme)
      || (options.refLevel !== undefined && options.refLevel !== this.options.refLevel)) this.appearanceDirty = true;
    Object.assign(this.options, options);
    if (this.options.spanHz > 0 && Number.isFinite(this.options.centerHz)) {
      if (this.viewCenter !== this.options.centerHz || this.viewSpan !== this.options.spanHz) this.viewDirty = true;
      this.viewCenter = this.options.centerHz; this.viewSpan = this.options.spanHz;
    }
  }
  get paintedView(): View {
    return { startHz: this.viewCenter - this.viewSpan / 2, endHz: this.viewCenter + this.viewSpan / 2 };
  }
  pixelToFreq(x: number): number {
    return this.width > 0 && this.options.spanHz > 0
      ? this.options.centerHz - this.options.spanHz / 2 + x / this.width * this.options.spanHz : this.options.centerHz;
  }
  clear(): void { this.head = -1; this.count = 0; this.draw(); }
  destroy(): void {
    if (!this.destroyed) {
      this.destroyed = true; this.releaseProgram(); this.gl.deleteTexture(this.texture); this.texture = null;
    }
  }
}
