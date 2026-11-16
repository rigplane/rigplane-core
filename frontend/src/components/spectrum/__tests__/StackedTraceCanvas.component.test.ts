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
import { ScopeRenderTrace, type TraceToken } from '$lib/runtime/scope-render-trace';
import { parseCurrentScopeFrame } from '$lib/runtime/adapters/scope-adapter';
import { scopeWire } from '$lib/runtime/__tests__/scope-wire-fixture';
import { defaultSpectrumOptions, type SpectrumOptions } from '$lib/renderers/spectrum-renderer';

type Push = (pixels: Uint8Array, token?: TraceToken | null) => void;
type Fixture = ReturnType<typeof webglFixture>;

let target: HTMLDivElement;
let component: Record<string, unknown> | undefined;
let frames: Map<number, (now: number) => void>;
let frameId: number;
let gl: Fixture[];
let chromePaints: number;
let chromeLabels: string[];
/** What the 2D contexts stroked and filled, by colour, and how often they clipped (the flat hatch does). */
let flat: { strokes: string[]; fills: string[]; clips: number };
let webglAvailable: boolean;
let resize: (width: number, height: number) => void;

function flushFrames(now: number): void {
  const due = [...frames.values()]; frames.clear();
  for (const frame of due) frame(now);
}

type Locate = import('$lib/renderers/stacked-trace-geometry').StackedTraceLocate;
function mountView(props: { centerHz?: number; trace?: ScopeRenderTrace; patch?: Partial<SpectrumOptions>;
  onLocate?: (locate: Locate | null) => void } = {}) {
  let push: Push = () => {};
  const onUnavailable = vi.fn();
  component = mount(Harness, { target, props: { ...props, onPush: (fn: Push) => { push = fn; }, onUnavailable } });
  flushSync();
  const root = target.querySelector('[data-testid="stacked-trace"]') as HTMLElement;
  const [scene, chrome] = Array.from(root.querySelectorAll('canvas'));
  return { push: (pixels: Uint8Array, token?: TraceToken | null) => push(pixels, token), onUnavailable, root, scene, chrome };
}

beforeEach(() => {
  frames = new Map(); frameId = 0; gl = []; chromePaints = 0; chromeLabels = []; webglAvailable = true;
  flat = { strokes: [], fills: [], clips: 0 };
  target = document.createElement('div');
  document.body.appendChild(target);
  Object.defineProperty(window, 'devicePixelRatio', { configurable: true, value: 1 });
  vi.stubGlobal('requestAnimationFrame', (callback: (now: number) => void) => { frames.set(++frameId, callback); return frameId; });
  vi.stubGlobal('cancelAnimationFrame', (id: number) => { frames.delete(id); });
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
        if (key === 'stroke') return () => { flat.strokes.push(String(context.strokeStyle)); };
        if (key === 'fillRect') return () => { flat.fills.push(String(context.fillStyle)); };
        if (key === 'clip') return () => { flat.clips++; };
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
  fixture.named('uniform3f').filter(call => call.args[0] === 'uFloor').at(-1)?.args[1];

describe('MOR-4463 StackedTraceCanvas', () => {
  it('delivers each pushed frame to the renderer on the next animation frame, latest wins', () => {
    const view = mountView();
    expect(gl).toHaveLength(1);
    flushFrames(0);
    expect(gl[0].named('texSubImage2D')).toHaveLength(0);
    view.push(new Uint8Array(475).fill(20));
    view.push(new Uint8Array(101).fill(40));
    flushFrames(16);
    // Only the latest of the two frames reaches the renderer: 101 samples
    // plus their levels, once as the first history row and once as the live trace.
    const uploads = gl[0].named('texSubImage2D');
    expect(uploads.map(upload => upload.args[4])).toEqual([205, 205]);
    expect((uploads[0].args[8] as Uint8Array)[0]).toBe(40);
    flushSync();
    expect(view.root.getAttribute('data-history-rows')).toBe('1');
    // The stack keeps painting display frames while its rows glide, then rests.
    expect(frames.size).toBe(1);
    flushFrames(500);
    expect(frames.size).toBe(0);
  });

  it('paints the chrome of the flat renderer over the scene, for whatever view it is given', async () => {
    mountView();
    expect(chromePaints).toBeGreaterThan(0);
    expect(chromeLabels).toContain('14.050');
    const before = chromePaints;
    chromeLabels = [];
    await unmount(component!); component = undefined;
    mountView({ centerHz: 14_200_000 });
    expect(chromePaints).toBeGreaterThan(before);
    expect(chromeLabels).toContain('14.200');
    expect(chromeLabels).not.toContain('14.050');
  });

  /** The uniform calls of the latest floor draw: from its program to its quad. */
  const floorUniforms = (fixture: Fixture) => {
    let quad = fixture.calls.length - 1;
    while (quad > 0 && !(fixture.calls[quad].name === 'drawArrays' && fixture.calls[quad].args[2] === 4)) quad--;
    let start = quad;
    while (start > 0 && fixture.calls[start].name !== 'useProgram') start--;
    return fixture.calls.slice(start, quad);
  };

  it('draws the marker, the passband and the hatch inside the scene and leaves the flat renderer the labels', () => {
    const view = mountView({ patch: { coverage: { startHz: 14_010_000, endHz: 14_080_000 } } });
    view.push(new Uint8Array(475).fill(20));
    flushFrames(16);
    // Nothing of the flat marks is painted in 2D: they would stand upright over a scene in perspective.
    expect(flat.strokes).not.toContain(defaultSpectrumOptions.tuneLineColor);
    expect(flat.strokes).not.toContain(defaultSpectrumOptions.passbandEdgeColor);
    expect(flat.fills).not.toContain(defaultSpectrumOptions.passbandFillColor);
    expect(flat.clips).toBe(0);
    expect(chromeLabels).toContain('14.050');
    // USB, 2.4 kHz of a 100 kHz view, tuned at its centre; samples from 10 % to 80 % of it.
    const floor = floorUniforms(gl[0]);
    const mark = floor.find(call => call.name === 'uniform3f' && call.args[0] === 'uMark')!.args.slice(1) as number[];
    expect(mark[0]).toBe(0.5);
    expect(mark[1]).toBeCloseTo(0.5, 12);
    expect(mark[2]).toBeCloseTo(0.524, 12);
    const cover = floor.find(call => call.name === 'uniform2f' && call.args[0] === 'uHatch')!.args.slice(1) as number[];
    expect(cover[0]).toBeCloseTo(0.1, 12);
    expect(cover[1]).toBeCloseTo(0.8, 12);
    // Every row gets the same marks as the floor.
    const marks = gl[0].named('uniform3f').filter(call => call.args[0] === 'uMark');
    expect(marks.length).toBeGreaterThanOrEqual(2);
    for (const call of marks) expect(call.args.slice(1)).toEqual(mark);
  });

  it.each([
    ['rgba(239,68,68,0.75)', [239 / 255, 68 / 255, 68 / 255, 0.75]],
    ['rgba(12, 34, 56, 0.5)', [12 / 255, 34 / 255, 56 / 255, 0.5]],
    ['#ff8000', [1, 128 / 255, 0, 1]],
    ['rgb(1, 2, 3)', [1 / 255, 2 / 255, 3 / 255, 1]],
  ])('gives the scene the marker colour of the flat view: %s', (css, rgba) => {
    const view = mountView({ patch: { tuneLineColor: css, passbandFillColor: 'rgba(59,130,246,0.15)' } });
    view.push(new Uint8Array(475).fill(20));
    flushFrames(16);
    const sent = (name: string) => Array.from(gl[0].named('uniform4fv').find(call => call.args[0] === name)!.args[1] as Float32Array);
    sent('uTuneColor').forEach((value, index) => expect(value).toBeCloseTo(rgba[index], 6));
    sent('uBandFill').forEach((value, index) => expect(value).toBeCloseTo([59 / 255, 130 / 255, 246 / 255, 0.15][index], 6));
  });

  it('hands the host a pointer map that answers from the scene, and takes it back on unmount', async () => {
    const maps: (Locate | null)[] = [];
    const view = mountView({ onLocate: locate => maps.push(locate) });
    expect(maps).toHaveLength(1);
    const locate = maps[0]!;
    view.push(new Uint8Array(475).fill(40));
    flushFrames(16);
    // On the front row a pointer's x is its frequency; high in the 400 x 200
    // pane it is on the last row, which is 1 / 2.5 of the pane wide.
    expect(locate(0.3, 0.8)).toBeCloseTo(0.3, 12);
    expect(locate(0.4, 0.3)).toBeCloseTo(0.25, 9);
    view.scene.dispatchEvent(new Event('webglcontextlost'));
    expect(locate(0.4, 0.3)).toBeNull();
    await unmount(component!); component = undefined;
    expect(maps).toHaveLength(2);
    expect(maps[1]).toBeNull();
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
    const trace = new ScopeRenderTrace();
    try {
      const view = mountView({ trace });
      flushFrames(0);
      // Two frames through the production parser, accepted by the real trace.
      const tokens = [0, 1].map((index) => {
        const parsed = parseCurrentScopeFrame(scopeWire({ source: 'audio_fft', sourceId: 'audio_fft', receiver: null,
          viewStartHz: 0, viewEndHz: 3_000, pixels: new Uint8Array(475).fill(20 + index) }),
          { source: 'audio_fft', sourceId: 'audio_fft', epoch: 1 });
        if (!parsed.ok || parsed.frame.pixels === null) throw new Error('scope fixture did not decode');
        trace.observe({ kind: 'accepted', frame: parsed.frame, token: { source: 'audio_fft', transportEpoch: 1,
          wireSequence: index, acceptedSequence: index + 1 }, callbackAt: performance.now(), acceptedAt: performance.now() });
        return { pixels: parsed.frame.pixels, token: trace.tokenForFrame(parsed.frame)! };
      });
      for (const { pixels, token } of tokens) view.push(pixels, token);
      flushFrames(16);
      trace.stop();
      const output = JSON.parse(trace.export()!);
      const stages = output.buckets[0].stages;
      // The first frame was replaced before it was painted; the second was painted.
      expect(stages.coalesced.count).toBe(1);
      expect(stages.coalesced.firstSequence).toBe(1);
      expect(output.burst.some((event: { stage: string; acceptedSequence?: number }) =>
        event.stage === 'coalesced' && event.acceptedSequence === 1)).toBe(true);
      expect(stages.spectrum_render.lastSequence).toBe(2);
      expect(output.geometry.spectrum).toBeDefined();
    } finally { trace.dispose(); }
  });

  it('releases the renderer and stops scheduling on unmount', async () => {
    const view = mountView();
    view.push(new Uint8Array(475).fill(20));
    flushFrames(16);
    // The stack is gliding: one display frame is pending, and unmount cancels it.
    expect(frames.size).toBe(1);
    await unmount(component!); component = undefined;
    expect(frames.size).toBe(0);
    expect(gl[0].named('deleteProgram')).toHaveLength(2);
    expect(gl[0].named('deleteTexture').length).toBeGreaterThanOrEqual(1);
    gl[0].calls.length = 0;
    flushFrames(32);
    expect(gl[0].calls).toHaveLength(0);
    expect(frames.size).toBe(0);
  });
});
