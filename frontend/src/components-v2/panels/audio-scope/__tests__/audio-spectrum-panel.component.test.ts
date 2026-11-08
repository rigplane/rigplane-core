/**
 * MOR-4271 (R2, GREEN-B RED): AudioSpectrumPanel gates its audio-fft demand
 * lease and its `data-streaming` attribute on the CANVAS SURFACE facts
 * (CanvasSurface: IntersectionObserver ∧ !document.hidden) via the canvas's
 * `onSurfaceChange`. On the old code the panel watches only its own
 * IntersectionObserver — `document.hidden` never reaches it — so a hidden
 * document keeps `data-streaming="true"` and holds the lease: this test is
 * RED until GREEN-B lands.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import { ScopeController } from '$lib/runtime/scope-controller.svelte';
import { PresentationResourceHost } from '$lib/runtime/resource-host';
import { IC7300_CAPABILITIES, IC7300_STATE } from '$lib/runtime/adapters/__tests__/fixtures/ic7300-profile';
import type { WsChannel } from '$lib/transport/ws-client';
import AudioSpectrumPanel from '../AudioSpectrumPanel.svelte';

const h = vi.hoisted(() => ({
  scope: null as unknown,
  host: null as unknown as PresentationResourceHost<unknown>,
  state: null as unknown,
  caps: null as unknown,
}));
vi.mock('$lib/runtime/frontend-runtime', async (importOriginal) => {
  const actual = await importOriginal<typeof import('$lib/runtime/frontend-runtime')>();
  return {
    ...actual,
    get presentationResources() { return h.host; },
    runtime: new Proxy(actual.runtime, {
      get(target, key) {
        if (key === 'scope') return h.scope;
        if (key === 'state') return h.state;
        if (key === 'caps') return h.caps;
        return Reflect.get(target, key, target);
      },
    }),
  };
});

function channel() {
  const binary = new Set<(data: ArrayBuffer) => void>();
  return {
    state: 'disconnected', sessionEpoch: 0,
    connect: vi.fn(), disconnect: vi.fn(),
    onBinary: (handler: (data: ArrayBuffer) => void) => {
      binary.add(handler); return () => { binary.delete(handler); };
    },
    onStateChange: () => () => {},
    onSessionTransition: () => () => {},
  };
}

let audio: ReturnType<typeof channel>;
let scope: ScopeController;
let target: HTMLDivElement;
let instance: ReturnType<typeof mount> | undefined;
let intersectionCallbacks: Array<{ element: Element; cb: IntersectionObserverCallback }>;
let hidden: boolean;
let originalHiddenDescriptor: PropertyDescriptor | undefined;

function intersectAll(isIntersecting: boolean): void {
  for (const record of intersectionCallbacks) {
    record.cb([{ target: record.element, isIntersecting } as IntersectionObserverEntry],
      {} as IntersectionObserver);
  }
}

beforeEach(() => {
  localStorage.clear();
  intersectionCallbacks = [];
  hidden = false;
  audio = channel();
  scope = new ScopeController(() => audio as unknown as WsChannel);
  h.scope = scope;
  h.state = structuredClone(IC7300_STATE);
  h.caps = structuredClone(IC7300_CAPABILITIES);
  h.host = new PresentationResourceHost('mor4271');
  h.host.configure('audio-fft', { available: true, selected: true, driver: scope.audioFftDriver });
  scope.registerPresentationDriver(h.host);
  target = document.createElement('div');
  document.body.append(target);
  Object.defineProperty(HTMLElement.prototype, 'setPointerCapture', { configurable: true, value: vi.fn() });
  Object.defineProperty(HTMLElement.prototype, 'releasePointerCapture', { configurable: true, value: vi.fn() });
  vi.stubGlobal('ResizeObserver', class {
    constructor(_cb: ResizeObserverCallback) {}
    observe() {}
    unobserve() {}
    disconnect() {}
  });
  vi.stubGlobal('IntersectionObserver', class {
    constructor(cb: IntersectionObserverCallback) { this.cb = cb; }
    observe(element: Element) { intersectionCallbacks.push({ element, cb: this.cb }); }
    unobserve() {}
    disconnect() {}
    private cb: IntersectionObserverCallback;
  });
  vi.stubGlobal('requestAnimationFrame', () => 1);
  vi.stubGlobal('cancelAnimationFrame', () => {});
  originalHiddenDescriptor = Object.getOwnPropertyDescriptor(document, 'hidden');
  Object.defineProperty(document, 'hidden', { configurable: true, get: () => hidden });
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({
    setTransform: () => {}, clearRect: () => {}, fillRect: () => {}, drawImage: () => {},
    createImageData: (w: number, height: number) => ({ data: new Uint8ClampedArray(w * height * 4), width: w, height }),
    getImageData: (_x: number, _y: number, w: number, height: number) => ({ data: new Uint8ClampedArray(w * height * 4), width: w, height }),
  } as unknown as CanvasRenderingContext2D);
});

afterEach(async () => {
  if (instance) await unmount(instance);
  instance = undefined;
  await h.host.teardown();
  target.remove();
  if (originalHiddenDescriptor) Object.defineProperty(document, 'hidden', originalHiddenDescriptor);
  Reflect.deleteProperty(HTMLElement.prototype, 'setPointerCapture');
  Reflect.deleteProperty(HTMLElement.prototype, 'releasePointerCapture');
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('AudioSpectrumPanel visibility follows the canvas surface facts (MOR-4271)', () => {
  it('a hidden document releases the audio-fft demand lease and clears data-streaming', async () => {
    instance = mount(AudioSpectrumPanel, { target, props: {} });
    flushSync();
    intersectAll(true);
    flushSync();
    await vi.waitFor(() => expect(h.host.snapshot('audio-fft').demand).toBe(1));
    const panel = target.querySelector<HTMLElement>('.audio-spectrum-panel')!;
    expect(panel.dataset.streaming).toBe('true');

    // Hidden: the canvas surface goes not-visible, the panel releases the
    // lease and stops advertising streaming.
    hidden = true;
    document.dispatchEvent(new Event('visibilitychange'));
    flushSync();
    expect(panel.dataset.streaming).toBe('false');
    await vi.waitFor(() => expect(h.host.snapshot('audio-fft').demand).toBe(0));

    // Visible again: the lease is re-acquired from the same surface facts.
    hidden = false;
    document.dispatchEvent(new Event('visibilitychange'));
    flushSync();
    expect(panel.dataset.streaming).toBe('true');
    await vi.waitFor(() => expect(h.host.snapshot('audio-fft').demand).toBe(1));
  });
});
