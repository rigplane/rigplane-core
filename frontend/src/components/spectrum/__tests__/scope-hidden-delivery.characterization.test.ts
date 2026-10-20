import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { ScopeController } from '$lib/runtime/scope-controller.svelte';
import { ScopeFrameHost } from '$lib/runtime/scope-frame-host';
import { WaterfallFreshnessGate } from '$lib/runtime/waterfall-freshness';
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
  const buffer = new ArrayBuffer(20);
  const view = new DataView(buffer);
  view.setUint8(0, 1);
  view.setUint32(3, 14_000_000, true);
  view.setUint32(7, 14_100_000, true);
  view.setUint16(11, id, true);
  view.setUint16(14, 4, true);
  new Uint8Array(buffer, 16).fill(id);
  return buffer;
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
    };
    function advance(ms: number) {
      now += ms;
      for (const [id, timer] of [...timers]) {
        if (timer.at <= now) { timers.delete(id); timer.callback(); }
      }
    }
    vi.spyOn(document, 'hidden', 'get').mockImplementation(() => hidden);
    // No RAF callback runs: delivery and synchronous waterfall painting remain distinct.
    const raf = vi.fn(() => 1);
    vi.stubGlobal('requestAnimationFrame', raf);
    vi.stubGlobal('cancelAnimationFrame', vi.fn());
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
    const host = new ScopeFrameHost(controller);
    host.updateAuthority({ source: 'hardware', receiver: 'MAIN', providerGeneration: 1 });
    const target = document.createElement('div');
    document.body.append(target);
    let push: ((pixels: Uint8Array) => void) | undefined;
    const suppressed: number[] = [];
    const freshness = new WaterfallFreshnessGate(timing);
    const component = mount(WaterfallCanvas, {
      target,
      props: {
        // Mirrors the SpectrumPanel waterfallPush registration (MOR-3471):
        // every delivery that reaches the registration classifies through
        // the gate — hidden or not, so the timing baseline tracks the
        // transport — and a suppressed delivery records on the
        // waterfall_suppressed trace stage there.
        onRegisterPush: (fn) => {
          push = (pixels) => {
            if (freshness.admit()) fn(pixels);
            else suppressed.push(pixels[0]);
          };
        },
      },
    });
    flushSync();
    const arrivals: number[] = [];
    const accepted: { id: number; sequence: number; receivedAt: number }[] = [];
    const unsubscribe = host.subscribePresentation((presentation) => {
      if (presentation.resolution.state !== 'live' || !presentation.envelope) return;
      const pixels = Uint8Array.from(presentation.resolution.frame.normalizedBins, (bin) => Math.round(bin * 255));
      accepted.push({ id: pixels[0], sequence: presentation.envelope.acceptedSequence, receivedAt: presentation.envelope.receivedAt });
      push?.(pixels);
    });
    const handle = await controller.hardwareScopeDriver.start();
    socket.connected();
    const baselineIds = [1, 2, 3];
    const ids = Array.from({ length: 64 }, (_, index) => index + 4);
    // These buffers are retained by this test, not by production or a modeled WebKit queue.
    const produced = ids.map(frame);
    const liveIds = [68, 69, 70, 71, 72, 73, 74];
    const deliver = (buffer: ArrayBuffer) => {
      arrivals.push(new DataView(buffer).getUint16(11, true));
      socket.deliver(buffer);
      flushSync();
    };
    try {
      expect(push).toBeTypeOf('function');
      // Visible live-spaced baseline: the gate measures the later gap
      // against this pre-gap delivery history.
      for (const id of baselineIds) { deliver(frame(id)); advance(83); }
      expect(pushed.mock.calls.map(([pixels]) => pixels[0])).toEqual(baselineIds);
      hidden = true;
      if (mode === 'continued') for (const buffer of produced) deliver(buffer);
      expect(pushed).toHaveBeenCalledTimes(baselineIds.length);
      expect(paint).toHaveBeenCalledTimes(baselineIds.length);
      expect(arrivals).toEqual(mode === 'continued' ? [...baselineIds, ...ids] : baselineIds);
      advance(2 * 60 * 60 * 1000);
      flushSync();
      expect(host.snapshotPresentation().resolution.state).toBe('ghost');
      hidden = false;
      document.dispatchEvent(new Event('visibilitychange'));
      flushSync();
      expect(pushed).toHaveBeenCalledTimes(baselineIds.length);
      if (mode === 'delayed') {
        for (const buffer of produced) { deliver(buffer); advance(1); }
      }
      // Live-spaced frames after the return: the post-gap deliveries
      // collapse (MOR-3471) and painting resumes after five consecutive
      // live-spaced frames re-earn it. In `delayed` the discontinuity is
      // the burst's first frame, so live painting resumes at frame 72; in
      // `continued` the first post-gap frame (68, itself live-spaced)
      // spends the discontinuity slot, so painting resumes one frame
      // later — a production-continued stream (frames flowing while
      // hidden) never trips the discontinuity at all.
      for (const id of liveIds) { advance(83); deliver(frame(id)); }
      expect(arrivals).toEqual([...baselineIds, ...ids, ...liveIds]);
      expect(accepted.map((value) => value.id)).toEqual([...baselineIds, ...ids, ...liveIds]);
      expect(accepted.map((value) => value.sequence)).toEqual([...baselineIds, ...ids, ...liveIds]);
      const paintedIds = pushed.mock.calls.map(([pixels]) => pixels[0]);
      expect(paintedIds).toEqual(mode === 'delayed' ? [...baselineIds, 72, 73, 74] : [...baselineIds, 73, 74]);
      expect(suppressed).toEqual(mode === 'delayed' ? [...ids, 68, 69, 70, 71] : [68, 69, 70, 71, 72]);
      expect(paint).toHaveBeenCalledTimes(paintedIds.length);
      expect(accepted[0].receivedAt).toBe(0);
      expect(accepted.at(-1)?.receivedAt).toBe(now);
      expect(host.snapshotPresentation().resolution.state).toBe('live');
      expect(socket.sessionEpoch).toBe(1);
      expect(socket.connect).toHaveBeenCalledTimes(1);
      expect(raf).not.toHaveBeenCalled();
    } finally {
      unsubscribe();
      await controller.hardwareScopeDriver.stop(handle);
      host.dispose();
      await unmount(component);
      target.remove();
      expect(timers.size).toBe(0);
    }
  });
});
