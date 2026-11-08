import { scopeWire } from '$lib/runtime/__tests__/scope-wire-fixture';
/**
 * Hidden-tab delivery characterization, MOR-4271 surface/scheduler world.
 *
 * The canvas owns a CanvasSurface (visibility = IntersectionObserver ∧
 * !document.hidden) and a ScopeRenderScheduler (active = surface.visible ∧
 * surface.sized). The contract being pinned:
 *
 *  - Hidden ⇒ the scheduler requests NO animation frame at all; nothing
 *    paints. (The old canvas also requested none, but only because it DROPPED
 *    the deliveries at the push registration — the new one stops scheduling
 *    while still accepting every frame into its latest-frame cache.)
 *  - The STREAM keeps accepting frames while hidden: arrival and accepted
 *    sequences advance, and the geometry freshness clock still runs.
 *  - The visibility wake repaints the CACHED LATEST frame — one row stands in
 *    for the whole hidden burst, latest-wins, with no new delivery.
 *
 * Real ScopeController over a fake channel and a fake liveness clock; the
 * test owns the rAF queue, the ResizeObserver and the IntersectionObserver.
 */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { get } from 'svelte/store';
import { ScopeController } from '$lib/runtime/scope-controller.svelte';
import { WaterfallRenderer } from '$lib/renderers/waterfall-renderer';
import WaterfallCanvas from '../WaterfallCanvas.svelte';

function channel() {
  const binary = new Set<(buffer: ArrayBuffer) => void>();
  const states = new Set<(state: string) => void>();
  const sessions = new Set<(value: { state: string; epoch: number }) => void>();
  let state = 'disconnected';
  const sessionEpoch = 1;
  return {
    get state() { return state; },
    sessionEpoch,
    connect: vi.fn(), disconnect: vi.fn(),
    sendRaw: vi.fn(() => true), onMessage: () => () => {},
    onBinary(handler: (buffer: ArrayBuffer) => void) {
      binary.add(handler); return () => { binary.delete(handler); };
    },
    onStateChange(handler: (value: string) => void) {
      states.add(handler); return () => { states.delete(handler); };
    },
    onSessionTransition(handler: (value: { state: string; epoch: number }) => void) {
      sessions.add(handler); return () => { sessions.delete(handler); };
    },
    connected() {
      state = 'connected';
      for (const handler of states) handler(state);
      for (const handler of sessions) handler({ state, epoch: sessionEpoch });
    },
    deliver(buffer: ArrayBuffer) { for (const handler of binary) handler(buffer); },
  };
}

function frame(id: number): ArrayBuffer {
  return scopeWire({ sourceId: 'radio', seq: id, viewStartHz: 14_000_000, viewEndHz: 14_100_000, pixels: new Uint8Array(4).fill(id) });
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('same-session scope delivery across hidden rendering', () => {
  it('hidden stops scheduling, the stream keeps accepting, and the wake repaints the cached latest frame', async () => {
    let now = 0;
    let hidden = false;
    let timerId = 0;
    const timers = new Map<number, { at: number; callback: () => void }>();
    const timing = {
      now: () => now,
      setTimeout(callback: () => void, delay: number) {
        const id = ++timerId;
        timers.set(id, { at: now + delay, callback });
        return id as unknown as ReturnType<typeof setTimeout>;
      },
      clearTimeout(id: ReturnType<typeof setTimeout>) { timers.delete(id as unknown as number); },
      // MOR-4270: the controller's one shared liveness clock (repeating).
      setInterval(callback: () => void, delay: number) {
        const id = ++timerId;
        const arm = () => { timers.set(id, { at: now + delay, callback: () => { callback(); arm(); } }); };
        arm();
        return id as unknown as ReturnType<typeof setInterval>;
      },
      clearInterval(id: ReturnType<typeof setInterval>) { timers.delete(id as unknown as number); },
    };
    function advance(ms: number) {
      now += ms;
      for (const [id, timer] of [...timers]) {
        if (timer.at <= now) { timers.delete(id); timer.callback(); }
      }
    }
    vi.spyOn(document, 'hidden', 'get').mockImplementation(() => hidden);
    // The test owns the animation-frame queue: a rAF stub that counts
    // requests, flushed explicitly after every live delivery.
    const rafCallbacks = new Map<number, FrameRequestCallback>();
    let rafId = 0;
    const raf = vi.fn((callback: FrameRequestCallback) => {
      rafCallbacks.set(++rafId, callback);
      return rafId;
    });
    vi.stubGlobal('requestAnimationFrame', raf);
    vi.stubGlobal('cancelAnimationFrame', (id: number) => { rafCallbacks.delete(id); });
    const flushPaint = (): void => {
      const pending = [...rafCallbacks.values()];
      rafCallbacks.clear();
      for (const callback of pending) callback(now);
    };
    let resizeCallback: ResizeObserverCallback | undefined;
    let observed: Element | undefined;
    vi.stubGlobal('ResizeObserver', class {
      constructor(cb: ResizeObserverCallback) { resizeCallback = cb; }
      observe(element: Element) { observed = element; }
      unobserve() {}
      disconnect() {}
    });
    let intersectionCallback: IntersectionObserverCallback | undefined;
    vi.stubGlobal('IntersectionObserver', class {
      constructor(cb: IntersectionObserverCallback) { intersectionCallback = cb; }
      observe() {}
      unobserve() {}
      disconnect() {}
    });
    const paint = vi.fn();
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(function (this: HTMLCanvasElement) {
      return {
        canvas: this, fillRect: vi.fn(), drawImage: vi.fn(),
        createImageData: (width: number, height: number) => ({
          width, height, data: new Uint8ClampedArray(width * height * 4),
        }),
        putImageData: paint,
        getImageData: (x: number, y: number, width: number, height: number) => ({
          width, height, data: new Uint8ClampedArray(width * height * 4),
        }),
      } as unknown as CanvasRenderingContext2D;
    });
    const pushed = vi.spyOn(WaterfallRenderer.prototype, 'pushRow');
    const socket = channel();
    const controller = new ScopeController(() => socket as never, timing);
    const target = document.createElement('div');
    document.body.append(target);
    let push: ((pixels: Uint8Array) => void) | undefined;
    const component = mount(WaterfallCanvas, {
      target,
      props: {
        // Mirrors the SpectrumPanel waterfallPush registration: every
        // delivery reaches the canvas's latest-frame cache unfiltered.
        onRegisterPush: (fn) => {
          push = (pixels) => fn(pixels);
        },
      },
    });
    flushSync();
    // Sized and visible before any delivery.
    resizeCallback?.([{ target: observed!, contentRect: { width: 300, height: 150 } } as ResizeObserverEntry],
      {} as ResizeObserver);
    intersectionCallback?.([{ target: observed!, isIntersecting: true } as IntersectionObserverEntry],
      {} as IntersectionObserver);
    flushSync();
    const arrivals: number[] = [];
    const accepted: { id: number; sequence: number; receivedAt: number }[] = [];
    controller.subscribeTraceReceipt((receipt) => {
      if (receipt.kind !== 'accepted') return;
      accepted.push({ id: receipt.frame.pixels![0]!, sequence: receipt.token.acceptedSequence,
        receivedAt: receipt.acceptedAt });
    });
    const unsubscribe = controller.stream('hardware').subscribe((streamFrame) => {
      if (streamFrame.pixels) push?.(streamFrame.pixels);
    });
    const handle = await controller.hardwareScopeDriver.start();
    socket.connected();
    const baselineIds = [1, 2, 3];
    const hiddenIds = Array.from({ length: 20 }, (_, index) => index + 4);
    const liveIds = [24, 25, 26, 27, 28];
    const deliver = (buffer: ArrayBuffer, paintFrame = true) => {
      arrivals.push(new DataView(buffer).getUint32(12, true));
      socket.deliver(buffer);
      flushSync();
      if (paintFrame) flushPaint();
    };
    try {
      expect(push).toBeTypeOf('function');
      // Visible live-spaced baseline: every delivery gets its own animation
      // frame, so all three paint; they also establish the pre-gap delivery
      // history the later gap is measured against.
      for (const id of baselineIds) { deliver(frame(id)); advance(83); }
      expect(pushed.mock.calls.map(([pixels]) => pixels[0])).toEqual(baselineIds);
      expect(paint).toHaveBeenCalledTimes(3);

      hidden = true;
      document.dispatchEvent(new Event('visibilitychange'));
      flushSync();
      flushPaint(); // drain the in-flight tick; the scheduler must not re-arm
      expect(rafCallbacks.size).toBe(0);
      // Hidden burst: the stream still ACCEPTS every frame while the
      // scheduler requests no animation frame at all.
      const rafRequestsBeforeHiddenBurst = raf.mock.calls.length;
      for (const id of hiddenIds) { deliver(frame(id)); advance(1); }
      expect(arrivals).toEqual([...baselineIds, ...hiddenIds]);
      expect(accepted.map((value) => value.id)).toEqual([...baselineIds, ...hiddenIds]);
      expect(accepted.map((value) => value.sequence)).toEqual([...baselineIds, ...hiddenIds]);
      expect(pushed).toHaveBeenCalledTimes(3);
      expect(paint).toHaveBeenCalledTimes(3);
      expect(raf.mock.calls.length - rafRequestsBeforeHiddenBurst).toBe(0);
      expect(rafCallbacks.size).toBe(0);

      advance(2 * 60 * 60 * 1000);
      flushSync();
      expect(get(controller.stream('hardware').geometry)?.freshness).toBe('stale');

      // Visibility wake: ONE row paints, standing latest-wins for the whole
      // hidden burst from the cached frame — no new delivery accompanies it.
      hidden = false;
      document.dispatchEvent(new Event('visibilitychange'));
      flushSync();
      flushPaint();
      expect(pushed).toHaveBeenCalledTimes(4);
      expect(pushed.mock.calls.at(-1)![0][0]).toBe(hiddenIds.at(-1));
      expect(paint).toHaveBeenCalledTimes(4);

      // Live frames after the return: each paints in its own animation frame.
      for (const id of liveIds) { advance(83); deliver(frame(id)); }
      expect(pushed.mock.calls.map(([pixels]) => pixels[0])).toEqual(
        [...baselineIds, hiddenIds.at(-1)!, ...liveIds]);
      expect(accepted.at(-1)?.receivedAt).toBe(now);
      expect(get(controller.stream('hardware').geometry)?.freshness).toBe('fresh');
      expect(socket.sessionEpoch).toBe(1);
      expect(socket.connect).toHaveBeenCalledTimes(1);
      // Nothing is left pending: no scheduler backlog, no animation frame.
      expect(rafCallbacks.size).toBe(0);
    } finally {
      unsubscribe();
      await controller.hardwareScopeDriver.stop(handle);
      await unmount(component);
      target.remove();
      expect(timers.size).toBe(0);
    }
  });
});
