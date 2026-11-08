import { mount, flushSync, unmount } from 'svelte';
import { afterEach, expect, it, vi } from 'vitest';
import AudioSpectrumCanvas from '../AudioSpectrumCanvas.svelte';

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('clears its actual pixels and smoothing at a null push even with a legacy data prop', async () => {
  let next: FrameRequestCallback | undefined, push: ((pixels: Uint8Array | null) => void) | undefined;
  let style = '', path: number[][] = [], traces: number[][][] = [];
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { next = callback; return 1; });
  vi.stubGlobal('cancelAnimationFrame', () => { next = undefined; });
  vi.stubGlobal('ResizeObserver', class {
    constructor(private callback: ResizeObserverCallback) {}
    observe() { this.callback([{ contentRect: { width: 480, height: 160 } } as ResizeObserverEntry], this as unknown as ResizeObserver); }
    disconnect() {}
  });
  const context = new Proxy({
    clearRect() { traces = []; }, beginPath() { path = []; },
    moveTo(x: number, y: number) { path.push([x, y]); }, lineTo(x: number, y: number) { path.push([x, y]); },
    stroke() { if (style === 'rgba(0, 220, 220, 0.9)') traces.push([...path]); },
    createLinearGradient: () => ({ addColorStop() {} }), set strokeStyle(value: string) { style = value; },
  }, { get: (target, key) => key in target ? Reflect.get(target, key) : () => {} }) as unknown as CanvasRenderingContext2D;
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(context);
  const target = document.createElement('div'); document.body.append(target);
  const component = mount(AudioSpectrumCanvas, { target, props: { data: new Uint8Array(128).fill(160),
    bandwidth: 48000, filterWidth: 2400, filterWidthMax: 2400, ifShift: 0,
    onRegisterPush: callback => { push = callback; } } });
  const paint = () => { flushSync(); const callback = next; next = undefined; expect(callback).toBeTypeOf('function'); callback!(0); };
  try {
    paint(); expect(traces).toHaveLength(1);
    push!(null); paint(); expect(traces).toHaveLength(0);
    push!(new Uint8Array(128)); paint();
    expect(traces).toHaveLength(1); expect(traces[0].every(([, y]) => y === 144)).toBe(true);
    push!(new Uint8Array(128).fill(160)); paint();
    expect(traces[0].some(([, y]) => y < 144)).toBe(true);
  } finally { await unmount(component); target.remove(); }
});
