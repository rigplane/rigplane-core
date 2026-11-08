/**
 * MOR-4271 (R2): AudioSpectrumCanvas on CanvasSurface + ScopeRenderScheduler.
 *
 * The canvas owns a CanvasSurface (ResizeObserver sizing, IntersectionObserver
 * ∧ !document.hidden visibility) and paints latest-wins through a
 * ScopeRenderScheduler. A frame queued while the canvas is collapsed paints
 * when the surface gains size (acceptance (a)), and — the MOR-4243 class fix —
 * a size change must NOT reset the renderer state: the smoothed trace has to
 * survive a resize and keep diverging from the raw frame it converged on.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import AudioSpectrumCanvas from '../AudioSpectrumCanvas.svelte';

const TRACE_STROKE = 'rgba(0, 220, 220, 0.9)';

let callback: ResizeObserverCallback | undefined;
let observed: Element | undefined;
let intersectionCallback: IntersectionObserverCallback | undefined;
let rafCallbacks: Map<number, FrameRequestCallback>;
let rafId: number;
let strokes: Array<{ style: unknown; points: number[][] }>;
let path: number[][];
let style = '';
let push: ((pixels: Uint8Array) => void) | undefined;
let component: ReturnType<typeof mount> | undefined;
let target: HTMLDivElement;
let nextCtx: CanvasRenderingContext2D;

function flushPaint(): void {
  const pending = [...rafCallbacks.values()];
  rafCallbacks.clear();
  for (const cb of pending) cb(performance.now());
}

/** Trace peak amplitude, normalised against the CURRENT viewport geometry so
 *  resizes that change the trapezoid height stay comparable. */
function tracePeakAmp(trapBottom: number, trapH: number): number {
  const traces = strokes.filter((stroke) => stroke.style === TRACE_STROKE);
  expect(traces.length).toBeGreaterThan(0);
  const peak = Math.min(...traces.at(-1)!.points.map(([, y]) => y));
  return (trapBottom - peak) / trapH;
}

beforeEach(() => {
  callback = undefined;
  observed = undefined;
  rafCallbacks = new Map();
  rafId = 0;
  strokes = [];
  path = [];
  style = '';
  push = undefined;
  vi.stubGlobal('ResizeObserver', class {
    constructor(cb: ResizeObserverCallback) { callback = cb; }
    observe(element: Element) { observed = element; }
    disconnect() {}
  });
  vi.stubGlobal('IntersectionObserver', class {
    constructor(cb: IntersectionObserverCallback) { intersectionCallback = cb; }
    observe() {}
    disconnect() {}
  });
  vi.stubGlobal('requestAnimationFrame', (cb: FrameRequestCallback) => {
    rafCallbacks.set(++rafId, cb);
    return rafId;
  });
  vi.stubGlobal('cancelAnimationFrame', (id: number) => { rafCallbacks.delete(id); });
  const noop = () => {};
  nextCtx = new Proxy({
    canvas: { width: 400, height: 160 },
    setTransform: noop, clearRect: noop, fillRect: noop, fillText: noop,
    beginPath: () => { path = []; }, moveTo: (x: number, y: number) => { path.push([x, y]); },
    lineTo: (x: number, y: number) => { path.push([x, y]); },
    closePath: noop, fill: noop, clip: noop, save: noop, restore: noop,
    quadraticCurveTo: noop,
    stroke: () => { strokes.push({ style, points: [...path] }); },
    createLinearGradient: () => ({ addColorStop: noop }),
    measureText: (text: string) => ({ width: String(text).length * 8 }),
  }, {
    get: (obj, key) => (key in obj ? obj[key] : noop),
    set: (obj, key, value) => {
      if (key === 'strokeStyle') style = String(value);
      else obj[key] = value;
      return true;
    },
  }) as unknown as CanvasRenderingContext2D;
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(nextCtx);
  target = document.createElement('div');
  document.body.appendChild(target);
});

afterEach(async () => {
  if (component) await unmount(component);
  component = undefined;
  target.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function mountCanvas(props: Record<string, unknown> = {}): void {
  component = mount(AudioSpectrumCanvas, { target, props: {
    data: null, bandwidth: 3600, filterWidth: 2400, filterWidthMax: 3600, ifShift: 0,
    onRegisterPush: (fn: (pixels: Uint8Array) => void) => { push = fn; },
    ...props,
  } });
  flushSync();
  intersectionCallback?.([{ target: observed!, isIntersecting: true } as IntersectionObserverEntry],
    {} as IntersectionObserver);
  flushSync();
}

function resize(width: number, height: number): void {
  callback!([{ target: observed!, contentRect: { width, height } } as ResizeObserverEntry],
    {} as ResizeObserver);
  flushSync();
}

it('draws a queued frame when a collapsed canvas gains size (acceptance (a))', () => {
  mountCanvas();
  resize(0, 0);
  push!(new Uint8Array(100).fill(40));
  flushPaint();
  expect(strokes).toHaveLength(0);

  resize(400, 160);
  flushPaint();
  expect(strokes.some((stroke) => stroke.style === TRACE_STROKE)).toBe(true);
});

it('a resize does not reset the renderer state (MOR-4243 class)', () => {
  mountCanvas();
  resize(400, 160);
  // Converge the smoothing: nine paints of the same all-peak frame drive
  // rs.smoothed to the raw amplitude, so the trace sits at the raw peak.
  const frame = new Uint8Array(1025).fill(160);
  for (let i = 0; i < 9; i++) {
    push!(frame);
    flushPaint();
  }
  // height 160 → trapBottom = 160 - 16 = 144, trapH = 144 - 18 = 126.
  const ampBefore = tracePeakAmp(144, 126);
  expect(ampBefore).toBeGreaterThan(0.9);

  // A HEIGHT-only resize (the smoothed buffer is width-indexed, so the state
  // must be preserved exactly); the same cached frame repaints.
  strokes = [];
  resize(400, 120);
  flushPaint();

  // height 120 → trapBottom = 104, trapH = 104 - 18 = 86.
  const ampAfter = tracePeakAmp(104, 86);
  // Old code ran rendererState.reset() in the ResizeObserver callback: the
  // redraw recomputed smoothing from zero and the trace collapsed to the
  // first-attack amplitude (0.55 of the raw peak). The renderer state must
  // survive the resize — the trace still reflects the converged smoothing.
  expect(ampAfter).toBeCloseTo(ampBefore, 1);
  expect(ampAfter).toBeGreaterThan(0.9);
});
