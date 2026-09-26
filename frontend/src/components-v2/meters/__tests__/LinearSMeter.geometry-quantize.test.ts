import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import type { ComponentProps } from 'svelte';
// @ts-expect-error -- Svelte does not publish types for its reactive test harness.
import { proxy } from 'svelte/internal/client';
import { clearCapabilities, setCapabilities } from '$lib/stores/capabilities.svelte';
import type { Capabilities } from '$lib/types/capabilities';
import { GEOMETRY_FALLBACK_STEP, quantizeUserUnits } from '../meter-geometry-grid';
import LinearSMeter from '../LinearSMeter.svelte';
import { projectSignalMeter, type SignalMeterProjection } from '../smeter-scale';
import type { SignalMeterFrame } from '../signal-meter-motion.svelte';

// MOR-2613. jsdom never measures the SVG, so the component is on the 0.5-unit
// fallback grid here. The pixel grid itself is pinned by
// meter-geometry-grid.test.ts; this file only checks that the rendered
// attributes follow that fallback.

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

let components: ReturnType<typeof mount>[] = [];
let roots: HTMLElement[] = [];

beforeEach(() => {
  setCapabilities(makeCaps());
});

afterEach(() => {
  components.forEach((component) => unmount(component));
  roots.forEach((root) => root.remove());
  components = [];
  roots = [];
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

function mountReactive(smoothedFraction: number, peakFraction: number) {
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
      rect.getAttribute('width'),
      rect.getAttribute('visibility'),
    ].join(','))
    .join('|');
  const peak = target.querySelector('[data-meter-peak]');
  return `${fills}#${peak?.getAttribute('x1')},${peak?.getAttribute('visibility')}`;
}

// Two CSS pixels per user unit, so one device pixel is 0.5 user units.
const PIXELS_PER_USER_UNIT = 2;

describe('quantizeUserUnits', () => {
  it('maps a sub-pixel change to the same grid value', () => {
    const before = quantizeUserUnits(10, PIXELS_PER_USER_UNIT);
    expect(quantizeUserUnits(10 + 0.2, PIXELS_PER_USER_UNIT)).toBe(before);
  });

  it('maps a one-pixel change to a new grid value', () => {
    const before = quantizeUserUnits(10, PIXELS_PER_USER_UNIT);
    const after = quantizeUserUnits(10 + 1 / PIXELS_PER_USER_UNIT, PIXELS_PER_USER_UNIT);
    expect(after).not.toBe(before);
    expect(after - before).toBe(1 / PIXELS_PER_USER_UNIT);
  });

  it('uses the 0.5-unit fallback when the ratio is unknown', () => {
    expect(GEOMETRY_FALLBACK_STEP).toBe(0.5);
    for (const unknown of [0, -1, Number.NaN]) {
      expect(quantizeUserUnits(10.2, unknown)).toBe(10);
      expect(quantizeUserUnits(10.3, unknown)).toBe(10.5);
    }
  });
});

describe('MOR-2613 — jsdom renders the 0.5-unit fallback grid', () => {
  // One segment of the 20-segment face is SEG_W user units wide (23.25 at the
  // default gap), and the peak moves one pitch per segment. A step of 0.01
  // segment is about 0.23 user units, inside the 0.5 fallback; 0.03 segment
  // is about 0.7 and crosses it, for both the partial width and the peak.
  const BASE = 5.2 / 20;
  const PEAK = BASE + 0.4;

  it('a change smaller than 0.5 units leaves the segment and peak attributes unchanged', () => {
    const { target, step } = mountReactive(BASE, PEAK);
    const before = geometry(target);
    expect(before).not.toBe('');
    step(BASE + 0.01, PEAK + 0.01);
    expect(geometry(target)).toBe(before);
  });

  it('a change of 0.5 units or more updates the segment and peak attributes', () => {
    const { target, step } = mountReactive(BASE, PEAK);
    const before = geometry(target);
    step(BASE + 0.03, PEAK + 0.03);
    expect(geometry(target)).not.toBe(before);
  });
});
