/** Real binary decoder/controller, panel fanout and canvases; fake app/transport boundary. */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { ScopeController } from '$lib/runtime/scope-controller.svelte';
import type { WsChannel } from '$lib/transport/ws-client';
import SpectrumPanel from '../SpectrumPanel.svelte';

const harness = vi.hoisted(() => ({ controller: null as ScopeController | null }));
vi.mock('$lib/runtime/frontend-runtime', () => ({
  presentationResources: {},
  runtime: {
    get scope() { return harness.controller!; },
    state: null,
    caps: { scopeSource: 'hardware' },
    acquireHardwareScope: () => harness.controller!.hardwareScopeDriver.start(),
    releaseHardwareScope: (lease: unknown) => harness.controller!.hardwareScopeDriver.stop(lease),
    subscribeDx: () => () => {},
  },
}));
vi.mock('$lib/runtime/adapters/panel-adapters', () => ({
  getVfoHandlers: () => ({ onFreqChange: vi.fn() }),
  getFilterHandlers: () => ({ onFilterWidthChange: vi.fn() }),
  getFilterWidthCommandLifecycle: () => ({ presentation: null, busy: false }),
  getPendingFrequencyHz: () => null,
  getTuningBurstFrequencyHz: () => null,
  bindVfoTunerContext: () => ({ activeReceiver: null, viewModel: null }),
}));

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); vi.unstubAllEnvs(); document.body.innerHTML = ''; });

function channel() {
  const receivers = new Set<(buffer: ArrayBuffer) => void>();
  const states = new Set<(state: string) => void>();
  let state = 'disconnected';
  return {
    sessionEpoch: 1,
    get state() { return state; },
    connect() { state = 'connected'; for (const update of states) update(state); },
    disconnect() { state = 'disconnected'; },
    onBinary(handler: (buffer: ArrayBuffer) => void) { receivers.add(handler); return () => receivers.delete(handler); },
    onStateChange(handler: (value: string) => void) { states.add(handler); return () => states.delete(handler); },
    onSessionTransition: () => () => {},
    receive(hex: string) {
      const bytes = Uint8Array.from(hex.match(/../g)!, value => Number.parseInt(value, 16));
      for (const handler of receivers) handler(bytes.buffer);
    },
  };
}

describe('binary → actual controller → actual panel → actual canvases', () => {
  it.each([false, true])('correlates delivery and RAF coalescing; export callback throws=%s', async (exportThrows) => {
    vi.stubEnv('VITE_SCOPE_TRACE_CANDIDATE', '1');
    vi.stubGlobal('ResizeObserver', class { observe() {} unobserve() {} disconnect() {} });
    const rafs = new Map<number, FrameRequestCallback>();
    let nextRaf = 0;
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { rafs.set(++nextRaf, callback); return nextRaf; });
    vi.stubGlobal('cancelAnimationFrame', (id: number) => rafs.delete(id));
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(function (this: HTMLCanvasElement) {
      const context = {
        canvas: this,
        createImageData: (w: number, h: number) => ({ width: w, height: h, data: new Uint8ClampedArray(w * h * 4) }),
        measureText: () => ({ width: 0 }),
        createLinearGradient: () => ({ addColorStop() {} }),
      };
      return new Proxy(context, { get: (target, key) => key in target ? Reflect.get(target, key) : () => {} }) as unknown as CanvasRenderingContext2D;
    });
    const transport = channel();
    harness.controller = new ScopeController(() => transport as unknown as WsChannel);
    const target = document.createElement('div'); document.body.appendChild(target);
    const completed = vi.fn((_contents: string) => { if (exportThrows) throw new Error('sink failed'); });
    const panel = mount(SpectrumPanel, { target, props: {
      hideToolbar: true, showBandPlan: false, captureTraceTimeline: true, onTraceComplete: completed,
    } });
    const button = (text: string) => [...target.querySelectorAll('button')].find(b => b.textContent === text)!;
    const drawPending = () => {
      const pending = [...rafs.values()]; rafs.clear();
      for (const draw of pending) draw(performance.now());
    };
    try {
      flushSync();
      // Literal Python-encoder golden: seed connection liveness before opt-in.
      transport.receive('010000809fd5002026d700ff000003000080ff');
      flushSync(); drawPending();
      expect(button('Start trace').disabled).toBe(false);
      button('Start trace').click(); flushSync();
      transport.receive('010000809fd5002026d700ffff0003000080ff');
      transport.receive('010000809fd5002026d70000000103000080ff');
      flushSync(); drawPending();
      button('Stop trace').click(); flushSync();
      expect(completed).toHaveBeenCalledTimes(1);
      const report = JSON.parse(completed.mock.calls[0][0]);
      expect(report.counts).toMatchObject({ accepted: 2, rejected: 0, wireGaps: 0 });
      for (const stage of ['callback', 'accepted', 'fanout', 'spectrum_push', 'waterfall_push']) {
        expect(report.timeline.filter((e: { stage: string }) => e.stage === stage)
          .map((e: { wireSequence: number }) => e.wireSequence)).toEqual([65535, 0]);
      }
      expect(report.timeline.filter((e: { stage: string }) => e.stage === 'spectrum_render'))
        .toMatchObject([{ wireSequence: 0, acceptedSequence: 3, transportEpoch: 1 }]);
      expect(report.timeline.filter((e: { stage: string }) => e.stage === 'coalesced'))
        .toMatchObject([{ wireSequence: 65535, acceptedSequence: 2 }]);
      expect(report.reason).toBe('operator');
      expect(report.timelineOverflow).toBe(false);
      if (exportThrows) expect(target.textContent).toContain('Trace export callback failed');
      transport.receive('010000809fd5002026d70001000003000080ff');
      expect(harness.controller.scopeFrame?.pixels).toEqual(new Uint8Array([0, 128, 255]));
      expect(completed).toHaveBeenCalledTimes(1);
    } finally {
      await unmount(panel);
      harness.controller = null;
    }
  });
});
