import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { ComponentProps } from 'svelte';
import type { Capabilities } from '$lib/types/capabilities';
import { clearCapabilities, setCapabilities } from '$lib/stores/capabilities.svelte';
import LinearSMeter from '../LinearSMeter.svelte';

// Minimal calibration table with an S9 knot — enough for `getS9Raw()` to
// resolve to a real anchor (raw 130) and for `rawToSegments` to place it at
// exactly 11 on the raw 0-20 domain (S9 is the last S-unit knot).
const CAL = [
  { raw: 0, actual: -54, label: 'S0' },
  { raw: 130, actual: 0, label: 'S9' },
];

function makeCaps(overrides: Partial<Capabilities> = {}): Capabilities {
  return {
    model: 'test-radio',
    scope: true,
    audio: true,
    tx: true,
    capabilities: ['scope', 'tx'],
    receivers: 2,
    vfoScheme: 'main_sub',
    freqRanges: [{ start: 1800000, end: 30000000, label: 'HF' }],
    modes: ['USB', 'LSB', 'CW', 'AM', 'FM'],
    filters: ['FIL1'],
    audioConfig: { sampleRate: 48000, channels: 1, codecs: ['opus'] },
    webrtc: { available: true, enabled: false },
    txBands: null,
    stateContractVersion: 1,
    providerGeneration: 0,
    ...overrides,
  };
}

beforeEach(() => {
  setCapabilities(makeCaps({ meterCalibrations: { s_meter: CAL } }));
});

let components: ReturnType<typeof mount>[] = [];
let roots: HTMLElement[] = [];

function mountMeter(props: ComponentProps<typeof LinearSMeter>) {
  const target = document.createElement('div');
  document.body.appendChild(target);
  roots.push(target);
  const component = mount(LinearSMeter, { target, props });
  flushSync();
  components.push(component);
  return target;
}

afterEach(() => {
  components.forEach((component) => unmount(component));
  roots.forEach((root) => root.remove());
  components = [];
  roots = [];
  clearCapabilities();
});

function segmentRects(target: HTMLElement): SVGRectElement[] {
  return Array.from(target.querySelectorAll('[data-segment]')) as SVGRectElement[];
}

describe('LinearSMeter display prop', () => {
  it('default mount (no display prop) renders exactly 20 segment rects', () => {
    const target = mountMeter({ value: 0 });
    expect(segmentRects(target)).toHaveLength(20);
  });

  it('display={{ segmentCount: 12, segmentGapPx: 3 }} renders exactly 12 segment rects', () => {
    const target = mountMeter({ value: 0, display: { segmentCount: 12, segmentGapPx: 3 } });
    expect(segmentRects(target)).toHaveLength(12);
  });

  it('honors segmentGapPx: the measured gap between adjacent rects changes by exactly the requested delta', () => {
    // The gap between rect i's right edge and rect i+1's left edge is
    // exactly SEG_GAP regardless of SEG_W's own normalization (SEG_W
    // cancels out of that difference), so this is a direct read of
    // segmentGapPx off the rendered geometry, not an inferred pitch.
    function measuredGap(target: HTMLElement): number {
      const [r0, r1] = segmentRects(target);
      const x0 = Number(r0.getAttribute('x'));
      const w0 = Number(r0.getAttribute('width'));
      const x1 = Number(r1.getAttribute('x'));
      return x1 - (x0 + w0);
    }

    const gap1 = measuredGap(mountMeter({ value: 0, display: { segmentCount: 12, segmentGapPx: 1 } }));
    const gap3 = measuredGap(mountMeter({ value: 0, display: { segmentCount: 12, segmentGapPx: 3 } }));

    expect(gap3 - gap1).toBe(2);
  });

  it('rescales the S9 crossover from the raw 20-segment domain instead of the literal 11', () => {
    const target = mountMeter({ value: 0, display: { segmentCount: 12, segmentGapPx: 1 } });
    const rects = segmentRects(target);
    // dimColor renders '#1A1008' from the first segment at/above the S9
    // crossover onward, '#0A2415' before it — read that switch off the DOM.
    const firstAboveS9 = rects.findIndex((r) => r.getAttribute('fill') === '#1A1008');

    expect(firstAboveS9).toBe(Math.round((11 / 20) * 12));
    expect(firstAboveS9).not.toBe(11);
  });
});
