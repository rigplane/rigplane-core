/**
 * MOR-1161: a canvas inside a CSS-scaled stage must size its backing store
 * to cssSize * devicePixelRatio * stageScale, and redraw that store when
 * the stage scale changes without the layout box changing.
 */
import { flushSync, mount, unmount } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import SpectrumCanvas from '../SpectrumCanvas.svelte';
import WaterfallCanvas from '../WaterfallCanvas.svelte';

const CSS_WIDTH = 200;
const CSS_HEIGHT = 100;

let target: HTMLDivElement;
let stage: HTMLDivElement;
let observe: ResizeObserverCallback;
let component: ReturnType<typeof mount> | undefined;

function installCanvasBox(scale: number): void {
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const width = this === stage || this.parentElement === stage ? CSS_WIDTH * scale : CSS_WIDTH;
    const height = this === stage || this.parentElement === stage ? CSS_HEIGHT * scale : CSS_HEIGHT;
    return { width, height, left: 0, top: 0, right: width, bottom: height, x: 0, y: 0, toJSON() { return this; } } as DOMRect;
  });
  Object.defineProperty(HTMLElement.prototype, 'offsetWidth', { configurable: true, get: () => CSS_WIDTH });
  Object.defineProperty(HTMLElement.prototype, 'offsetHeight', { configurable: true, get: () => CSS_HEIGHT });
}

beforeEach(() => {
  target = document.createElement('div');
  stage = document.createElement('div');
  stage.appendChild(target);
  document.body.appendChild(stage);
  vi.stubGlobal('requestAnimationFrame', () => 1);
  vi.stubGlobal('cancelAnimationFrame', () => {});
  vi.stubGlobal('ResizeObserver', class {
    constructor(callback: ResizeObserverCallback) { observe = callback; }
    observe(element: Element) {
      observe([{ contentRect: { width: CSS_WIDTH, height: CSS_HEIGHT }, target: element } as ResizeObserverEntry], this as unknown as ResizeObserver);
    }
    disconnect() {}
  });
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
    setTransform: () => {},
    clearRect: () => {},
    createImageData: (w: number) => ({ data: new Uint8ClampedArray(w * 4), width: w, height: 1 }),
  } as unknown as CanvasRenderingContext2D);
  Object.defineProperty(window, 'devicePixelRatio', { configurable: true, value: 2 });
});

afterEach(async () => {
  if (component) await unmount(component);
  component = undefined;
  stage.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  Reflect.deleteProperty(HTMLElement.prototype, 'offsetWidth');
  Reflect.deleteProperty(HTMLElement.prototype, 'offsetHeight');
});

function fireResize(): void {
  const canvas = target.querySelector('canvas')!;
  observe([{ contentRect: { width: CSS_WIDTH, height: CSS_HEIGHT }, target: canvas } as ResizeObserverEntry], {} as ResizeObserver);
  flushSync();
}

describe.each([
  ['SpectrumCanvas', SpectrumCanvas],
  ['WaterfallCanvas', WaterfallCanvas],
] as const)('%s backing store (MOR-1161)', (_name, CanvasComponent) => {
  it.each([1, 1.5, 2, 3])('matches cssSize x devicePixelRatio x stage scale %s', (scale) => {
    installCanvasBox(scale);
    component = mount(CanvasComponent, { target, props: {} });
    flushSync();
    const canvas = target.querySelector('canvas')!;
    expect(canvas.width).toBe(Math.round(CSS_WIDTH * 2 * scale));
    expect(canvas.height).toBe(Math.round(CSS_HEIGHT * 2 * scale));
  });

  it('redraws the backing store when the stage scale changes without a layout resize', () => {
    installCanvasBox(1);
    component = mount(CanvasComponent, { target, props: {} });
    flushSync();
    const canvas = target.querySelector('canvas')!;
    expect(canvas.width).toBe(CSS_WIDTH * 2);

    installCanvasBox(2);
    fireResize();
    expect(canvas.width).toBe(CSS_WIDTH * 2 * 2);
    expect(canvas.height).toBe(CSS_HEIGHT * 2 * 2);
  });
});
