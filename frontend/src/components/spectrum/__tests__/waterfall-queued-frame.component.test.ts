/**
 * MOR-4206 review round 2, finding 1: a frame queued in the latest-wins
 * pump carries a delivery-time options snapshot. When the panorama advances
 * before the rAF paint runs, the paint must keep the queued pixels'
 * sample-window geometry but reconcile it with the CURRENT viewport and
 * appearance options, recomputing the sampling shift. Painting the older
 * snapshot instead moves waterfall history backward relative to the
 * spectrum and the overlays, and pixelToFreq() tunes against the reverted
 * center.
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
let rafId: number;

function flushPaint(): void {
  const pending = [...rafCallbacks.values()];
  rafCallbacks.clear();
  for (const callback of pending) callback(performance.now());
}

beforeEach(() => {
  target = document.createElement('div');
  document.body.appendChild(target);
  rafCallbacks = new Map();
  rafId = 0;
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
    rafCallbacks.set(++rafId, callback);
    return rafId;
  });
  vi.stubGlobal('cancelAnimationFrame', (id: number) => { rafCallbacks.delete(id); });
  vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
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
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function mountHarness(props: Record<string, unknown> = {}): void {
  component = mount(WaterfallQueuedFrameHarness, { target, props });
  flushSync();
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
});
