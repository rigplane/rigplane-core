/** MOR-1161: backing store is cssSize * devicePixelRatio * stageScale, redrawn on either change. */
import { flushSync, mount, unmount } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import CanvasStageHarness from './CanvasStageHarness.svelte';

const CSS_WIDTH = 200;
const CSS_HEIGHT = 100;
const NATIVE_WIDTH = 400;
const NATIVE_HEIGHT = 200;

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | undefined;
let hostWidth = NATIVE_WIDTH;
let hostHeight = NATIVE_HEIGHT;

/**
 * jsdom has no layout, so the stage's holder reports this box. Resizing it
 * is what moves `ScaledStage`'s own scale — the test never calls a
 * `ResizeObserver` callback itself.
 */
/** Box an element would report in a real browser: its own inline size when it
 *  has one, otherwise the size the host stub gives the stage's holder. */
function boxFor(element: Element): { width: number; height: number } {
  const style = element instanceof HTMLElement ? element.style : null;
  return {
    width: style?.width ? parseFloat(style.width) : hostWidth,
    height: style?.height ? parseFloat(style.height) : hostHeight,
  };
}

/**
 * Reports each observed element's OWN box, the way a real `ResizeObserver`
 * does, and records the stage holder so the test can resize the host. The
 * test never invokes a callback itself.
 */
class HostResizeObserver {
  static holders: { callback: ResizeObserverCallback; target: Element }[] = [];
  private readonly callback: ResizeObserverCallback;

  constructor(callback: ResizeObserverCallback) {
    this.callback = callback;
  }

  observe(element: Element): void {
    const box = boxFor(element);
    this.callback([{ target: element, contentRect: box } as unknown as ResizeObserverEntry], this as unknown as ResizeObserver);
    if (element instanceof HTMLElement && element.classList.contains('scaled-stage-holder')) {
      HostResizeObserver.holders.push({ callback: this.callback, target: element });
    }
  }

  disconnect(): void {}
}

function rectFor(element: HTMLElement): { width: number; height: number } {
  const box = boxFor(element);
  if (element.classList.contains('scaled-stage-holder')) return { width: hostWidth, height: hostHeight };
  // A canvas painted under the stage's transform: its layout box is its own
  // size, its painted box is that size times the stage's current scale.
  const scale = hostWidth / NATIVE_WIDTH;
  return { width: box.width * scale, height: box.height * scale };
}

function resizeHost(width: number, height: number): void {
  hostWidth = width;
  hostHeight = height;
  for (const holder of HostResizeObserver.holders) {
    holder.callback(
      [{ target: holder.target, contentRect: { width, height } } as unknown as ResizeObserverEntry],
      {} as ResizeObserver,
    );
  }
  flushSync();
}

beforeEach(() => {
  hostWidth = NATIVE_WIDTH;
  hostHeight = NATIVE_HEIGHT;
  HostResizeObserver.holders = [];
  target = document.createElement('div');
  document.body.appendChild(target);
  vi.stubGlobal('requestAnimationFrame', () => 1);
  vi.stubGlobal('cancelAnimationFrame', () => {});
  vi.stubGlobal('ResizeObserver', HostResizeObserver);
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(function (this: HTMLCanvasElement) {
    return {
      canvas: this, setTransform: () => {}, clearRect: () => {}, fillRect: () => {}, drawImage: () => {},
      createImageData: (w: number) => ({ data: new Uint8ClampedArray(w * 4), width: w, height: 1 }),
    } as unknown as CanvasRenderingContext2D;
  });
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (this: HTMLElement) {
    const box = rectFor(this);
    return { ...box, left: 0, top: 0, right: box.width, bottom: box.height, x: 0, y: 0, toJSON() { return this; } } as DOMRect;
  });
  Object.defineProperty(HTMLElement.prototype, 'offsetWidth', { configurable: true, get() { return rectFor(this as HTMLElement).width; } });
  Object.defineProperty(HTMLElement.prototype, 'offsetHeight', { configurable: true, get() { return rectFor(this as HTMLElement).height; } });
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

function mountInStage(kind: 'spectrum' | 'waterfall'): void {
  component = mount(CanvasStageHarness, { target, props: { canvas: kind, nativeW: NATIVE_WIDTH, nativeH: NATIVE_HEIGHT } });
}

describe.each([
  ['SpectrumCanvas', 'spectrum'],
  ['WaterfallCanvas', 'waterfall'],
] as const)('%s backing store (MOR-1161)', (_name, kind) => {
  // `ScaledStage` caps its scale at 1, so a canvas only ever sees a scale at
  // or below its authored size. 1, 0.5, 1/3 and 2/3 cover the ticket's x1,
  // x2 and x3 device-pixel ratios plus the fractional case.
  it.each([1, 0.5, 1 / 3, 2 / 3])('matches cssSize x devicePixelRatio x stage scale %s', (scale) => {
    hostWidth = NATIVE_WIDTH * scale;
    hostHeight = NATIVE_HEIGHT * scale;
    mountInStage(kind);
    flushSync();
    const canvas = target.querySelector('canvas')!;
    expect(canvas.width).toBe(Math.round(CSS_WIDTH * 2 * scale));
    expect(canvas.height).toBe(Math.round(CSS_HEIGHT * 2 * scale));
  });

  it('recomputes the backing store when the stage scale changes', () => {
    mountInStage(kind);
    flushSync();
    const canvas = target.querySelector('canvas')!;
    expect(canvas.width).toBe(CSS_WIDTH * 2);

    // Half the host: the stage's own measurement drops its scale to 0.5.
    resizeHost(NATIVE_WIDTH / 2, NATIVE_HEIGHT / 2);

    expect(canvas.width).toBe(CSS_WIDTH);
    expect(canvas.height).toBe(CSS_HEIGHT / 2);
  });
});
