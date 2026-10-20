/** Real binary decoder/controller, panel fanout and canvases; fake app/transport boundary. */
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { ScopeController } from '$lib/runtime/scope-controller.svelte';
import type { WsChannel } from '$lib/transport/ws-client';
import SpectrumPanel from '../SpectrumPanel.svelte';

const harness = vi.hoisted(() => ({ controller: null as ScopeController | null, source: 'hardware' as 'hardware' | 'audio_fft' }));
vi.mock('$lib/runtime/frontend-runtime', () => ({
  presentationResources: {
    configure: vi.fn(),
    acquire: () => ({ resource: 'audio-fft', handle: harness.controller!.audioFftDriver.start() }),
    release: (lease: { handle: unknown }) => harness.controller!.audioFftDriver.stop(lease.handle),
  },
  runtime: {
    get scope() { return harness.controller!; },
    state: null,
    get caps() { return { scopeSource: harness.source }; },
    defaultScopeStatus: { transport: 'connected' },
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
  it.each([
    ['hardware', 'ordinary', false], ['hardware', 'ordinary', true],
    ['hardware', 'continued-hidden', false], ['audio_fft', 'continued-hidden', false],
    ['hardware', 'deferred-return', false], ['audio_fft', 'deferred-return', false],
    ['hardware', 'default-hidden', false], ['audio_fft', 'default-hidden', false],
    ['hardware', 'disabled', false], ['audio_fft', 'disabled', false],
  ] as const)('%s %s; export callback throws=%s', async (source, mode, exportThrows) => {
    harness.source = source;
    let now = 1_000;
    let hidden = false;
    let focused = true;
    vi.spyOn(performance, 'now').mockImplementation(() => now);
    vi.spyOn(document, 'hidden', 'get').mockImplementation(() => hidden);
    vi.spyOn(document, 'visibilityState', 'get').mockImplementation(() => hidden ? 'hidden' : 'visible');
    vi.spyOn(document, 'hasFocus').mockImplementation(() => focused);
    vi.stubEnv('VITE_SCOPE_TRACE_CANDIDATE', mode === 'disabled' ? '0' : '1');
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
    const channelFactory = vi.fn(() => transport as unknown as WsChannel);
    harness.controller = new ScopeController(channelFactory);
    const target = document.createElement('div'); document.body.appendChild(target);
    const completed = vi.fn((_contents: string) => { if (exportThrows) throw new Error('sink failed'); });
    const panel = mount(SpectrumPanel, { target, props: {
      hideToolbar: true, showBandPlan: false, captureTraceTimeline: mode === 'ordinary', onTraceComplete: completed,
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
      expect(channelFactory).toHaveBeenCalledWith(source === 'audio_fft' ? 'audio-scope' : 'scope');
      if (mode === 'disabled') {
        expect(button('Start trace')).toBeUndefined();
        expect(button('Start transition trace')).toBeUndefined();
        expect(completed).not.toHaveBeenCalled();
        return;
      }
      const transition = mode === 'continued-hidden' || mode === 'deferred-return';
      const start = button(transition ? 'Start transition trace' : 'Start trace');
      expect(start).toBeDefined();
      expect(start.disabled).toBe(false);
      start.click(); flushSync();
      if (transition || mode === 'default-hidden') {
        now += 10;
        transport.receive('010000809fd5002026d700ffff0003000080ff');
        flushSync(); drawPending();
        now += 10; focused = false; window.dispatchEvent(new Event('blur'));
        now += 10; hidden = true; document.dispatchEvent(new Event('visibilitychange')); flushSync();
        if (mode === 'default-hidden') {
          expect(completed).toHaveBeenCalledTimes(1);
          const report = JSON.parse(completed.mock.calls[0][0]);
          expect(report.reason).toBe('hidden');
          expect(report.counts.accepted).toBe(1);
          expect(report).not.toHaveProperty('transition');
          expect(report).not.toHaveProperty('timeline');
          hidden = false; document.dispatchEvent(new Event('visibilitychange'));
          transport.receive('010000809fd5002026d70000000103000080ff');
          flushSync(); drawPending();
          expect(completed).toHaveBeenCalledTimes(1);
          return;
        }
        expect(completed).not.toHaveBeenCalled();
        now += 10;
        if (mode === 'continued-hidden') {
          transport.receive('010000809fd5002026d70000000103000080ff');
          flushSync(); // Canvas visibility suppresses RAF scheduling while hidden.
        }
        now += 500;
        hidden = false;
        // Delivery before visibilitychange must observe the current document state.
        if (mode === 'deferred-return') transport.receive('010000809fd5002026d70000000103000080ff');
        transport.receive('010000809fd5002026d70001000003000080ff');
        flushSync(); drawPending();
        now += 10; document.dispatchEvent(new Event('visibilitychange')); flushSync(); drawPending();
        focused = true; window.dispatchEvent(new Event('focus'));
        button('Stop trace').click(); flushSync();
        expect(completed).toHaveBeenCalledTimes(1);
        const report = JSON.parse(completed.mock.calls[0][0]);
        expect(report.reason).toBe('operator');
        expect(report.transition).toEqual({ mode: 'document-transition', visibilityProvenance: 'document-observed' });
        expect(report.counts).toMatchObject({ accepted: 3, rejected: 0, wireGaps: 0 });
        expect(report.timelineOverflow).toBe(false);
        const events = (stage: string) => report.timeline.filter((e: { stage: string }) => e.stage === stage);
        expect(events('visibility')).toMatchObject([
          { atMs: 0, document: { hidden: false } }, { atMs: 30, document: { hidden: true } },
          { atMs: 550, document: { hidden: false } },
        ]);
        for (const stage of ['callback', 'accepted', 'fanout', 'spectrum_push']) {
          expect(events(stage)).toMatchObject([
            { atMs: 10, source, wireSequence: 65535, acceptedSequence: 2, document: { hidden: false } },
            { atMs: mode === 'continued-hidden' ? 40 : 540, source, wireSequence: 0, acceptedSequence: 3,
              document: { hidden: mode === 'continued-hidden' } },
            { atMs: 540, source, wireSequence: 1, acceptedSequence: 4, document: { hidden: false, hasFocus: false } },
          ]);
        }
        // MOR-3471 freshness gate: only the registration's first delivery
        // paints by now — the later deliveries sit inside the fresh gate's
        // resume run (or arrive hidden), so they reach the transport and
        // fan-out stages above without painting a row.
        expect(events('waterfall_push').map((e: { acceptedSequence: number }) => e.acceptedSequence))
          .toEqual([2]);
        for (const stage of ['raf', 'spectrum_render']) {
          expect(events(stage)).toMatchObject([
            { atMs: 10, wireSequence: 65535, acceptedSequence: 2 },
            { atMs: 550, wireSequence: 1, acceptedSequence: 4, document: { hidden: false } },
          ]);
        }
        expect(events('coalesced')).toEqual([]);
        expect(report.timeline.every((e: { document?: unknown }) => e.document !== undefined)).toBe(true);
        return;
      }
      transport.receive('010000809fd5002026d700ffff0003000080ff');
      transport.receive('010000809fd5002026d70000000103000080ff');
      flushSync(); drawPending();
      button('Stop trace').click(); flushSync();
      expect(completed).toHaveBeenCalledTimes(1);
      const report = JSON.parse(completed.mock.calls[0][0]);
      expect(report.counts).toMatchObject({ accepted: 2, rejected: 0, wireGaps: 0 });
      for (const stage of ['callback', 'accepted', 'fanout', 'spectrum_push']) {
        expect(report.timeline.filter((e: { stage: string }) => e.stage === stage)
          .map((e: { wireSequence: number }) => e.wireSequence)).toEqual([65535, 0]);
      }
      // MOR-3471: the two deliveries arrive back-to-back, so the second
      // is burst-drain-spaced and paints no row.
      expect(report.timeline.filter((e: { stage: string }) => e.stage === 'waterfall_push')
        .map((e: { wireSequence: number }) => e.wireSequence)).toEqual([65535]);
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
