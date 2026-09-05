import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { ScopeController } from '$lib/runtime/scope-controller.svelte';
import { PresentationResourceHost } from '$lib/runtime/resource-host';
import { setCapabilities, clearCapabilities, getCapabilities } from '$lib/stores/capabilities.svelte';
import { radio } from '$lib/stores/radio.svelte';
import { IC7300_CAPABILITIES, IC7300_STATE } from '$lib/runtime/adapters/__tests__/fixtures/ic7300-profile';
import { ManagedAppTxHarness } from '$lib/runtime/tx-controller/__tests__/support/managed-app-tx-harness';
import { SpectrumRenderer, defaultSpectrumOptions } from '$lib/renderers/spectrum-renderer';
import { WaterfallRenderer } from '$lib/renderers/waterfall-renderer';
import type { WsChannel } from '$lib/transport/ws-client';
import producerFixtures from './fixtures/audio-fft-producer-frames.json';

const h = vi.hoisted(() => ({ scope: null as unknown, host: null as unknown }));
vi.mock('$lib/runtime/frontend-runtime', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/runtime/frontend-runtime')>();
  return {
    ...actual,
    get presentationResources() { return h.host; },
    runtime: new Proxy(actual.runtime, {
      get(target, key) {
        if (key === 'scope') return h.scope;
        if (key === 'acquireHardwareScope') return (consumer: string) =>
          (h.host as PresentationResourceHost<unknown>).acquire('hardware-scope', consumer);
        if (key === 'releaseHardwareScope') return (lease: Parameters<PresentationResourceHost<unknown>['release']>[0]) =>
          (h.host as PresentationResourceHost<unknown>).release(lease);
        return Reflect.get(target, key, target);
      },
    }),
  };
});
const txHarness = new ManagedAppTxHarness({ stale: true });
vi.mock('$lib/runtime/tx-controller/managed-app-host', async (importOriginal) => ({
  ...await importOriginal<typeof import('$lib/runtime/tx-controller/managed-app-host')>(),
  getManagedAppTxController: () => txHarness.controller,
}));
import RadioLayout from '../RadioLayout.svelte';
import SpectrumPanel from '../../../components/spectrum/SpectrumPanel.svelte';
import * as transport from '$lib/transport/ws-client';
import { getVfoHandlers, getFilterHandlers } from '$lib/runtime/adapters/panel-adapters';

function channel() {
  const binary = new Set<(data: ArrayBuffer) => void>();
  return {
    state: 'connected', sessionEpoch: 1,
    connect: vi.fn(), disconnect: vi.fn(),
    onBinary: (handler: (data: ArrayBuffer) => void) => {
      binary.add(handler); return () => { binary.delete(handler); };
    },
    onStateChange: () => () => {}, onSessionTransition: () => () => {},
    fire: (data: ArrayBuffer) => { for (const handler of binary) handler(data); },
    handlerCount: () => binary.size,
  };
}
function producerFrame(name = 'uncropped') {
  const fixture = producerFixtures.frames.find(frame => frame.name === name)!;
  const data = Uint8Array.from(atob(fixture.wireBase64), char => char.charCodeAt(0)).buffer;
  return { data, pixels: new Uint8Array(data, 16), fixture };
}
function frame(start = 14176000, end = 14200000, pixels = new Uint8Array([0, 16, 48, 80, 24, 8, 0, 32])) {
  const data = new ArrayBuffer(16 + pixels.length);
  const view = new DataView(data);
  view.setUint8(0, 1);
  view.setUint32(3, start, true); view.setUint32(7, end, true);
  view.setUint16(14, pixels.length, true);
  new Uint8Array(data, 16).set(pixels);
  return { data, pixels };
}
let host: PresentationResourceHost<unknown>;
let scope: ScopeController;
let audio: ReturnType<typeof channel>;
let hardware: ReturnType<typeof channel>;
let instance: ReturnType<typeof mount> | undefined;
let target: HTMLDivElement;
let raf: Map<number, FrameRequestCallback>;
let nextRaf: number;
const context = new Proxy({
  canvas: { width: 640, height: 240 },
  lineTo: vi.fn(),
  putImageData: vi.fn(),
  createImageData: (w: number, height: number) => ({ data: new Uint8ClampedArray(w * height * 4), width: w, height }),
  getImageData: (_x: number, _y: number, w: number, height: number) => ({ data: new Uint8ClampedArray(w * height * 4), width: w, height }),
  createLinearGradient: () => ({ addColorStop: () => {} }),
  measureText: () => ({ width: 20 }),
}, { get: (obj, key) => Reflect.get(obj, key) ?? (() => {}) });

function select(audioFft: boolean, generation = 1) {
  setCapabilities({ ...IC7300_CAPABILITIES, providerGeneration: generation,
    scope: !audioFft, scopeSource: audioFft ? 'audio_fft' : 'hardware', audioFftAvailable: true });
  radio.current = { ...IC7300_STATE, providerGeneration: generation };
  host.configure('hardware-scope', { available: !audioFft, selected: !audioFft, driver: scope.hardwareScopeDriver });
  scope.registerPresentationDriver(host);
  host.configure('audio-fft', { available: true, selected: true });
  flushSync();
}
function draw() {
  flushSync();
  const tasks = [...raf.values()]; raf.clear();
  for (const callback of tasks) callback(0);
}
function mountPanel(layout = true) {
  instance = layout ? mount(RadioLayout, { target, props: { skinId: 'sdr-test' } }) : mount(SpectrumPanel, { target });
  flushSync();
}

beforeEach(() => {
  localStorage.clear();
  context.lineTo.mockClear(); context.putImageData.mockClear();
  Object.defineProperty(HTMLElement.prototype, 'setPointerCapture', { configurable: true, value: vi.fn() });
  Object.defineProperty(HTMLElement.prototype, 'releasePointerCapture', { configurable: true, value: vi.fn() });
  vi.stubGlobal('PointerEvent', class extends MouseEvent { pointerId = 1; pointerType = 'mouse'; });
  Object.defineProperty(window, 'innerWidth', { configurable: true, value: 1440 });
  Object.defineProperty(window, 'innerHeight', { configurable: true, value: 900 });
  Object.defineProperty(document, 'hidden', { configurable: true, value: false });
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(() => context as unknown as CanvasRenderingContext2D);
  vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockReturnValue({ x: 0, y: 0, left: 0, top: 0, right: 640, bottom: 240, width: 640, height: 240, toJSON: () => ({}) });
  vi.stubGlobal('ResizeObserver', class {
    constructor(private callback: ResizeObserverCallback) {}
    observe(element: Element) { this.callback([{ target: element, contentRect: element.getBoundingClientRect() } as ResizeObserverEntry], this as unknown as ResizeObserver); }
    unobserve() {} disconnect() {}
  });
  raf = new Map(); nextRaf = 0;
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => { raf.set(++nextRaf, callback); return nextRaf; });
  vi.stubGlobal('cancelAnimationFrame', (id: number) => { raf.delete(id); });
  audio = channel(); hardware = channel();
  scope = new ScopeController(name => (name === 'scope' ? hardware : audio) as unknown as WsChannel);
  host = new PresentationResourceHost('mor2355'); h.host = host; h.scope = scope;
  target = document.createElement('div'); document.body.append(target);
  txHarness.reset({ stale: true });
});
afterEach(async () => {
  if (instance) await unmount(instance); instance = undefined;
  await host.teardown(); clearCapabilities(); radio.current = null;
  target.remove(); vi.restoreAllMocks(); vi.unstubAllGlobals();
  Reflect.deleteProperty(HTMLElement.prototype, 'setPointerCapture');
  Reflect.deleteProperty(HTMLElement.prototype, 'releasePointerCapture');
});

describe('MOR-2355 central default scope source', () => {
  it('renders parsed audio FFT in the real SDR center with AF bounds and no RF actions', async () => {
    select(true);
    const render = vi.spyOn(SpectrumRenderer.prototype, 'render');
    const push = vi.spyOn(WaterfallRenderer.prototype, 'pushRow');
    const options = vi.spyOn(WaterfallRenderer.prototype, 'updateOptions');
    const tune = vi.spyOn(getVfoHandlers(), 'onFreqChange');
    const filter = vi.spyOn(getFilterHandlers(), 'onFilterWidthCommit');
    const send = vi.spyOn(transport, 'sendCommand');
    mountPanel();
    const panel = target.querySelector('.content-center .spectrum-panel');
    expect(panel).not.toBeNull();
    await vi.waitFor(() => expect(audio.connect).toHaveBeenCalledWith('/api/v1/audio-scope'));
    expect(hardware.connect).not.toHaveBeenCalled();
    expect(host.snapshot('hardware-scope').demand).toBe(0);
    expect(host.snapshot('audio-fft').demand).toBe(2);
    expect(audio.connect).toHaveBeenCalledTimes(1);
    const input = producerFrame();
    expect(input.pixels).toHaveLength(2049);
    expect(input.fixture.startHz).toBe(14176000);
    const expectedPixels = input.pixels.slice(1024).map(value => Math.round(value / 2));
    audio.fire(input.data); draw();
    expect(scope.audioScopeFrame?.startFreq).toBe(14176000);
    expect(scope.audioScopeFrame?.pixels).toHaveLength(2049);
    expect(Math.max(...scope.audioScopeFrame!.pixels)).toBe(160);
    expect(render).toHaveBeenCalledWith(expect.anything(), expectedPixels, 640, 240,
      expect.objectContaining({ spanHz: 12000, centerHz: 6000, showRfOverlays: false, tuneHz: 0, passbandHz: 0 }));
    expect(push).toHaveBeenCalledWith(expectedPixels);
    expect(options).toHaveBeenLastCalledWith(expect.objectContaining({ centerHz: 6000, spanHz: 12000 }));
    expect(panel!.textContent).toContain('Audio FFT · AF');
    expect(panel!.querySelector('.freq-axis')!.textContent).toContain('0 kHz');
    expect(panel!.querySelector('.freq-axis')!.textContent).toContain('12 kHz');
    expect(panel!.querySelector('.spectrum-toolbar')).toBeNull();
    expect(panel!.querySelector('.passband-resize-zone, .tune-line, .span-indicators')).toBeNull();
    panel!.dispatchEvent(new WheelEvent('wheel', { deltaY: 100, bubbles: true }));
    const area = panel!.querySelector('.waterfall-content')!;
    area.dispatchEvent(new PointerEvent('pointerdown', { button: 0, clientX: 100, bubbles: true }));
    window.dispatchEvent(new PointerEvent('pointermove', { clientX: 180, bubbles: true }));
    window.dispatchEvent(new PointerEvent('pointerup', { clientX: 180, bubbles: true }));
    const canvas = area.querySelector('canvas')!;
    canvas.dispatchEvent(new PointerEvent('pointerdown', { button: 0, clientX: 100, bubbles: true }));
    canvas.dispatchEvent(new PointerEvent('pointerup', { button: 0, clientX: 100, bubbles: true }));
    const toggle = panel!.querySelector<HTMLButtonElement>('[aria-label="Scope viewer"]')!;
    toggle.click(); flushSync();
    expect(host.snapshot('audio-fft').demand).toBe(1);
    expect(panel!.textContent).toContain('Scope viewer OFF');
    toggle.click(); flushSync();
    expect(host.snapshot('audio-fft').demand).toBe(2);
    expect(audio.connect).toHaveBeenCalledTimes(1);
    expect(tune).not.toHaveBeenCalled(); expect(filter).not.toHaveBeenCalled(); expect(send).not.toHaveBeenCalled();
    await unmount(instance!); instance = undefined;
    await vi.waitFor(() => expect(host.snapshot('audio-fft').demand).toBe(0));
    expect(audio.handlerCount()).toBe(0);
  });

  it('uses frame-local positive-frequency bins for cropped and recentered producer output', async () => {
    select(true); mountPanel(false);
    const render = vi.spyOn(SpectrumRenderer.prototype, 'render');
    const push = vi.spyOn(WaterfallRenderer.prototype, 'pushRow');
    await vi.waitFor(() => expect(audio.connect).toHaveBeenCalledTimes(1));
    for (const [name, count, span, center] of [
      ['uncropped', 1025, 12000, 6000],
      ['recentered', 1025, 12000, 6000],
      ['cropped', 257, 3000, 1500],
    ] as const) {
      const input = producerFrame(name);
      audio.fire(input.data); draw();
      const projected = render.mock.calls.at(-1)![1];
      expect(projected).toHaveLength(count);
      expect(projected).toEqual(input.pixels.slice((input.pixels.length - 1) / 2).map(value => Math.round(value / 2)));
      expect(render.mock.calls.at(-1)![4]).toMatchObject({ spanHz: span, centerHz: center });
      expect(push).toHaveBeenLastCalledWith(projected);
      expect(target.querySelector('.freq-axis')!.textContent).toContain(`${span / 1000} kHz`);
      expect(projected[64]).toBeGreaterThan(projected[40]);
      expect(projected[192]).toBeGreaterThan(projected[160]);
    }
  });

  it('preserves distinct 80/120/160 producer levels in the trace, peak hold and waterfall', async () => {
    select(true); mountPanel(false);
    const render = vi.spyOn(SpectrumRenderer.prototype, 'render');
    const push = vi.spyOn(WaterfallRenderer.prototype, 'pushRow');
    await vi.waitFor(() => expect(audio.connect).toHaveBeenCalledTimes(1));
    const input = frame(14176000, 14200000, new Uint8Array([160, 120, 80, 0, 80, 120, 160]));
    audio.fire(input.data); draw();
    const projected = render.mock.calls.at(-1)![1];
    expect(projected).toEqual(new Uint8Array([0, 40, 60, 80]));
    expect(push).toHaveBeenLastCalledWith(projected);
    const row = context.putImageData.mock.calls.at(-1)![0] as ImageData;
    const rgb = (x: number) => Array.from(row.data.slice(x * 4, x * 4 + 3));
    expect(new Set([rgb(160), rgb(320), rgb(480)].map(value => value.join(','))).size).toBe(3);

    const renderer = new SpectrumRenderer();
    renderer.setAvgEnabled(false);
    renderer.setPeakHoldEnabled(true);
    context.lineTo.mockClear();
    renderer.render(context as unknown as CanvasRenderingContext2D, projected, 4, 100,
      { ...defaultSpectrumOptions, showRfOverlays: false });
    const positiveTraceY = context.lineTo.mock.calls.slice(-7, -4).map(call => call[1]);
    expect(positiveTraceY[0]).toBeGreaterThan(positiveTraceY[1]);
    expect(positiveTraceY[1]).toBeGreaterThan(positiveTraceY[2]);
    context.lineTo.mockClear();
    renderer.render(context as unknown as CanvasRenderingContext2D, new Uint8Array(4), 4, 100,
      { ...defaultSpectrumOptions, showRfOverlays: false });
    const peakYs = context.lineTo.mock.calls.slice(-4).map(call => call[1]);
    expect(new Set(peakYs).size).toBe(4);
  });

  it('preserves hardware rendering and switches source with one demand and clean canvases', async () => {
    select(false); mountPanel(false);
    const push = vi.spyOn(WaterfallRenderer.prototype, 'pushRow');
    await vi.waitFor(() => expect(hardware.connect).toHaveBeenCalledWith('/api/v1/scope'));
    expect(audio.connect).not.toHaveBeenCalled();
    const input = frame(14100000, 14200000); hardware.fire(input.data); draw();
    expect(push).toHaveBeenCalledWith(input.pixels);
    expect(target.querySelector('.spectrum-toolbar')).not.toBeNull();
    target.querySelector<HTMLButtonElement>('[title="Toggle fullscreen"]')!.click(); flushSync();
    expect(target.querySelector('.spectrum-panel.fullscreen')).not.toBeNull();
    const original = target.querySelector('canvas');
    select(true, 2);
    await vi.waitFor(() => expect(audio.connect).toHaveBeenCalledTimes(1));
    expect(host.snapshot('hardware-scope').demand).toBe(0);
    expect(host.snapshot('audio-fft').demand).toBe(1);
    expect(hardware.handlerCount()).toBe(0);
    expect(target.querySelector('canvas')).not.toBe(original);
    expect(target.querySelector('.spectrum-panel.fullscreen')).toBeNull();
    expect(target.querySelector('.freq-axis')).toBeNull();
    audio.fire(producerFrame().data); draw();
    expect(target.textContent).toContain('12 kHz');
    select(false, 3);
    await vi.waitFor(() => expect(audio.disconnect).toHaveBeenCalledTimes(1));
    expect(host.snapshot('audio-fft').demand).toBe(0);
    expect(host.snapshot('hardware-scope').demand).toBe(1);
    expect(audio.handlerCount()).toBe(0);
    await unmount(instance!); instance = undefined;
    await vi.waitFor(() => expect(host.snapshot('hardware-scope').demand).toBe(0));
    expect(hardware.handlerCount()).toBe(0);
    expect(getCapabilities()?.scopeSource).toBe('hardware');
  });
});
