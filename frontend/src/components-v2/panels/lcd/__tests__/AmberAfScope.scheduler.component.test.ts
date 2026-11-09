/**
 * MOR-4271 (R2, GREEN-B RED): AmberAfScope on CanvasSurface +
 * ScopeRenderScheduler. The scheduler is woken by input (a pushed frame, a
 * size change, a visibility change) and paints latest-wins; while the scope
 * is idle and visible it must request NO animation frame at all. The old
 * component ran a perpetual self-rearming rAF loop (`draw()` re-arms at the
 * end of every tick), which paints forever with no input — this test pins
 * the replacement contract with a rAF stub that auto-runs every requested
 * frame off a macrotask, the way a display runs them.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import {
  setLcdContrastPreset,
} from '$lib/runtime/adapters/lcd-chrome-adapter';
import AmberAfScope from '../AmberAfScope.svelte';

let component: ReturnType<typeof mount> | undefined;
let target: HTMLDivElement;
let rafRequestCount: number;
let nextHandle = 0;
let scheduled: Map<number, ReturnType<typeof setTimeout>>;
let inkFills: string[];
let currentFillStyle: string;

function macrotask(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

beforeEach(() => {
  target = document.createElement('div');
  document.body.appendChild(target);
  rafRequestCount = 0;
  scheduled = new Map();
  inkFills = [];
  currentFillStyle = '';
  setLcdContrastPreset('MID');
  // Auto-running rAF: every requested frame runs on the next macrotask, so
  // a perpetual loop cannot hide behind an unflushed manual queue.
  // cancelAnimationFrame really cancels, so unmount stops the loop.
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
    rafRequestCount++;
    const handle = ++nextHandle;
    scheduled.set(handle, setTimeout(() => {
      scheduled.delete(handle);
      callback(performance.now());
    }, 0));
    return handle;
  });
  vi.stubGlobal('cancelAnimationFrame', (handle: number) => {
    const timer = scheduled.get(handle);
    if (timer !== undefined) {
      clearTimeout(timer);
      scheduled.delete(handle);
    }
  });
  vi.stubGlobal('ResizeObserver', class {
    constructor(private readonly callback: ResizeObserverCallback) {}
    observe(element: Element) {
      this.callback([{ target: element, contentRect: { width: 320, height: 160 } } as ResizeObserverEntry],
        this as unknown as ResizeObserver);
    }
    unobserve() {}
    disconnect() {}
  });
  vi.stubGlobal('IntersectionObserver', class {
    constructor(private readonly callback: IntersectionObserverCallback) {}
    observe(element: Element) {
      this.callback([{ target: element, isIntersecting: true } as IntersectionObserverEntry],
        this as unknown as IntersectionObserver);
    }
    unobserve() {}
    disconnect() {}
  });
  vi.spyOn(Math, 'random').mockReturnValue(0.9);
  const noop = () => {};
  const ctx = {
    clearRect: noop, setTransform: noop,
    measureText: (text: string) => ({ width: String(text).length * 8 }),
    beginPath: noop, moveTo: noop, lineTo: noop, stroke: noop,
    fillRect: () => { inkFills.push(currentFillStyle); },
    fillText: () => { inkFills.push(currentFillStyle); },
  } as unknown as CanvasRenderingContext2D;
  Object.defineProperty(ctx, 'fillStyle', {
    configurable: true,
    get: () => currentFillStyle,
    set: (value: string) => { currentFillStyle = String(value); },
  });
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(ctx);
});

afterEach(async () => {
  if (component) await unmount(component);
  component = undefined;
  target.remove();
  // Drain any stray auto-run frame the old perpetual loop scheduled.
  await macrotask();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('AmberAfScope on ScopeRenderScheduler (MOR-4271)', () => {
  it('no animation frames are requested while idle and visible', async () => {
    component = mount(AmberAfScope, {
      target,
      props: { data: null, mode: 'fill', filterWidth: 2400, filterWidthMax: 3600 },
    });
    flushSync();
    // Settle: the initial size/visibility wake may paint the instrument
    // frame; let every requested frame run.
    for (let i = 0; i < 4; i++) await macrotask();

    rafRequestCount = 0;
    // Idle: no pushed frames, no size change, no visibility change — a few
    // display frames pass.
    for (let i = 0; i < 5; i++) await macrotask();
    expect(rafRequestCount).toBe(0);
  });

  it('an LCD contrast change repaints the ink once, then scheduling goes idle', async () => {
    // The scope lives on an LCD screen whose --lcd-alpha-* tokens the
    // contrast preset rewrites; refreshAlphas only runs while rendering.
    const screen = document.createElement('div');
    screen.classList.add('lcd-screen');
    document.body.appendChild(screen);
    const scopeTarget = document.createElement('div');
    screen.appendChild(scopeTarget);
    component = mount(AmberAfScope, {
      target: scopeTarget,
      props: { data: new Uint8Array(256).fill(160), mode: 'fill', filterWidth: 2400, filterWidthMax: 3600 },
    });
    flushSync();
    for (let i = 0; i < 4; i++) await macrotask();

    // MID active ink is α=1.00; DIM is α=0.55. No frames are arriving.
    expect(inkFills).toContain('rgba(26, 16, 0, 1)');
    inkFills = [];
    rafRequestCount = 0;
    setLcdContrastPreset('DIM');
    flushSync();
    for (let i = 0; i < 4; i++) await macrotask();
    // Exactly one wake → at most one requested frame, and the repaint used
    // the new DIM alpha.
    expect(rafRequestCount).toBeLessThanOrEqual(1);
    expect(inkFills).toContain('rgba(26, 16, 0, 0.55)');
    // Scheduling is idle again: no perpetual re-arming for the ink.
    rafRequestCount = 0;
    for (let i = 0; i < 5; i++) await macrotask();
    expect(rafRequestCount).toBe(0);
    screen.remove();
  });
});
