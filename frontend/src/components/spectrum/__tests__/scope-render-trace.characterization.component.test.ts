import { scopeWire } from '$lib/runtime/__tests__/scope-wire-fixture';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { parseCurrentScopeFrame } from '$lib/runtime/adapters/scope-adapter';
import producerFrames from '../../../components-v2/layout/__tests__/fixtures/audio-fft-producer-frames.json';
import { ScopeRenderTrace } from '$lib/runtime/scope-render-trace';
import { WaterfallRenderer } from '$lib/renderers/waterfall-renderer';
import { SpectrumRenderer } from '$lib/renderers/spectrum-renderer';
import type { WaterfallOptions } from '$lib/renderers/waterfall-renderer';
import type { TraceToken } from '$lib/runtime/scope-render-trace';
import WaterfallCanvas from '../WaterfallCanvas.svelte';
import SpectrumCanvas from '../SpectrumCanvas.svelte';

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); document.body.innerHTML = ''; });

describe('actual canvas trace callbacks', () => {
  // MOR-4206: the waterfall no longer paints synchronously per push — like
  // the spectrum it coalesces latest-wins to one paint per rAF, tracing the
  // dropped frame as `coalesced`. MOR-4271 moves both canvases onto
  // CanvasSurface + ScopeRenderScheduler and KEEPS this trace contract:
  // `recordGeometry` on draw, `coalesced` (with the REPLACED frame's token)
  // when a push arrives while a paint is pending, and the stage timings.
  // This characterization pins that contract: two same-tick pushes, one rAF
  // flush, one paint per canvas.
  it('coalesces waterfall rows and spectrum draws to one paint per rAF each', async () => {
    const callbacks: FrameRequestCallback[] = [];
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { callbacks.push(callback); return callbacks.length; });
    vi.stubGlobal('cancelAnimationFrame', vi.fn());
    const resizeCallbacks: ResizeObserverCallback[] = [];
    vi.stubGlobal('ResizeObserver', class {
      constructor(cb: ResizeObserverCallback) { resizeCallbacks.push(cb); }
      observe() {}
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
    vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(function (this: HTMLCanvasElement) {
      return { canvas: this, createImageData: (width: number, height: number) =>
        ({ width, height, data: new Uint8ClampedArray(width * height * 4) }),
        fillRect() {}, drawImage() {}, setTransform() {}, clearRect() {}, putImageData() {},
      } as unknown as CanvasRenderingContext2D;
    });
    const rows = vi.spyOn(WaterfallRenderer.prototype, 'pushRow').mockImplementation(() => {});
    const renders = vi.spyOn(SpectrumRenderer.prototype, 'render').mockImplementation(() => {});
    const trace = new ScopeRenderTrace();
    const target = document.createElement('div'); document.body.appendChild(target);
    let waterfallPush: ((pixels: Uint8Array, options?: WaterfallOptions, token?: TraceToken | null) => void) | undefined;
    let spectrumPush: ((pixels: Uint8Array, token?: TraceToken | null) => void) | undefined;
    const waterfall = mount(WaterfallCanvas, { target, props: { trace, onRegisterPush: (fn) => { waterfallPush = fn; } } });
    const spectrum = mount(SpectrumCanvas, { target, props: { data: null, trace, onRegisterPush: (fn) => { spectrumPush = fn; } } });
    flushSync();
    // MOR-4271: the schedulers only paint a sized, visible surface — give
    // both canvases their size and visibility explicitly.
    for (const cb of resizeCallbacks) {
      cb([{ target, contentRect: { width: 300, height: 150 } } as ResizeObserverEntry], {} as ResizeObserver);
    }
    intersectionCallback?.([{ target, isIntersecting: true } as IntersectionObserverEntry], {} as IntersectionObserver);
    flushSync();
    try {
      const fixture = producerFrames.frames.find(frame => frame.name === 'uncropped')!;
      const parsedFrames = [0, 1].map(() => {
        const bytes = Uint8Array.from(atob(fixture.wireBase64), char => char.charCodeAt(0));
        const parsed = parseCurrentScopeFrame(scopeWire({ source: 'audio_fft', sourceId: 'audio_fft', receiver: null, viewStartHz: fixture.startHz, viewEndHz: fixture.endHz, pixels: bytes.slice(16) }), { source: 'audio_fft', sourceId: 'audio_fft', epoch: 1 });
        if (!parsed.ok || parsed.frame.pixels === null) throw new Error('Producer FFT fixture did not decode');
        return { frame: parsed.frame, pixels: parsed.frame.pixels };
      });
      for (const [index, { frame, pixels }] of parsedFrames.entries()) {
        trace.observe({ kind: 'accepted', frame, token: { source: 'audio_fft', transportEpoch: 1,
          wireSequence: index, acceptedSequence: index + 1 }, callbackAt: performance.now(), acceptedAt: performance.now() });
        const token = trace.tokenForFrame(frame)!;
        waterfallPush?.(pixels, undefined, token);
        spectrumPush?.(pixels, token);
      }
      // Nothing paints synchronously anymore — both canvases await the rAF.
      expect(rows).not.toHaveBeenCalled();
      expect(renders).not.toHaveBeenCalled();
      for (const pending of [...callbacks]) pending(performance.now());
      expect(rows).toHaveBeenCalledTimes(1);
      expect(renders).toHaveBeenCalledTimes(1);
      trace.stop();
      const output = JSON.parse(trace.export()!);
      expect(output.buckets[0].stages.waterfall_push.count).toBe(1);
      expect(output.buckets[0].stages.waterfall_options.count).toBe(1);
      expect(output.buckets[0].stages.spectrum_push.count).toBe(2);
      // Both canvases dropped the first frame: spectrum's rAF coalescing and
      // the waterfall's latest-wins replacement each trace one.
      expect(output.buckets[0].stages.coalesced.count).toBe(2);
      expect(output.buckets[0].stages.spectrum_render.count).toBe(1);
      expect(Object.keys(output.geometry).sort()).toEqual(['spectrum', 'waterfall']);
      expect(output.burst.some((event: { stage: string; acceptedSequence?: number }) =>
        event.stage === 'coalesced' && event.acceptedSequence === 1)).toBe(true);
    } finally {
      await unmount(waterfall); await unmount(spectrum); trace.dispose();
    }
  });
});
