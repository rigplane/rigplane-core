import { flushSync, mount, unmount } from 'svelte';
import { expect, it, vi } from 'vitest';
import AudioSpectrumCanvas from '../AudioSpectrumCanvas.svelte';

it('draws a queued frame when a collapsed canvas gains size', async () => {
  let callback: ResizeObserverCallback | undefined;
  let observed: Element;
  let frame: FrameRequestCallback | undefined;
  let push: ((pixels: Uint8Array) => void) | undefined;
  let style = '';
  const strokes: string[] = [];
  vi.stubGlobal('ResizeObserver', class {
    constructor(cb: ResizeObserverCallback) { callback = cb; }
    observe(element: Element) { observed = element; }
    disconnect() {}
  });
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => { frame = cb; return 1; });
  vi.stubGlobal('cancelAnimationFrame', () => {});
  const noop = () => {};
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
    setTransform: noop, clearRect: noop, fillRect: noop, fillText: noop,
    beginPath: noop, moveTo: noop, lineTo: noop, closePath: noop,
    stroke: () => strokes.push(style), fill: noop, clip: noop, save: noop,
    restore: noop, quadraticCurveTo: noop,
    createLinearGradient: () => ({ addColorStop: noop }),
    set strokeStyle(value: string) { style = value; },
  } as unknown as CanvasRenderingContext2D);
  const target = document.createElement('div');
  document.body.appendChild(target);
  const component = mount(AudioSpectrumCanvas, { target, props: {
    data: null, bandwidth: 3600, filterWidth: NaN, filterWidthMax: 3600, ifShift: 0,
    onRegisterPush: (fn: (pixels: Uint8Array) => void) => { push = fn; },
  } });
  flushSync();
  push!(new Uint8Array(100).fill(40));
  frame?.(0);
  callback!([{ target: observed!, contentRect: { width: 400, height: 160 } } as ResizeObserverEntry], {} as ResizeObserver);
  flushSync();
  frame?.(0);
  expect(strokes).toContain('rgba(0, 220, 220, 0.9)');
  await unmount(component);
  target.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
