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
  coverage?: Readonly<{ startHz: number; endHz: number }>;
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
  // NEW row only; history reprojects by centerHz deltas instead.
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
    declarations += `layout(std140) uniform Rows${i} { vec4 rows${i}[${blockRows}]; };\n`;
    lookup += `if (row < ${(i + 1) * blockRows}) return rows${i}[row - ${i * blockRows}];\n`;
  }
  return `#version 300 es
precision highp float;
precision highp int;
uniform highp sampler2D uRing;
uniform vec2 uView;
uniform vec2 uSize;
uniform int uHead, uCount, uRows, uSamples;
uniform float uRef, uAmplitudeMax;
uniform vec3 uColors[6];
uniform float uStops[6];
uniform int uColorCount;
${declarations}
vec4 rowInfo(int row) { ${lookup} return vec4(0.0); }
out vec4 color;
void main() {
  int age = int(floor(uSize.y - gl_FragCoord.y));
  if (age >= uCount) { color = vec4(0.0, 16.0/255.0, 32.0/255.0, 1.0); return; }
  int row = (uHead - age + uRows) % uRows;
  vec4 info = rowInfo(row);
  float hz = uView.x + floor(gl_FragCoord.x) / uSize.x * uView.y;
  float coordinate = (hz - info.x) / info.y;
  if (coordinate < 0.0 || coordinate >= 1.0 || hz < info.z || hz >= info.w) {
    float hatch = step(4.0, mod(floor(gl_FragCoord.x) + floor(uSize.y - gl_FragCoord.y), 8.0));
    color = vec4(mix(vec3(0.0, 16.0, 32.0), vec3(36.0, 48.0, 60.0), hatch) / 255.0, 1.0);
    return;
  }
  int bin = clamp(int(floor(coordinate * float(uSamples))), 0, uSamples - 1);
  float sampleValue = texelFetch(uRing, ivec2(bin, row), 0).r * 255.0;
  sampleValue = floor(min(sampleValue, uAmplitudeMax) * 80.0 / uAmplitudeMax + 0.5);
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
  private readonly row = new Float32Array(4);
  private readonly colors = new Float32Array(18);
  private readonly stops = new Float32Array(6);
  private palette: ColorSchemeName | null = null;
  private blockRows = 0;
  private width = 0;
  private height = 0;
  private samples = 0;
  private head = -1;
  private count = 0;
  private originHz: number;
  private destroyed = false;

  constructor(private readonly canvas: HTMLCanvasElement, options: WaterfallOptions) {
    const gl = canvas.getContext('webgl2', { alpha: false, antialias: false, depth: false, stencil: false });
    if (!gl) throw new WaterfallRenderError();
    this.gl = gl;
    this.options = { ...options };
    this.originHz = options.centerHz;
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

  private release(): void {
    const gl = this.gl;
    gl.deleteTexture(this.texture); this.texture = null;
    gl.deleteProgram(this.program); this.program = null;
    for (const buffer of this.blocks) gl.deleteBuffer(buffer);
    this.blocks.length = 0; this.uniforms.clear();
  }

  resize(width: number, height: number): void {
    if (this.destroyed || (width === this.width && height === this.height)) return;
    this.canvas.width = width; this.canvas.height = height;
    this.width = width; this.height = height;
    this.release(); this.samples = 0; this.head = -1; this.count = 0;
    if (width <= 0 || height <= 0) return;
    const gl = this.gl;
    this.blockRows = Math.floor(gl.getParameter(gl.MAX_UNIFORM_BLOCK_SIZE) / 16);
    if (height > gl.getParameter(gl.MAX_TEXTURE_SIZE)
      || Math.ceil(height / this.blockRows) > gl.getParameter(gl.MAX_FRAGMENT_UNIFORM_BLOCKS)) throw new WaterfallRenderError();
    const program = gl.createProgram();
    if (!program) throw new WaterfallRenderError();
    this.program = program;
    const vertex = this.shader(gl.VERTEX_SHADER, VERTEX);
    let fragment: WebGLShader | null = null;
    try {
      fragment = this.shader(gl.FRAGMENT_SHADER, fragmentShader(height, this.blockRows));
      gl.attachShader(program, vertex); gl.attachShader(program, fragment); gl.linkProgram(program);
      if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new WaterfallRenderError();
    } finally { gl.deleteShader(vertex); gl.deleteShader(fragment); }
    gl.useProgram(program);
    for (const name of ['uRing', 'uView', 'uSize', 'uHead', 'uCount', 'uRows', 'uSamples',
      'uRef', 'uAmplitudeMax', 'uColors', 'uStops', 'uColorCount']) {
      this.uniforms.set(name, gl.getUniformLocation(program, name));
    }
    for (let i = 0; i < Math.ceil(height / this.blockRows); i++) {
      const buffer = gl.createBuffer();
      if (!buffer) throw new WaterfallRenderError();
      this.blocks.push(buffer);
      gl.bindBuffer(gl.UNIFORM_BUFFER, buffer);
      gl.bufferData(gl.UNIFORM_BUFFER, this.blockRows * 16, gl.DYNAMIC_DRAW);
      gl.bindBufferBase(gl.UNIFORM_BUFFER, i, buffer);
      gl.uniformBlockBinding(program, gl.getUniformBlockIndex(program, `Rows${i}`), i);
    }
    this.texture = gl.createTexture();
    if (!this.texture) throw new WaterfallRenderError();
    gl.activeTexture(gl.TEXTURE0); gl.bindTexture(gl.TEXTURE_2D, this.texture);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.pixelStorei(gl.UNPACK_ALIGNMENT, 1);
    this.draw();
  }

  pushRow(data: Uint8Array): void {
    if (this.destroyed || !this.program || this.width <= 0 || this.height <= 0) return;
    const offset = this.options.sampleOffset ?? 0, samples = data.length - offset;
    if (samples <= 0) return;
    const gl = this.gl;
    if (samples !== this.samples) {
      if (samples > gl.getParameter(gl.MAX_TEXTURE_SIZE)) throw new WaterfallRenderError();
      this.samples = samples; this.head = -1; this.count = 0;
      this.originHz = this.options.centerHz;
      gl.bindTexture(gl.TEXTURE_2D, this.texture);
      gl.texImage2D(gl.TEXTURE_2D, 0, gl.R8, samples, this.height, 0, gl.RED, gl.UNSIGNED_BYTE, null);
    }
    this.head = (this.head + 1) % this.height;
    this.count = Math.min(this.height, this.count + 1);
    const o = this.options, span = o.sampleSpanHz || o.spanHz || 1;
    const start = o.sampleWindow?.start ?? 0, range = (o.sampleWindow?.end ?? 1) - start;
    const sampleStart = o.centerHz - o.panoramaShiftHz - span / 2 - start * span / range;
    this.row[0] = sampleStart - this.originHz;
    this.row[1] = span / range;
    this.row[2] = (o.coverage?.startHz ?? Math.min(sampleStart, sampleStart + span / range)) - this.originHz;
    this.row[3] = (o.coverage?.endHz ?? Math.max(sampleStart, sampleStart + span / range)) - this.originHz;
    gl.bindBuffer(gl.UNIFORM_BUFFER, this.blocks[Math.floor(this.head / this.blockRows)]);
    gl.bufferSubData(gl.UNIFORM_BUFFER, (this.head % this.blockRows) * 16, this.row);
    gl.bindTexture(gl.TEXTURE_2D, this.texture);
    gl.texSubImage2D(gl.TEXTURE_2D, 0, 0, this.head, samples, 1, gl.RED, gl.UNSIGNED_BYTE, data, offset);
    this.draw();
  }

  /** Repaints history without mutating either the texture or its row geometry. */
  draw(): void {
    if (this.destroyed || !this.program || this.width <= 0 || this.height <= 0) return;
    const gl = this.gl, o = this.options;
    gl.useProgram(this.program); gl.viewport(0, 0, this.width, this.height);
    gl.uniform1i(this.uniforms.get('uRing')!, 0);
    gl.uniform2f(this.uniforms.get('uView')!, o.centerHz - (o.spanHz || 1) / 2 - this.originHz, o.spanHz || 1);
    gl.uniform2f(this.uniforms.get('uSize')!, this.width, this.height);
    gl.uniform1i(this.uniforms.get('uHead')!, this.head);
    gl.uniform1i(this.uniforms.get('uCount')!, this.count);
    gl.uniform1i(this.uniforms.get('uRows')!, this.height);
    gl.uniform1i(this.uniforms.get('uSamples')!, this.samples);
    gl.uniform1f(this.uniforms.get('uRef')!, o.refLevel);
    gl.uniform1f(this.uniforms.get('uAmplitudeMax')!, o.sampleAmplitudeMax ?? 80);
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
    gl.drawArrays(gl.TRIANGLE_STRIP, 0, 4);
  }

  updateOptions(options: Partial<WaterfallOptions>): void { Object.assign(this.options, options); }
  pixelToFreq(x: number): number {
    return this.width > 0 && this.options.spanHz > 0
      ? this.options.centerHz - this.options.spanHz / 2 + x / this.width * this.options.spanHz : this.options.centerHz;
  }
  clear(): void { this.head = -1; this.count = 0; this.draw(); }
  destroy(): void { if (!this.destroyed) { this.destroyed = true; this.release(); } }
}
