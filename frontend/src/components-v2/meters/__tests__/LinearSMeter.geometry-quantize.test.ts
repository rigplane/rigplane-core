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

function onHalfUnitGrid(value: number): boolean {
  return Math.abs(value / GEOMETRY_FALLBACK_STEP - Math.round(value / GEOMETRY_FALLBACK_STEP)) < 1e-9;
}

function writtenGeometry(target: HTMLElement): number[] {
  const values: number[] = [];
  for (const rect of target.querySelectorAll<SVGRectElement>('[data-meter-fill]')) {
    values.push(Number(rect.getAttribute('x')), Number(rect.getAttribute('width')));
  }
  const peak = target.querySelector('[data-meter-peak]');
  values.push(Number(peak?.getAttribute('x1')), Number(peak?.getAttribute('x2')));
  return values;
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

describe('MOR-2613 — jsdom writes the 0.5-unit fallback grid', () => {
  // No width is measured in jsdom, so every geometry attribute the component
  // writes must land on the 0.5-unit fallback. The sweep does not assume how
  // the scale maps a fraction to a position: it only reads what was written.
  const STEPS = 50;

  it('keeps fill x/width and the peak line on the 0.5 grid across the range', () => {
    const { target, step } = mountReactive(0, 0.4);
    for (let i = 0; i <= STEPS; i += 1) {
      const fraction = i / STEPS;
      step(fraction, Math.min(1, fraction + 0.4));
      for (const value of writtenGeometry(target)) {
        expect(onHalfUnitGrid(value), `step ${i}: ${value}`).toBe(true);
      }
    }
  });
});
