import { scopeWire } from '$lib/runtime/__tests__/scope-wire-fixture';
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
  it.each(['continued', 'delayed'] as const)('%s delivery characterizes receipt and paint independently', async (mode) => {
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
    // MOR-4206: the waterfall paints from a requestAnimationFrame callback
    // (latest-wins per animation frame). The test owns the rAF queue and
    // flushes it after every live delivery, so each such frame paints in
    // its own animation frame and receipt vs paint stay observable; the
    // deferred burst below deliberately withholds the flush so one
    // animation frame faces the whole drain.
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
    vi.stubGlobal('ResizeObserver', class {
      observe() {} unobserve() {} disconnect() {}
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
        // Mirrors the SpectrumPanel waterfallPush registration: backlog
        // handling is consolidated into the canvas's rAF pump (MOR-4206) —
        // every delivery reaches the registration unfiltered and replaces
        // the pending frame, so one animation frame paints only the
        // latest delivery.
        onRegisterPush: (fn) => {
          push = (pixels) => fn(pixels);
        },
      },
    });
    flushSync();
    const arrivals: number[] = [];
    const accepted: { id: number; sequence: number; receivedAt: number }[] = [];
    controller.subscribeTraceReceipt((receipt) => {
      if (receipt.kind !== 'accepted') return;
      accepted.push({ id: receipt.frame.pixels![0]!, sequence: receipt.token.acceptedSequence,
        receivedAt: receipt.acceptedAt });
    });
    const unsubscribe = controller.stream('hardware').subscribe((frame) => {
      if (frame.pixels) push?.(frame.pixels);
    });
    const handle = await controller.hardwareScopeDriver.start();
    socket.connected();
    const baselineIds = [1, 2, 3];
    const ids = Array.from({ length: 64 }, (_, index) => index + 4);
    // These buffers are retained by this test, not by production or a modeled WebKit queue.
    const produced = ids.map(frame);
    const liveIds = [68, 69, 70, 71, 72, 73, 74];
    const deliver = (buffer: ArrayBuffer, paint = true) => {
      arrivals.push(new DataView(buffer).getUint32(12, true));
      socket.deliver(buffer);
      flushSync();
      if (paint) flushPaint();
    };
    try {
      expect(push).toBeTypeOf('function');
      // Visible live-spaced baseline: every delivery gets its own
      // animation frame, so all three paint; they also establish the
      // pre-gap delivery history the later gap is measured against.
      for (const id of baselineIds) { deliver(frame(id)); advance(83); }
      expect(pushed.mock.calls.map(([pixels]) => pixels[0])).toEqual(baselineIds);
      hidden = true;
      if (mode === 'continued') for (const buffer of produced) deliver(buffer);
      expect(pushed).toHaveBeenCalledTimes(3);
      expect(paint).toHaveBeenCalledTimes(3);
      expect(arrivals).toEqual(mode === 'continued' ? [...baselineIds, ...ids] : baselineIds);
      advance(2 * 60 * 60 * 1000);
      flushSync();
      expect(get(controller.stream('hardware').geometry)?.freshness).toBe('stale');
      hidden = false;
      document.dispatchEvent(new Event('visibilitychange'));
      flushSync();
      expect(pushed).toHaveBeenCalledTimes(3);
      if (mode === 'delayed') {
        // The deferred backlog drains back-to-back with no animation
        // frame in between: every delivery reaches the registration
        // unfiltered and replaces the pump's pending frame, so the single
        // flush that follows paints ONLY the burst's final frame
        // (latest-wins consolidation, MOR-4206) — one deferred row stands
        // in for the whole backlog.
        for (const buffer of produced) { deliver(buffer, false); advance(1); }
        flushPaint();
      }
      // Live frames after the return: each paints in its own animation
      // frame — a normal silence costs no live rows. In `continued` the
      // hidden deliveries never reached the pump (the canvas drops
      // deliveries while the document is hidden) — whether a real Space
      // switch keeps delivering while hidden is unconfirmed (MOR-3471
      // trace pending).
      for (const id of liveIds) { advance(83); deliver(frame(id)); }
      expect(arrivals).toEqual([...baselineIds, ...ids, ...liveIds]);
      expect(accepted.map((value) => value.id)).toEqual([...baselineIds, ...ids, ...liveIds]);
      expect(accepted.map((value) => value.sequence)).toEqual([...baselineIds, ...ids, ...liveIds]);
      const paintedIds = pushed.mock.calls.map(([pixels]) => pixels[0]);
      expect(paintedIds).toEqual(mode === 'delayed'
        ? [...baselineIds, ids.at(-1)!, ...liveIds]
        : [...baselineIds, ...liveIds]);
      expect(paint).toHaveBeenCalledTimes(paintedIds.length);
      expect(accepted[0].receivedAt).toBe(0);
      expect(accepted.at(-1)?.receivedAt).toBe(now);
      expect(get(controller.stream('hardware').geometry)?.freshness).toBe('fresh');
      expect(socket.sessionEpoch).toBe(1);
      expect(socket.connect).toHaveBeenCalledTimes(1);
      // Painting went through rAF scheduling and nothing is left pending:
      // every delivered frame painted in its own animation frame except
      // the coalesced burst, and no backlog survives either path.
      expect(raf).toHaveBeenCalled();
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
