/**
 * MOR-4342: a temporary GPU size-limit failure must not brick the
 * waterfall. `failed()` destroys and nulls the renderer, and only mount /
 * `webglcontextrestored` re-created it — so once a resize exceeded the GPU
 * limits (the throw path in `WaterfallRenderer.resize`), shrinking the
 * panel back never recovered: `applyBacking` no-opped on the null renderer
 * and the error veil stayed forever. A later resize that fits the limits
 * must re-initialize the renderer.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { webglFixture } from '$lib/renderers/__tests__/webgl-fixture';
import {
  WaterfallRenderError,
  WaterfallRenderer,
  type WaterfallOptions,
} from '../../../lib/renderers/waterfall-renderer';
import WaterfallQueuedFrameHarness, { harnessOptions } from './WaterfallQueuedFrameHarness.svelte';

function options(centerHz: number, panoramaShiftHz: number): WaterfallOptions {
  return { colorScheme: 'classic', refLevel: 0, speed: 1, centerHz, spanHz: 100_000, panoramaShiftHz };
}

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | undefined;
let rafCallbacks: Map<number, FrameRequestCallback>;
let rafId: number;
let resizeCallback: ResizeObserverCallback | undefined;
let observed: Element | undefined;
let intersectionCallback: IntersectionObserverCallback | undefined;
let hidden: boolean;
let originalHiddenDescriptor: PropertyDescriptor | undefined;

function flushPaint(): void {
  const pending = [...rafCallbacks.values()];
  rafCallbacks.clear();
  for (const callback of pending) callback(performance.now());
}

function resize(width: number, height: number): void {
  resizeCallback!([{ target: observed!, contentRect: { width, height } } as ResizeObserverEntry],
    {} as ResizeObserver);
  flushSync();
}

beforeEach(() => {
  target = document.createElement('div');
  document.body.appendChild(target);
  rafCallbacks = new Map();
  rafId = 0;
  resizeCallback = undefined;
  observed = undefined;
  intersectionCallback = undefined;
  hidden = false;
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
    rafCallbacks.set(++rafId, callback);
    return rafId;
  });
  vi.stubGlobal('cancelAnimationFrame', (id: number) => { rafCallbacks.delete(id); });
  vi.stubGlobal('ResizeObserver', class {
    constructor(cb: ResizeObserverCallback) { resizeCallback = cb; }
    observe(element: Element) { observed = element; }
    unobserve() {}
    disconnect() {}
  });
  vi.stubGlobal('IntersectionObserver', class {
    constructor(cb: IntersectionObserverCallback) { intersectionCallback = cb; }
    observe() {}
    unobserve() {}
    disconnect() {}
  });
  originalHiddenDescriptor = Object.getOwnPropertyDescriptor(document, 'hidden');
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
  HTMLElement.prototype.setPointerCapture = vi.fn();
  HTMLElement.prototype.releasePointerCapture = vi.fn();
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(function (this: HTMLCanvasElement, kind: string) {
    if (kind === 'webgl2') return webglFixture(this.width, this.height).gl;
    return {
      canvas: this, fillRect: vi.fn(), drawImage: vi.fn(),
      createImageData: (width: number, height: number) => ({ width, height, data: new Uint8ClampedArray(width * height * 4) }),
      putImageData: vi.fn(),
      getImageData: (x: number, y: number, width: number, height: number) => ({ width, height, data: new Uint8ClampedArray(width * height * 4) }),
    } as unknown as CanvasRenderingContext2D;
  });
  harnessOptions.value = options(14_000_000, 0);
});

afterEach(async () => {
  if (component) await unmount(component);
  component = undefined;
  target.remove();
  if (originalHiddenDescriptor) Object.defineProperty(document, 'hidden', originalHiddenDescriptor);
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function mountHarness(props: Record<string, unknown> = {}): void {
  component = mount(WaterfallQueuedFrameHarness, { target, props });
  flushSync();
  resize(300, 150);
  intersectionCallback?.([{ target: observed!, isIntersecting: true } as IntersectionObserverEntry],
    {} as IntersectionObserver);
  flushSync();
  flushPaint();
}

describe('waterfall size-limit failure recovery (MOR-4342)', () => {
  it('re-initializes the renderer when a later resize fits the GPU limits', () => {
    let push: ((pixels: Uint8Array, frameOptions?: WaterfallOptions) => void) | undefined;
    mountHarness({ onRegisterPush: (fn: typeof push) => { push = fn; } });
    expect(target.querySelector('[role="alert"]')).toBeNull();

    // The panel grows past the GPU limits: the resize throws, the renderer
    // is destroyed and the error veil appears.
    const resizeSpy = vi.spyOn(WaterfallRenderer.prototype, 'resize')
      .mockImplementationOnce(() => { throw new WaterfallRenderError(); });
    resize(300, 1500);
    expect(target.querySelector('[role="alert"]')).not.toBeNull();
    resizeSpy.mockRestore();

    // Shrinking back must recover: the veil clears and the next delivered
    // frame paints through a live renderer again.
    resize(300, 150);
    expect(target.querySelector('[role="alert"]')).toBeNull();
    const pushSpy = vi.spyOn(WaterfallRenderer.prototype, 'pushRow');
    push!(new Uint8Array(475).fill(64), options(14_000_000, 0));
    flushPaint();
    expect(pushSpy).toHaveBeenCalledTimes(1);
  });
});
