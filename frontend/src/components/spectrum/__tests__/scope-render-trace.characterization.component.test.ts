import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { ScopeRenderTrace } from '$lib/runtime/scope-render-trace';
import { WaterfallRenderer } from '$lib/renderers/waterfall-renderer';
import { SpectrumRenderer } from '$lib/renderers/spectrum-renderer';
import type { WaterfallOptions } from '$lib/renderers/waterfall-renderer';
import type { TraceToken } from '$lib/runtime/scope-render-trace';
import WaterfallCanvas from '../WaterfallCanvas.svelte';
import SpectrumCanvas from '../SpectrumCanvas.svelte';

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); document.body.innerHTML = ''; });

describe('actual canvas trace callbacks', () => {
  it('counts synchronous rows separately from coalesced latest-token RAF draws', async () => {
    const callbacks: FrameRequestCallback[] = [];
    vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { callbacks.push(callback); return callbacks.length; });
    vi.stubGlobal('cancelAnimationFrame', vi.fn());
    vi.stubGlobal('ResizeObserver', class { observe() {} disconnect() {} });
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
    try {
      const first = Object.freeze({ receiver: 0, mode: 0, startFreq: 1, endFreq: 2, pixels: new Uint8Array(2049) });
      const second = Object.freeze({ ...first, pixels: new Uint8Array(2049) });
      for (const [index, frame] of [first, second].entries()) {
        trace.observe({ kind: 'accepted', frame, token: { source: 'audio_fft', transportEpoch: 1,
          wireSequence: index, acceptedSequence: index + 1 }, callbackAt: performance.now(), acceptedAt: performance.now() });
        const token = trace.tokenForFrame(frame)!;
        waterfallPush?.(frame.pixels, undefined, token);
        spectrumPush?.(frame.pixels, token);
      }
      expect(rows).toHaveBeenCalledTimes(2);
      expect(renders).not.toHaveBeenCalled();
      const pending = callbacks.at(-1)!;
      pending(performance.now());
      expect(renders).toHaveBeenCalledTimes(1);
      trace.stop();
      const output = JSON.parse(trace.export()!);
      expect(output.buckets[0].stages.waterfall_push.count).toBe(2);
      expect(output.buckets[0].stages.waterfall_options.count).toBe(2);
      expect(output.buckets[0].stages.spectrum_push.count).toBe(2);
      expect(output.buckets[0].stages.coalesced.count).toBe(1);
      expect(output.buckets[0].stages.spectrum_render.count).toBe(1);
      expect(Object.keys(output.geometry).sort()).toEqual(['spectrum', 'waterfall']);
      expect(output.burst.some((event: { stage: string; acceptedSequence?: number }) =>
        event.stage === 'coalesced' && event.acceptedSequence === 1)).toBe(true);
    } finally {
      await unmount(waterfall); await unmount(spectrum); trace.dispose();
    }
  });
});
