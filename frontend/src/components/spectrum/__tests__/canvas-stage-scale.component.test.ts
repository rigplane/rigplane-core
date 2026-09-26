/** MOR-1161: backing store is cssSize * devicePixelRatio * stageScale, redrawn on either change. */
import { flushSync, mount, unmount } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import SpectrumCanvas from '../SpectrumCanvas.svelte';
import WaterfallCanvas from '../WaterfallCanvas.svelte';

const CSS_WIDTH = 200;
const CSS_HEIGHT = 100;

let target: HTMLDivElement;
let observe: ResizeObserverCallback;
let pixelListeners: Map<string, () => void>;
let component: ReturnType<typeof mount> | undefined;
let stageScale = 1;

function paintedSize(element: HTMLElement): { width: number; height: number } {
  if (element instanceof HTMLCanvasElement) return { width: CSS_WIDTH * stageScale, height: CSS_HEIGHT * stageScale };
  return { width: CSS_WIDTH, height: CSS_HEIGHT };
}

beforeEach(() => {
  stageScale = 1;
  target = document.createElement('div');
  document.body.appendChild(target);
  vi.stubGlobal('requestAnimationFrame', () => 1);
  vi.stubGlobal('cancelAnimationFrame', () => {});
  pixelListeners = new Map();
  vi.stubGlobal('matchMedia', (query: string) => ({
    addEventListener: (_type: string, listener: () => void) => { pixelListeners.set(query, listener); },
    removeEventListener: (_type: string, listener: () => void) => {
      if (pixelListeners.get(query) === listener) pixelListeners.delete(query);
    },
  }));
  vi.stubGlobal('ResizeObserver', class {
    constructor(callback: ResizeObserverCallback) { observe = callback; }
    observe(element: Element) {
      observe([{ contentRect: { width: CSS_WIDTH, height: CSS_HEIGHT }, target: element } as ResizeObserverEntry], this as unknown as ResizeObserver);
    }
    disconnect() {}
  });
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
    setTransform: () => {}, clearRect: () => {}, fillRect: () => {}, drawImage: () => {},
    createImageData: (w: number) => ({ data: new Uint8ClampedArray(w * 4), width: w, height: 1 }),
  } as unknown as CanvasRenderingContext2D);
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const box = paintedSize(this);
    return { ...box, left: 0, top: 0, right: box.width, bottom: box.height, x: 0, y: 0, toJSON() { return this; } } as DOMRect;
  });
  Object.defineProperty(HTMLElement.prototype, 'offsetWidth', { configurable: true, get: () => CSS_WIDTH });
  Object.defineProperty(HTMLElement.prototype, 'offsetHeight', { configurable: true, get: () => CSS_HEIGHT });
  Object.defineProperty(window, 'devicePixelRatio', { configurable: true, value: 2 });
});

afterEach(async () => {
  if (component) await unmount(component);
  component = undefined;
  target.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  Reflect.deleteProperty(HTMLElement.prototype, 'offsetWidth');
  Reflect.deleteProperty(HTMLElement.prototype, 'offsetHeight');
});

function fireResize(): void {
  observe([{ contentRect: { width: CSS_WIDTH, height: CSS_HEIGHT }, target: target.querySelector('canvas')! } as ResizeObserverEntry], {} as ResizeObserver);
  flushSync();
}

describe.each([
  ['SpectrumCanvas', SpectrumCanvas],
  ['WaterfallCanvas', WaterfallCanvas],
] as const)('%s backing store (MOR-1161)', (_name, CanvasComponent) => {
  it.each([1, 1.5, 2, 3])('matches cssSize x devicePixelRatio x stage scale %s', (scale) => {
    stageScale = scale;
    component = mount(CanvasComponent, { target, props: {} });
    flushSync();
    const canvas = target.querySelector('canvas')!;
    expect(canvas.width).toBe(Math.round(CSS_WIDTH * 2 * scale));
    expect(canvas.height).toBe(Math.round(CSS_HEIGHT * 2 * scale));
  });

  it('redraws when the stage scale changes without a layout resize', () => {
    component = mount(CanvasComponent, { target, props: {} });
    flushSync();
    const canvas = target.querySelector('canvas')!;
    expect(canvas.width).toBe(CSS_WIDTH * 2);
    stageScale = 2;
    fireResize();
    expect(canvas.width).toBe(CSS_WIDTH * 4);
    expect(canvas.height).toBe(CSS_HEIGHT * 4);
  });

  it('redraws when devicePixelRatio changes without a layout resize', () => {
    component = mount(CanvasComponent, { target, props: {} });
    flushSync();
    Object.defineProperty(window, 'devicePixelRatio', { configurable: true, value: 3 });
    pixelListeners.get('(resolution: 2dppx)')?.();
    flushSync();
    const canvas = target.querySelector('canvas')!;
    expect(canvas.width).toBe(CSS_WIDTH * 3);
    expect(canvas.height).toBe(CSS_HEIGHT * 3);
  });
});
