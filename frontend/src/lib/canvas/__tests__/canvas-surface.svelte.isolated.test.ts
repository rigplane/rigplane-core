import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync } from 'svelte';
import { CanvasSurface, canvasBackingSize, type CanvasBackingSize } from '../canvas-surface.svelte';

describe('canvasBackingSize', () => {
  it.each([
    [1, 1, 320, 160],
    [1, 1.5, 480, 240],
    [1, 2, 640, 320],
    [1, 3, 960, 480],
    [2, 1, 640, 320],
    [2, 2, 1280, 640],
    [1.5, 1.5, 720, 360],
  ])('css 320x160 at dpr %s and stage scale %s is %sx%s device pixels', (dpr, stageScale, width, height) => {
    expect(canvasBackingSize(320, 160, dpr, stageScale)).toEqual({ width, height, pixelScale: dpr * stageScale });
  });

  it('floors a missing scale at 1 and rounds a fractional product', () => {
    expect(canvasBackingSize(10, 8, 0, Number.NaN)).toEqual({ width: 10, height: 8, pixelScale: 1 });
    expect(canvasBackingSize(100, 50, 1.25, 1.5)).toEqual({ width: 188, height: 94, pixelScale: 1.875 });
  });
});

interface FakeResizeEntry {
  target: Element;
  contentRect: { width: number; height: number };
}

interface FakeIntersectionEntry {
  target: Element;
  isIntersecting: boolean;
}

const resizeObservers: Array<{ cb: (entries: FakeResizeEntry[]) => void; disconnected: boolean }> = [];
const intersectionObservers: Array<{
  cb: (entries: FakeIntersectionEntry[]) => void;
  options?: { rootMargin?: string };
  disconnected: boolean;
}> = [];
const mediaListeners = new Map<string, Set<() => void>>();

let hidden = false;
let dpr = 1;
let setTransform: ReturnType<typeof vi.fn>;
let originalHiddenDescriptor: PropertyDescriptor | undefined;
let originalDprDescriptor: PropertyDescriptor | undefined;

function fireResize(target: Element, width: number, height: number) {
  for (const observer of resizeObservers) {
    if (!observer.disconnected) observer.cb([{ target, contentRect: { width, height } }]);
  }
}

function fireIntersection(target: Element, isIntersecting: boolean) {
  for (const observer of intersectionObservers) {
    if (!observer.disconnected) observer.cb([{ target, isIntersecting }]);
  }
}

function fireMedia(query: string) {
  for (const listener of mediaListeners.get(query) ?? []) listener();
}

function createSurface(options?: ConstructorParameters<typeof CanvasSurface>[0]) {
  let surface!: CanvasSurface;
  const destroy = $effect.root(() => {
    surface = new CanvasSurface(options);
  });
  return { surface, destroy };
}

beforeEach(() => {
  resizeObservers.length = 0;
  intersectionObservers.length = 0;
  mediaListeners.clear();
  hidden = false;
  dpr = 1;

  vi.stubGlobal(
    'ResizeObserver',
    class {
      private readonly cb: (entries: FakeResizeEntry[]) => void;
      constructor(cb: (entries: FakeResizeEntry[]) => void) {
        this.cb = cb;
        resizeObservers.push({ cb, disconnected: false });
      }
      observe() {}
      unobserve() {}
      disconnect() {
        const record = resizeObservers.find((observer) => observer.cb === this.cb);
        if (record) record.disconnected = true;
      }
    },
  );
  vi.stubGlobal(
    'IntersectionObserver',
    class {
      private readonly cb: (entries: FakeIntersectionEntry[]) => void;
      constructor(cb: (entries: FakeIntersectionEntry[]) => void, options?: { rootMargin?: string }) {
        this.cb = cb;
        intersectionObservers.push({ cb, options, disconnected: false });
      }
      observe() {}
      unobserve() {}
      disconnect() {
        const record = intersectionObservers.find((observer) => observer.cb === this.cb);
        if (record) record.disconnected = true;
      }
    },
  );
  vi.stubGlobal('matchMedia', (query: string) => ({
    addEventListener: (_type: string, listener: () => void) => {
      const set = mediaListeners.get(query) ?? new Set<() => void>();
      set.add(listener);
      mediaListeners.set(query, set);
    },
    removeEventListener: (_type: string, listener: () => void) => {
      mediaListeners.get(query)?.delete(listener);
    },
  }));

  originalHiddenDescriptor = Object.getOwnPropertyDescriptor(document, 'hidden');
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
  originalDprDescriptor = Object.getOwnPropertyDescriptor(window, 'devicePixelRatio');
  Object.defineProperty(window, 'devicePixelRatio', { configurable: true, get: () => dpr });

  setTransform = vi.fn();
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({ setTransform } as unknown as RenderingContext);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  if (originalHiddenDescriptor) Object.defineProperty(document, 'hidden', originalHiddenDescriptor);
  if (originalDprDescriptor) Object.defineProperty(window, 'devicePixelRatio', originalDprDescriptor);
});

describe('CanvasSurface sizing', () => {
  it('publishes css and backing facts when the host gains size from 0x0 (MOR-4243 class)', () => {
    const { surface, destroy } = createSurface({ stageScale: () => 1 });
    const canvas = document.createElement('canvas');
    const onSize = vi.fn();
    surface.onSizeChanged(onSize);
    const detach = surface.attach(canvas);

    fireResize(canvas, 0, 0);
    flushSync();
    expect(surface.sized).toBe(false);
    expect(surface.backingW).toBe(0);
    expect(canvas.width).toBe(300);
    expect(onSize).not.toHaveBeenCalled();

    fireResize(canvas, 640, 200);
    flushSync();
    expect(surface.cssW).toBe(640);
    expect(surface.cssH).toBe(200);
    expect(surface.sized).toBe(true);
    expect(surface.backingW).toBe(640);
    expect(surface.backingH).toBe(200);
    expect(surface.pixelScale).toBe(1);
    expect(onSize).toHaveBeenCalledTimes(1);
    expect(canvas.width).toBe(640);
    expect(canvas.height).toBe(200);
    expect(setTransform).toHaveBeenCalledWith(1, 0, 0, 1, 0, 0);

    detach();
    destroy();
  });

  it('emits sizeChanged once for repeated identical rects', () => {
    const { surface, destroy } = createSurface({ stageScale: () => 1 });
    const canvas = document.createElement('canvas');
    const onSize = vi.fn();
    surface.onSizeChanged(onSize);
    const detach = surface.attach(canvas);

    fireResize(canvas, 640, 200);
    flushSync();
    fireResize(canvas, 640, 200);
    flushSync();
    expect(onSize).toHaveBeenCalledTimes(1);

    detach();
    destroy();
  });

  it('re-applies the backing store once on a devicePixelRatio change and re-arms the query', () => {
    dpr = 2;
    const { surface, destroy } = createSurface({ stageScale: () => 1 });
    const canvas = document.createElement('canvas');
    const onSize = vi.fn();
    surface.onSizeChanged(onSize);
    const detach = surface.attach(canvas);
    fireResize(canvas, 640, 200);
    flushSync();
    expect(surface.pixelScale).toBe(2);
    expect(canvas.width).toBe(1280);
    expect(onSize).toHaveBeenCalledTimes(1);

    dpr = 3;
    fireMedia('(resolution: 2dppx)');
    flushSync();
    expect(surface.pixelScale).toBe(3);
    expect(canvas.width).toBe(1920);
    expect(onSize).toHaveBeenCalledTimes(2);

    dpr = 4;
    fireMedia('(resolution: 3dppx)');
    flushSync();
    expect(canvas.width).toBe(2560);
    expect(onSize).toHaveBeenCalledTimes(3);

    detach();
    destroy();
  });

  it('re-applies the backing store once when the stage scale changes', () => {
    let scale = $state(1);
    const { surface, destroy } = createSurface({ stageScale: () => scale });
    const canvas = document.createElement('canvas');
    const onSize = vi.fn();
    surface.onSizeChanged(onSize);
    const detach = surface.attach(canvas);
    fireResize(canvas, 640, 200);
    flushSync();
    expect(canvas.width).toBe(640);
    expect(onSize).toHaveBeenCalledTimes(1);

    scale = 2;
    flushSync();
    expect(surface.pixelScale).toBe(2);
    expect(canvas.width).toBe(1280);
    expect(onSize).toHaveBeenCalledTimes(2);

    detach();
    destroy();
  });

  it('delegates backing application to a custom applier (renderer-owned resize)', () => {
    const applyBacking = vi.fn();
    const { surface, destroy } = createSurface({ stageScale: () => 1, applyBacking });
    const canvas = document.createElement('canvas');
    const detach = surface.attach(canvas);
    fireResize(canvas, 640, 200);
    flushSync();
    const expected: CanvasBackingSize = { width: 640, height: 200, pixelScale: 1 };
    expect(applyBacking).toHaveBeenCalledWith(canvas, expected);
    expect(canvas.width).toBe(300);
    expect(setTransform).not.toHaveBeenCalled();
    detach();
    destroy();
  });
});

describe('CanvasSurface visibility', () => {
  it('is visible only while intersecting and the document is not hidden', () => {
    const { surface, destroy } = createSurface({ stageScale: () => 1 });
    const canvas = document.createElement('canvas');
    const onVisibility = vi.fn();
    surface.onVisibilityChanged(onVisibility);
    const detach = surface.attach(canvas);
    flushSync();
    expect(surface.visible).toBe(false);
    expect(onVisibility).not.toHaveBeenCalled();

    fireIntersection(canvas, true);
    flushSync();
    expect(surface.visible).toBe(true);
    expect(onVisibility).toHaveBeenCalledTimes(1);
    expect(onVisibility).toHaveBeenLastCalledWith(true);

    hidden = true;
    document.dispatchEvent(new Event('visibilitychange'));
    flushSync();
    expect(surface.visible).toBe(false);
    expect(onVisibility).toHaveBeenLastCalledWith(false);

    hidden = false;
    document.dispatchEvent(new Event('visibilitychange'));
    flushSync();
    expect(surface.visible).toBe(true);

    fireIntersection(canvas, false);
    flushSync();
    expect(surface.visible).toBe(false);

    detach();
    destroy();
  });

  it('treats a missing IntersectionObserver as intersecting', () => {
    vi.stubGlobal('IntersectionObserver', undefined);
    const { surface, destroy } = createSurface({ stageScale: () => 1 });
    const canvas = document.createElement('canvas');
    const detach = surface.attach(canvas);
    flushSync();
    expect(surface.visible).toBe(true);
    detach();
    destroy();
  });

  it('detach removes observers and listeners', () => {
    const { surface, destroy } = createSurface({ stageScale: () => 1 });
    const canvas = document.createElement('canvas');
    const onVisibility = vi.fn();
    const onSize = vi.fn();
    surface.onVisibilityChanged(onVisibility);
    surface.onSizeChanged(onSize);
    const detach = surface.attach(canvas);
    fireIntersection(canvas, true);
    flushSync();
    expect(surface.visible).toBe(true);

    detach();
    expect(resizeObservers.every((observer) => observer.disconnected)).toBe(true);
    expect(intersectionObservers.every((observer) => observer.disconnected)).toBe(true);

    fireIntersection(canvas, false);
    fireResize(canvas, 800, 600);
    hidden = true;
    document.dispatchEvent(new Event('visibilitychange'));
    flushSync();
    expect(surface.visible).toBe(true);
    expect(onVisibility).toHaveBeenCalledTimes(1);
    expect(onSize).not.toHaveBeenCalled();
    destroy();
  });
});
