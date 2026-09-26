/** MOR-1161: backing store is cssSize * devicePixelRatio * stageScale, redrawn on either change. */
import { flushSync, mount, unmount } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import SpectrumCanvas from '../SpectrumCanvas.svelte';
import WaterfallCanvas from '../WaterfallCanvas.svelte';

const CSS_WIDTH = 200;
const CSS_HEIGHT = 100;

let target: HTMLDivElement;
let observe: ResizeObserverCallback;
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
  vi.stubGlobal('ResizeObserver', class {
    constructor(callback: ResizeObserverCallback) { observe = callback; }
    observe(element: Element) {
      observe([{ contentRect: { width: CSS_WIDTH, height: CSS_HEIGHT }, target: element } as unknown as ResizeObserverEntry], this as unknown as ResizeObserver);
    }
    disconnect() {}
  });
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(function (this: HTMLCanvasElement) {
    return {
      canvas: this, setTransform: () => {}, clearRect: () => {}, fillRect: () => {}, drawImage: () => {},
      createImageData: (w: number) => ({ data: new Uint8ClampedArray(w * 4), width: w, height: 1 }),
    } as unknown as CanvasRenderingContext2D;
  });
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
  observe([{ contentRect: { width: CSS_WIDTH, height: CSS_HEIGHT }, target: target.querySelector('canvas')! } as unknown as ResizeObserverEntry], {} as ResizeObserver);
  flushSync();
}

function mountCanvas(CanvasComponent: typeof SpectrumCanvas | typeof WaterfallCanvas): void {
  component = CanvasComponent === SpectrumCanvas
    ? mount(SpectrumCanvas, { target, props: { data: null } })
    : mount(WaterfallCanvas, { target, props: {} });
}

describe.each([
  ['SpectrumCanvas', SpectrumCanvas],
  ['WaterfallCanvas', WaterfallCanvas],
] as const)('%s backing store (MOR-1161)', (_name, CanvasComponent) => {
  it.each([1, 1.5, 2, 3])('matches cssSize x devicePixelRatio x stage scale %s', (scale) => {
    stageScale = scale;
    mountCanvas(CanvasComponent);
    flushSync();
    const canvas = target.querySelector('canvas')!;
    expect(canvas.width).toBe(Math.round(CSS_WIDTH * 2 * scale));
    expect(canvas.height).toBe(Math.round(CSS_HEIGHT * 2 * scale));
  });

  it('redraws when the stage scale changes without a layout resize', () => {
    mountCanvas(CanvasComponent);
    flushSync();
    const canvas = target.querySelector('canvas')!;
    expect(canvas.width).toBe(CSS_WIDTH * 2);
    stageScale = 2;
    fireResize();
    expect(canvas.width).toBe(CSS_WIDTH * 4);
    expect(canvas.height).toBe(CSS_HEIGHT * 4);
  });
});
