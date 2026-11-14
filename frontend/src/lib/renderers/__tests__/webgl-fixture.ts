import { vi } from 'vitest';

/** Records the WebGL calls; pixel output is checked by the browser tests. */
export function webglFixture(width = 100, height = 50) {
  const calls: { name: string; args: unknown[] }[] = [];
  const constants = ['VERTEX_SHADER', 'FRAGMENT_SHADER', 'COMPILE_STATUS', 'LINK_STATUS',
    'TEXTURE_2D', 'TEXTURE0', 'R8', 'RED', 'UNSIGNED_BYTE', 'TEXTURE_MIN_FILTER',
    'TEXTURE_MAG_FILTER', 'NEAREST', 'TEXTURE_WRAP_S', 'TEXTURE_WRAP_T', 'CLAMP_TO_EDGE',
    'UNPACK_ALIGNMENT', 'TRIANGLE_STRIP', 'UNIFORM_BUFFER', 'DYNAMIC_DRAW',
    'FRAMEBUFFER', 'COLOR_ATTACHMENT0', 'FRAMEBUFFER_COMPLETE', 'MAX_UNIFORM_BLOCK_SIZE', 'MAX_FRAGMENT_UNIFORM_BLOCKS', 'MAX_TEXTURE_SIZE'];
  const values = Object.fromEntries(constants.map((key, index) => [key, index + 1]));
  const canvas = document.createElement('canvas');
  canvas.width = width; canvas.height = height;
  const gl = new Proxy({ ...values, canvas }, {
    get(target, key: string) {
      if (key in target) return target[key as keyof typeof target];
      return (...args: unknown[]) => {
        // Snapshot typed-array arguments at call time: the renderer reuses
        // its upload buffers, so a stored reference would alias the final
        // contents by the time an assertion reads it.
        calls.push({ name: key, args: args.map(arg =>
          ArrayBuffer.isView(arg) ? (arg as Uint8Array).slice() : arg) });
        if (key === 'getShaderParameter' || key === 'getProgramParameter') return true;
        if (key === 'getParameter') return args[0] === values.MAX_UNIFORM_BLOCK_SIZE ? 16384
          : args[0] === values.MAX_FRAGMENT_UNIFORM_BLOCKS ? 12 : 16384;
        if (key === 'checkFramebufferStatus') return values.FRAMEBUFFER_COMPLETE;
        if (key.startsWith('create')) return {};
        if (key === 'getUniformLocation') return args[1];
        if (key === 'getUniformBlockIndex') return 0;
        return undefined;
      };
    },
  }) as unknown as WebGL2RenderingContext;
  const getContext = vi.fn((kind: string) => kind === 'webgl2' ? gl : null);
  Object.defineProperty(canvas, 'getContext', { value: getContext });
  return { canvas, gl, calls, getContext,
    named: (name: string) => calls.filter(call => call.name === name) };
}

export function countTypedArrays() {
  let count = 0;
  for (const key of ['Uint8Array', 'Uint8ClampedArray', 'Uint16Array', 'Uint32Array', 'Float32Array', 'Float64Array'] as const) {
    const Original = globalThis[key];
    vi.stubGlobal(key, new Proxy(Original, {
      construct(target, args) { count++; return Reflect.construct(target, args); },
    }));
  }
  return () => count;
}
