/**
 * MOR-4206 review round 2, finding 1: a frame queued in the latest-wins
 * scheduler carries a delivery-time options snapshot. When the panorama
 * advances before the rAF paint runs, the paint must keep the queued pixels'
 * sample-window geometry but reconcile it with the CURRENT viewport and
 * appearance options, recomputing the sampling shift. Painting the older
 * snapshot instead moves waterfall history backward relative to the
 * spectrum and the overlays, and pixelToFreq() tunes against the reverted
 * center.
 *
 * MOR-4271 (R2): WaterfallCanvas owns a CanvasSurface + ScopeRenderScheduler.
 * The RO/IO stubs below are explicit: the scheduler only paints while the
 * surface is sized and visible, a push arriving while hidden is CACHED
 * (never dropped), and the visibility wake repaints the latest cached frame.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import {
  WaterfallRenderer,
  type WaterfallOptions,
} from '../../../lib/renderers/waterfall-renderer';
import WaterfallQueuedFrameHarness, { harnessOptions } from './WaterfallQueuedFrameHarness.svelte';

const SPAN_HZ = 100_000;
const DELIVERED_CENTER_HZ = 14_000_000;
const DELIVERED_SHIFT_HZ = 2_000;
// The queued pixels' sample window: viewport center minus shift at delivery.
const SAMPLE_WINDOW_CENTER_HZ = DELIVERED_CENTER_HZ - DELIVERED_SHIFT_HZ; // 13_998_000
const ADVANCED_CENTER_HZ = 14_010_000;
const ADVANCED_REF_LEVEL = 5;

function options(centerHz: number, panoramaShiftHz: number, refLevel = 0): WaterfallOptions {
  return { colorScheme: 'classic', refLevel, speed: 1, centerHz, spanHz: SPAN_HZ, panoramaShiftHz };
}

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | undefined;
let rafCallbacks: Map<number, FrameRequestCallback>;
let rafRequestCount: number;
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

beforeEach(() => {
  target = document.createElement('div');
  document.body.appendChild(target);
  rafCallbacks = new Map();
  rafRequestCount = 0;
  rafId = 0;
  hidden = false;
  resizeCallback = undefined;
  observed = undefined;
  intersectionCallback = undefined;
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
    rafRequestCount++;
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
  // jsdom lacks pointer capture; the gesture recognizer calls it on pointerdown.
  HTMLElement.prototype.setPointerCapture = vi.fn();
  HTMLElement.prototype.releasePointerCapture = vi.fn();
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(function (this: HTMLCanvasElement) {
    return {
      canvas: this, fillRect: vi.fn(), drawImage: vi.fn(),
      createImageData: (width: number, height: number) => ({
        width, height, data: new Uint8ClampedArray(width * height * 4),
      }),
      putImageData: vi.fn(),
      getImageData: (x: number, y: number, width: number, height: number) => ({
        width, height, data: new Uint8ClampedArray(width * height * 4),
      }),
    } as unknown as CanvasRenderingContext2D;
  });
  harnessOptions.value = options(DELIVERED_CENTER_HZ, DELIVERED_SHIFT_HZ);
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
  // The surface starts sized and visible (the test owns both observers).
  resizeCallback!([{ target: observed!, contentRect: { width: 300, height: 150 } } as ResizeObserverEntry],
    {} as ResizeObserver);
  intersectionCallback?.([{ target: observed!, isIntersecting: true } as IntersectionObserverEntry],
    {} as IntersectionObserver);
  flushSync();
  rafCallbacks.clear();
  rafRequestCount = 0;
}

describe('queued frame vs. an advancing viewport (MOR-4206 review round 2)', () => {
  it('paints the queued row under the newer viewport with a recomputed sampling shift', () => {
    const updateSpy = vi.spyOn(WaterfallRenderer.prototype, 'updateOptions');
    const pushSpy = vi.spyOn(WaterfallRenderer.prototype, 'pushRow');
    let push: ((pixels: Uint8Array, frameOptions?: WaterfallOptions) => void) | undefined;
    mountHarness({ onRegisterPush: (fn: typeof push) => { push = fn; } });
    // Delivery: the frame's sample window is centered 2 kHz below the
    // delivery-time viewport center. The frame waits for its animation
    // frame — nothing has painted yet.
    push!(new Uint8Array(475).fill(64), options(DELIVERED_CENTER_HZ, DELIVERED_SHIFT_HZ));
    expect(pushSpy).not.toHaveBeenCalled();
    // The panorama advances (and an appearance option changes) before the
    // animation frame runs; the options effect applies the newer geometry.
    harnessOptions.value = options(ADVANCED_CENTER_HZ, 12_000, ADVANCED_REF_LEVEL);
    flushSync();
    updateSpy.mockClear();
    flushPaint();
    expect(pushSpy).toHaveBeenCalledTimes(1);
    // The paint retains the queued pixels' sample-window center
    // (13_998_000 Hz) but paints it under the CURRENT viewport and
    // appearance options: the sampling shift is recomputed against the
    // advanced center (14_010_000 - 13_998_000 = 12_000 Hz) instead of
    // restoring the delivery-time center and shift.
    expect(updateSpy).toHaveBeenCalledTimes(1);
    expect(updateSpy.mock.calls[0]![0]).toMatchObject({
      centerHz: ADVANCED_CENTER_HZ,
      spanHz: SPAN_HZ,
      panoramaShiftHz: ADVANCED_CENTER_HZ - SAMPLE_WINDOW_CENTER_HZ,
      refLevel: ADVANCED_REF_LEVEL,
    });
  });

  it('tunes a tap against the newer viewport after the queued row paints', () => {
    const onFreqClick = vi.fn();
    let push: ((pixels: Uint8Array, frameOptions?: WaterfallOptions) => void) | undefined;
    mountHarness({
      onFreqClick,
      onRegisterPush: (fn: typeof push) => { push = fn; },
    });
    push!(new Uint8Array(475).fill(64), options(DELIVERED_CENTER_HZ, DELIVERED_SHIFT_HZ));
    harnessOptions.value = options(ADVANCED_CENTER_HZ, 12_000, ADVANCED_REF_LEVEL);
    flushSync();
    flushPaint();
    // jsdom canvas defaults to 300 px wide; a tap in the middle maps to the
    // viewport center. pixelToFreq must read the CURRENT center, not the
    // delivery-time one the queued snapshot carried.
    const canvas = target.querySelector('canvas')!;
    expect(canvas.width).toBe(300);
    Object.defineProperty(canvas, 'getBoundingClientRect', {
      configurable: true,
      value: () => ({ x: 0, y: 0, left: 0, top: 0, right: 300, bottom: 150, width: 300, height: 150, toJSON: () => ({}) }),
    });
    canvas.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, cancelable: true, button: 0, pointerId: 1, clientX: 150, clientY: 20 }));
    canvas.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, cancelable: true, button: 0, pointerId: 1, clientX: 150, clientY: 20 }));
    expect(onFreqClick).toHaveBeenCalledTimes(1);
    expect(onFreqClick).toHaveBeenCalledWith(ADVANCED_CENTER_HZ);
  });

  it('frames pushed while hidden repaint from cache on visibility restore', () => {
    const pushSpy = vi.spyOn(WaterfallRenderer.prototype, 'pushRow');
    let push: ((pixels: Uint8Array, frameOptions?: WaterfallOptions) => void) | undefined;
    mountHarness({ onRegisterPush: (fn: typeof push) => { push = fn; } });

    // Visible baseline: one delivered frame paints in one animation frame.
    push!(new Uint8Array(475).fill(64), options(DELIVERED_CENTER_HZ, DELIVERED_SHIFT_HZ));
    flushPaint();
    expect(pushSpy).toHaveBeenCalledTimes(1);

    // Hidden: the stream keeps accepting frames, the scheduler requests no
    // animation frame at all, and the pushed frames are NOT dropped — they
    // replace the canvas's cached latest frame.
    hidden = true;
    document.dispatchEvent(new Event('visibilitychange'));
    flushSync();
    flushPaint(); // drain any in-flight tick; nothing may re-arm
    const requestsBefore = rafRequestCount;
    for (let sequence = 2; sequence <= 21; sequence++) {
      push!(new Uint8Array(475).fill(sequence), options(DELIVERED_CENTER_HZ, DELIVERED_SHIFT_HZ));
    }
    expect(rafRequestCount - requestsBefore).toBe(0);
    flushPaint();
    expect(pushSpy).toHaveBeenCalledTimes(1);

    // Visible again: the wake repaints the LATEST pushed frame (fill 21)
    // from cache — no new delivery accompanies the repaint.
    const requestsBeforeRestore = rafRequestCount;
    hidden = false;
    document.dispatchEvent(new Event('visibilitychange'));
    flushSync();
    expect(rafRequestCount - requestsBeforeRestore).toBeGreaterThan(0);
    flushPaint();
    expect(pushSpy).toHaveBeenCalledTimes(2);
    expect(pushSpy.mock.calls.at(-1)![0][0]).toBe(21);
  });
});
