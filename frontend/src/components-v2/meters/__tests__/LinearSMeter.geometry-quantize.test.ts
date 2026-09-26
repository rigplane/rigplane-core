import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { ComponentProps } from 'svelte';
// @ts-expect-error -- Svelte does not publish types for its reactive test harness.
import { proxy } from 'svelte/internal/client';
import { clearCapabilities, setCapabilities } from '$lib/stores/capabilities.svelte';
import type { Capabilities } from '$lib/types/capabilities';
import LinearSMeter from '../LinearSMeter.svelte';
import { projectSignalMeter, type SignalMeterProjection } from '../smeter-scale';
import type { SignalMeterFrame } from '../signal-meter-motion.svelte';

// MOR-2613 step 1b. The default face is viewBox="0 0 600 …" stretched to the
// rendered width, so one user unit is not one device pixel. Geometry that
// moves every animation frame (partial segment width, peak line) must be
// written only when a whole device pixel of that rendered width changes.
//
// The component measures the SVG the same way the VFO face does. This harness
// gives that measure a known width: 1200 CSS px against a 600-unit viewBox is
// 2 px per user unit, so the 0.5-unit fallback grid is not what is under test.

const RENDERED_WIDTH = 1200;

class FakeResizeObserver {
  private static callback?: (entries: { target: Element }[]) => void;
  private static targets = new Set<Element>();
  constructor(callback: (entries: { target: Element }[]) => void) {
    FakeResizeObserver.callback = callback;
  }
  observe(target: Element): void { FakeResizeObserver.targets.add(target); }
  unobserve(target: Element): void { FakeResizeObserver.targets.delete(target); }
  disconnect(): void { FakeResizeObserver.targets.clear(); }
  static fire(): void {
    const entries = [...FakeResizeObserver.targets].map((target) => ({ target }));
    FakeResizeObserver.callback?.(entries);
  }
}

const CAL = [
  { raw: 0, actual: -54, label: 'S0' },
  { raw: 130, actual: 0, label: 'S9' },
  { raw: 240, actual: 40, label: 'S9+40' },
];

function makeCaps(): Capabilities {
  return {
    model: 'test-radio',
    scope: true,
    audio: true,
    tx: true,
    capabilities: ['scope', 'tx'],
    receivers: 2,
    vfoScheme: 'main_sub',
    freqRanges: [{ start: 1800000, end: 30000000, label: 'HF' }],
    modes: ['USB'],
    filters: ['FIL1'],
    audioConfig: { sampleRate: 48000, channels: 1, codecs: ['opus'] },
    webrtc: { available: true, enabled: false },
    txBands: null,
    stateContractVersion: 1,
    providerGeneration: 0,
    meterCalibrations: { s_meter: CAL },
  };
}

let originalResizeObserver: unknown;
let originalClientWidth: PropertyDescriptor | undefined;
let components: ReturnType<typeof mount>[] = [];
let roots: HTMLElement[] = [];

beforeEach(() => {
  setCapabilities(makeCaps());
  originalResizeObserver = globalThis.ResizeObserver;
  globalThis.ResizeObserver = FakeResizeObserver as unknown as typeof ResizeObserver;
  originalClientWidth = Object.getOwnPropertyDescriptor(Element.prototype, 'clientWidth');
  Object.defineProperty(Element.prototype, 'clientWidth', {
    configurable: true,
    get: () => RENDERED_WIDTH,
  });
});

afterEach(() => {
  components.forEach((component) => unmount(component));
  roots.forEach((root) => root.remove());
  components = [];
  roots = [];
  globalThis.ResizeObserver = originalResizeObserver as typeof ResizeObserver;
  if (originalClientWidth) {
    Object.defineProperty(Element.prototype, 'clientWidth', originalClientWidth);
  }
  clearCapabilities();
});

function frame(
  projection: SignalMeterProjection,
  smoothedFraction: number,
  peakFraction: number,
): SignalMeterFrame {
  return {
    projection,
    smoothedFraction,
    peakFraction,
    afterglowFraction: null,
    reducedMotion: false,
  };
}

async function mountReactive(smoothedFraction: number, peakFraction: number) {
  const projection = projectSignalMeter(0);
  const state = proxy({
    frame: frame(projection, smoothedFraction, peakFraction),
  });
  const target = document.createElement('div');
  document.body.appendChild(target);
  roots.push(target);
  const component = mount(LinearSMeter, {
    target,
    props: state as ComponentProps<typeof LinearSMeter>,
  });
  components.push(component);
  flushSync();
  await Promise.resolve();
  flushSync();
  FakeResizeObserver.fire();
  flushSync();
  return {
    target,
    step(nextSmoothed: number, nextPeak: number) {
      state.frame = frame(projection, nextSmoothed, nextPeak);
      flushSync();
    },
  };
}

function geometry(target: HTMLElement): string {
  const fills = [...target.querySelectorAll<SVGRectElement>('[data-meter-fill]')]
    .map((rect) => [
      rect.getAttribute('data-meter-fill'),
      rect.getAttribute('x'),
      rect.getAttribute('width'),
      rect.getAttribute('fill'),
      rect.getAttribute('visibility'),
    ].join(','))
    .join('|');
  const peak = target.querySelector('[data-meter-peak]');
  return `${fills}#${peak?.getAttribute('x1')},${peak?.getAttribute('visibility')}`;
}

describe('MOR-2613 — segment geometry changes only on a whole device pixel', () => {
  // Default face: viewBox width 600, BAR_WIDTH 484, 20 segments, gap 2.
  // SEG_W = (484 - 38) / 20 = 22.3 user units. At 1200 CSS px the scale is
  // 2 device px per user unit, so one device pixel is 0.5 user units and a
  // partial-segment step of 0.01 user units (0.02 device px) must not rewrite
  // attributes. A step of one user unit is two device pixels and must.
  const BASE = 5.2 / 20;

  it('a displayed-value change smaller than one device pixel writes no segment or peak attribute', async () => {
    const { target, step } = await mountReactive(BASE, BASE + 0.4);
    const before = geometry(target);
    expect(before).not.toBe('');
    step(BASE + 0.01 / 20, BASE + 0.4 + 0.01 / 20);
    expect(geometry(target)).toBe(before);
  });

  it('a displayed-value change of one device pixel or more updates the partial width and the peak line', async () => {
    const { target, step } = await mountReactive(BASE, BASE + 0.4);
    const before = geometry(target);
    step(BASE + 1 / 20, BASE + 0.4 + 1 / 20);
    expect(geometry(target)).not.toBe(before);
  });
});
