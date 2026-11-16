/**
 * MOR-4463: `StackedTraceCanvas` on a real `CanvasSurface` and a real
 * `ScopeRenderScheduler`, with WebGL2 and the 2D context faked: frame
 * delivery, chrome, resizing, pixel-scale change, fallback and disposal.
 */
import { flushSync, mount, unmount } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { webglFixture } from '$lib/renderers/__tests__/webgl-fixture';
import { RecordedPath } from '$lib/renderers/__tests__/canvas-layers-fixture';
import Harness from './StackedTraceCanvasHarness.svelte';

type Push = (pixels: Uint8Array, token?: unknown) => void;
type Fixture = ReturnType<typeof webglFixture>;

let target: HTMLDivElement;
let component: Record<string, unknown> | undefined;
let frames: ((now: number) => void)[];
let gl: Fixture[];
let chromePaints: number;
let chromeLabels: string[];
let webglAvailable: boolean;
let resize: (width: number, height: number) => void;

function flushFrames(now: number): void {
  const due = frames; frames = [];
  for (const frame of due) frame(now);
}

function mountView(props: { centerHz?: number; trace?: unknown } = {}) {
  let push: Push = () => {};
  const onUnavailable = vi.fn();
  component = mount(Harness, { target, props: { ...props, onPush: (fn: Push) => { push = fn; }, onUnavailable } as never });
  flushSync();
  const root = target.querySelector('[data-testid="stacked-trace"]') as HTMLElement;
  const [scene, chrome] = Array.from(root.querySelectorAll('canvas'));
  return { push: (pixels: Uint8Array, token?: unknown) => push(pixels, token), onUnavailable, root, scene, chrome };
}

beforeEach(() => {
  frames = []; gl = []; chromePaints = 0; chromeLabels = []; webglAvailable = true;
  target = document.createElement('div');
  document.body.appendChild(target);
  Object.defineProperty(window, 'devicePixelRatio', { configurable: true, value: 1 });
  vi.stubGlobal('requestAnimationFrame', (callback: (now: number) => void) => frames.push(callback));
  vi.stubGlobal('cancelAnimationFrame', () => {});
  vi.stubGlobal('Path2D', RecordedPath);
  vi.stubGlobal('ResizeObserver', class {
    constructor(private readonly callback: ResizeObserverCallback) {}
    observe(element: Element): void {
      resize = (width, height) => this.callback(
        [{ target: element, contentRect: { width, height } } as unknown as ResizeObserverEntry],
        this as unknown as ResizeObserver);
      resize(400, 200);
    }
    disconnect(): void {}
  });
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockImplementation(function (this: HTMLCanvasElement, kind: string) {
    if (kind === 'webgl2') {
      if (!webglAvailable) return null;
      const fixture = webglFixture(this.width, this.height);
      gl.push(fixture);
      return fixture.gl;
    }
    const owner = this;
    const isChrome = () => owner.classList.contains('chrome');
    return new Proxy({ canvas: owner } as Record<string, unknown>, {
      get(context, key: string) {
        if (key in context) return context[key];
        if (key === 'createLinearGradient') return () => ({ addColorStop: () => {} });
        if (key === 'measureText') return () => ({ width: 10 });
        // renderSpectrum composes its cached layers onto the visible canvas.
        if (key === 'clearRect') return () => { if (isChrome()) chromePaints++; };
        if (key === 'fillText') return (text: string) => { chromeLabels.push(text); };
        return () => {};
      },
      set(context, key: string, value) { context[key] = value; return true; },
    }) as unknown as CanvasRenderingContext2D;
  });
});

afterEach(async () => {
  if (component) await unmount(component);
  component = undefined;
  target.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const floorInset = (fixture: Fixture) =>
  fixture.named('uniform2f').filter(call => call.args[0] === 'uFloor').at(-1)?.args[1];

describe('MOR-4463 StackedTraceCanvas', () => {
  it('delivers each pushed frame to the renderer on the next animation frame, latest wins', () => {
    const view = mountView();
    expect(gl).toHaveLength(1);
    flushFrames(0);
    expect(gl[0].named('texSubImage2D')).toHaveLength(0);
    view.push(new Uint8Array(475).fill(20));
    view.push(new Uint8Array(101).fill(40));
    flushFrames(16);
    const uploads = gl[0].named('texSubImage2D');
    expect(uploads).toHaveLength(1);
    expect((uploads[0].args[8] as Uint8Array)[0]).toBe(40);
    flushSync();
    expect(view.root.getAttribute('data-history-rows')).toBe('1');
    // The stack keeps painting display frames while its rows glide, then rests.
    expect(frames.length).toBe(1);
    flushFrames(500);
    expect(frames.length).toBe(0);
  });

  it('paints the chrome of the flat renderer over the scene, for whatever view it is given', async () => {
    mountView();
    expect(chromePaints).toBeGreaterThan(0);
    expect(chromeLabels).toContain('14.050');
    const before = chromePaints;
    chromeLabels = [];
    await unmount(component!); component = undefined;
    mountView({ centerHz: 14_060_000 });
    expect(chromePaints).toBeGreaterThan(before);
    expect(chromeLabels).toContain('14.060');
  });

  it('follows a resize: both canvases take the new backing size, chrome and scene repaint', () => {
    const view = mountView();
    view.push(new Uint8Array(475).fill(20));
    flushFrames(16); flushFrames(500);
    expect([view.scene.width, view.scene.height, view.chrome.width, view.chrome.height]).toEqual([400, 200, 400, 200]);
    const chromeBefore = chromePaints, drawsBefore = gl[0].named('drawArrays').length;
    resize(600, 300);
    expect([view.scene.width, view.scene.height, view.chrome.width, view.chrome.height]).toEqual([600, 300, 600, 300]);
    expect(chromePaints).toBeGreaterThan(chromeBefore);
    flushFrames(600);
    expect(gl[0].named('drawArrays').length).toBeGreaterThan(drawsBefore);
  });

  it('repaints chrome and rescales the scene when only the pixel scale changes', () => {
    const view = mountView();
    view.push(new Uint8Array(475).fill(20));
    flushFrames(16); flushFrames(500);
    expect(floorInset(gl[0])).toBe(16);
    const chromeBefore = chromePaints;
    // Half the CSS size at twice the pixel ratio: the backing size is unchanged.
    Object.defineProperty(window, 'devicePixelRatio', { configurable: true, value: 2 });
    resize(200, 100);
    expect([view.scene.width, view.scene.height]).toEqual([400, 200]);
    expect(chromePaints).toBeGreaterThan(chromeBefore);
    flushFrames(600);
    expect(floorInset(gl[0])).toBe(32);
  });

  it('reports the view unavailable when the browser gives no WebGL2 context', () => {
    webglAvailable = false;
    const view = mountView();
    expect(view.onUnavailable).toHaveBeenCalledTimes(1);
    view.push(new Uint8Array(475).fill(20));
    expect(() => flushFrames(16)).not.toThrow();
  });

  it('reports the view unavailable when the renderer fails while painting', () => {
    const view = mountView();
    // The ring cannot be widened for the first real frame on this device.
    (gl[0].gl as unknown as Record<string, unknown>).checkFramebufferStatus = () => 0;
    view.push(new Uint8Array(475).fill(20));
    expect(() => flushFrames(16)).not.toThrow();
    expect(view.onUnavailable).toHaveBeenCalledTimes(1);
    expect(gl[0].named('deleteProgram').length).toBe(2);
    gl[0].calls.length = 0;
    view.push(new Uint8Array(475).fill(20));
    flushFrames(32);
    expect(gl[0].calls).toHaveLength(0);
  });

  it('reports the view unavailable when the WebGL context is lost', () => {
    const view = mountView();
    view.push(new Uint8Array(475).fill(20));
    flushFrames(16);
    view.scene.dispatchEvent(new Event('webglcontextlost'));
    expect(view.onUnavailable).toHaveBeenCalledTimes(1);
    gl[0].calls.length = 0;
    view.push(new Uint8Array(475).fill(20));
    flushFrames(32);
    expect(gl[0].calls).toHaveLength(0);
  });

  it('records geometry, paints and coalesced frames on the host render trace', () => {
    const trace = { status: 'recording', record: vi.fn(), recordGeometry: vi.fn() };
    const view = mountView({ trace });
    flushFrames(0);
    trace.record.mockClear(); trace.recordGeometry.mockClear();
    const first = { id: 1 }, second = { id: 2 };
    view.push(new Uint8Array(475).fill(20), first);
    view.push(new Uint8Array(475).fill(40), second);
    expect(trace.record).toHaveBeenCalledWith('coalesced', first, 0, true);
    flushFrames(16);
    expect(trace.recordGeometry).toHaveBeenCalledWith('spectrum', 400, 200, 400, 200, 1);
    const renders = trace.record.mock.calls.filter(call => call[0] === 'spectrum_render');
    expect(renders).toHaveLength(1);
    expect(renders[0][1]).toBe(second);
    expect(renders[0][2]).toBeGreaterThanOrEqual(0);
  });

  it('releases the renderer and stops scheduling on unmount', async () => {
    const view = mountView();
    view.push(new Uint8Array(475).fill(20));
    flushFrames(16);
    await unmount(component!); component = undefined;
    expect(gl[0].named('deleteProgram')).toHaveLength(2);
    expect(gl[0].named('deleteTexture').length).toBeGreaterThanOrEqual(1);
    gl[0].calls.length = 0;
    flushFrames(32);
    expect(gl[0].calls).toHaveLength(0);
    expect(frames).toHaveLength(0);
  });
});
