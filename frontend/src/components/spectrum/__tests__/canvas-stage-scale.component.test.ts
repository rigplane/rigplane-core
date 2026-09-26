/**
 * MOR-1161: backing store is cssSize * devicePixelRatio * stageScale, and a
 * stage-scale change recomputes it.
 *
 * The harness publishes the scale through the same context and getter that
 * `ScaledStage` uses (`provideStageScale`). jsdom has no layout, so it cannot
 * show the stage's own measurement move, but it can show that a change
 * published the way the stage publishes it recomputes the store. Nothing here
 * calls a `ResizeObserver` callback.
 */
import { flushSync, mount, unmount } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import CanvasStageHarness, { stageScale } from './CanvasStageHarness.svelte';

const CSS_WIDTH = 200;
const CSS_HEIGHT = 100;

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | undefined;

class BoxResizeObserver {
  constructor(private readonly callback: ResizeObserverCallback) {}

  observe(element: Element): void {
    this.callback(
      [{ target: element, contentRect: { width: CSS_WIDTH, height: CSS_HEIGHT } } as unknown as ResizeObserverEntry],
      this as unknown as ResizeObserver,
    );
  }

  disconnect(): void {}
}

beforeEach(() => {
  stageScale.value = 1;
  target = document.createElement('div');
  document.body.appendChild(target);
  vi.stubGlobal('requestAnimationFrame', () => 1);
  vi.stubGlobal('cancelAnimationFrame', () => {});
  vi.stubGlobal('ResizeObserver', BoxResizeObserver);
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(function (this: HTMLCanvasElement) {
    return {
      canvas: this, setTransform: () => {}, clearRect: () => {}, fillRect: () => {}, drawImage: () => {},
      createImageData: (w: number) => ({ data: new Uint8ClampedArray(w * 4), width: w, height: 1 }),
    } as unknown as CanvasRenderingContext2D;
  });
  Object.defineProperty(window, 'devicePixelRatio', { configurable: true, value: 2 });
});

afterEach(async () => {
  if (component) await unmount(component);
  component = undefined;
  target.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function mountCanvas(kind: 'spectrum' | 'waterfall', scale: number): void {
  stageScale.value = scale;
  component = mount(CanvasStageHarness, { target, props: { canvas: kind } });
}

describe.each([
  ['SpectrumCanvas', 'spectrum'],
  ['WaterfallCanvas', 'waterfall'],
] as const)('%s backing store (MOR-1161)', (_name, kind) => {
  it.each([1, 0.5, 1.5, 2, 3])('matches cssSize x devicePixelRatio x stage scale %s', (scale) => {
    mountCanvas(kind, scale);
    flushSync();
    const canvas = target.querySelector('canvas')!;
    expect(canvas.width).toBe(Math.round(CSS_WIDTH * 2 * scale));
    expect(canvas.height).toBe(Math.round(CSS_HEIGHT * 2 * scale));
  });

  it('recomputes the backing store when the published stage scale changes', () => {
    mountCanvas(kind, 1);
    flushSync();
    const canvas = target.querySelector('canvas')!;
    expect(canvas.width).toBe(CSS_WIDTH * 2);

    stageScale.value = 0.5;
    flushSync();

    expect(canvas.width).toBe(CSS_WIDTH);
    expect(canvas.height).toBe(CSS_HEIGHT);
  });
});
