import { flushSync, mount, unmount } from 'svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import AmberAfScope from '../AmberAfScope.svelte';

type Mode = 'compact' | 'fill' | 'dominant';
type Point = [number, number];
const width = 320;
const height = 160;
let component: ReturnType<typeof mount> | undefined;
let target: HTMLDivElement;
let nextFrame: FrameRequestCallback | undefined;
let path: Point[];
let strokes: Point[][];
let bars: number[][];
let labels: string[];

beforeEach(() => {
  path = [];
  strokes = [];
  bars = [];
  labels = [];
  nextFrame = undefined;
  target = document.createElement('div');
  document.body.appendChild(target);
  vi.spyOn(Math, 'random').mockReturnValue(0.9);
  vi.stubGlobal('requestAnimationFrame', (callback: FrameRequestCallback) => {
    nextFrame = callback;
    return 1;
  });
  vi.stubGlobal('cancelAnimationFrame', () => { nextFrame = undefined; });
  vi.stubGlobal('ResizeObserver', class {
    constructor(private callback: ResizeObserverCallback) {}
    observe() {
      this.callback([{ contentRect: { width, height } } as ResizeObserverEntry], this as unknown as ResizeObserver);
    }
    disconnect() {}
  });
  const context = {
    clearRect: () => { strokes = []; bars = []; labels = []; },
    setTransform: () => {},
    measureText: (text: string) => ({ width: text.length * 8 }),
    fillText: (text: string) => { labels.push(text); },
    beginPath: () => { path = []; },
    moveTo: (x: number, y: number) => { path.push([x, y]); },
    lineTo: (x: number, y: number) => { path.push([x, y]); },
    stroke: () => { strokes.push([...path]); },
    fillRect: (...rect: number[]) => { bars.push(rect); },
  } as unknown as CanvasRenderingContext2D;
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(context);
});

afterEach(async () => {
  if (component) await unmount(component);
  component = undefined;
  target.remove();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

function renderFrame() {
  const callback = nextFrame;
  expect(callback).toBeTypeOf('function');
  nextFrame = undefined;
  callback!(0);
}

function setup(mode: Mode, initial: Uint8Array | null) {
  let data = initial;
  let push: ((pixels: Uint8Array) => void) | undefined;
  component = mount(AmberAfScope, {
    target,
    props: {
      get data() { return data; },
      mode,
      filterWidth: 2400,
      filterWidthMax: 3600,
      onRegisterPush: (callback) => { push = callback; },
    },
  });
  flushSync();
  renderFrame();
  return {
    setData(pixels: Uint8Array | null) { data = pixels; renderFrame(); },
    push(pixels: Uint8Array) { expect(push).toBeTypeOf('function'); push!(pixels); renderFrame(); },
  };
}

function expectFrameOnly() {
  expect(bars).toEqual([]);
  expect(strokes).toHaveLength(1);
  expect(strokes[0]).toHaveLength(6);
  expect(strokes[0].some(([, y]) => y === 40)).toBe(true);
  expect(labels).toEqual(['Shift: ', '+0000', 'Hz', 'Filter: ', '2400', 'Hz']);
}

function expectZeroActivity() {
  expect(bars).toEqual([]);
  expect(strokes.slice(1).flat().every(([, y]) => y === height)).toBe(true);
}

function expectActivity(mode: Mode) {
  expect(bars.length).toBeGreaterThan(0);
  expect(bars.some(([, y, , h]) => y < height && h > 0)).toBe(true);
  if (mode === 'dominant') {
    expect(strokes.slice(1).flat().some(([, y]) => y < height)).toBe(true);
  }
}

describe.each<Mode>(['compact', 'fill', 'dominant'])('Amber AF %s input honesty', (mode) => {
  it.each([null, new Uint8Array()])('keeps only the instrument frame without bins (%s)', (pixels) => {
    setup(mode, pixels);
    expectFrameOnly();
    for (let i = 0; i < 5; i++) renderFrame();
    expectFrameOnly();
  });

  it('does not invent activity for measured zero bins', () => {
    setup(mode, new Uint8Array(256));
    expectZeroActivity();
    for (let i = 0; i < 5; i++) renderFrame();
    expectZeroActivity();
  });

  it.each([null, new Uint8Array()])('clears smoothing and peaks after explicit prop input (%s)', (empty) => {
    const scope = setup(mode, new Uint8Array(256).fill(160));
    expectActivity(mode);
    scope.setData(empty);
    expectFrameOnly();
    scope.setData(new Uint8Array(256));
    expectZeroActivity();
    scope.setData(new Uint8Array(256).fill(80));
    expectActivity(mode);
  });

  it('clears pushed empty bins and resumes with real bins', () => {
    const scope = setup(mode, null);
    scope.push(new Uint8Array(256).fill(160));
    expectActivity(mode);
    scope.push(new Uint8Array());
    expectFrameOnly();
    scope.push(new Uint8Array(256));
    expectZeroActivity();
    scope.push(new Uint8Array(256).fill(80));
    expectActivity(mode);
  });
});
