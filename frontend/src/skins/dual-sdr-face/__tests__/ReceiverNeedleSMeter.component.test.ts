import { afterEach, describe, expect, it, vi } from 'vitest';
import { flushSync, unmount } from 'svelte';
import { createClassComponent } from 'svelte/legacy';
import type { Capabilities } from '$lib/types/capabilities';
import { clearCapabilities, setCapabilities } from '$lib/stores/capabilities.svelte';
import { calibratedToSegments } from '../../../components-v2/meters/smeter-scale';

const smoothing = vi.hoisted(() => {
  const instances: Array<{ update: ReturnType<typeof vi.fn>; start: ReturnType<typeof vi.fn>; stop: ReturnType<typeof vi.fn> }> = [];
  const createSmoother = vi.fn((_attack: number, _release: number) => {
    let value = 0;
    const instance = {
      update: vi.fn((next: number) => { value = next; }),
      start: vi.fn(),
      stop: vi.fn(),
    };
    instances.push(instance);
    return {
      get value() { return value; },
      update: instance.update,
      start: instance.start,
      stop: instance.stop,
    };
  });
  return { createSmoother, instances };
});

vi.mock('$lib/utils/smoothing.svelte', () => ({
  createSmoother: smoothing.createSmoother,
  prefersReducedMotion: vi.fn(() => false),
}));

import ReceiverNeedleSMeter from '../ReceiverNeedleSMeter.svelte';

// A real curve in the REAL capabilities store (the MOR-1272 convention this
// suite's neighbours follow): S0/S9/S9+40 anchors are enough to calibrate.
const CALIBRATED_CAPS = {
  model: 'IC-7610',
  stateContractVersion: 1,
  providerGeneration: 0,
  meterCalibrations: {
    s_meter: [
      { raw: 0, actual: -54, label: 'S0' },
      { raw: 130, actual: 0, label: 'S9' },
      { raw: 240, actual: 40, label: 'S9+40' },
    ],
  },
} as unknown as Capabilities;

let destroy: (() => void) | null = null;

afterEach(() => {
  destroy?.();
  destroy = null;
  document.body.innerHTML = '';
  smoothing.createSmoother.mockClear();
  smoothing.instances.length = 0;
  clearCapabilities();
});

// The mocked smoother's value is a plain closure, not a Svelte signal, so a
// derived angle only recomputes when a reactive input (the `value` prop)
// changes. Deliver the reading, retract it once (the effect has already
// driven the smoother target), then deliver it again — the second delivery
// recomputes the angle from the settled smoother value.
function renderReading(value: number): HTMLElement {
  const target = document.createElement('div');
  document.body.appendChild(target);
  const instance = createClassComponent({
    component: ReceiverNeedleSMeter,
    target,
    props: { value: null },
  });
  flushSync();
  for (const next of [value, null, value]) {
    instance.$set({ value: next });
    flushSync();
  }
  destroy = () => instance.$destroy();
  return target;
}

// MOR-2720 (MOR-2705 part 4a decision): on an uncalibrated radio the needle
// MOVES with the raw fraction like the bars (`calibratedToSegments` is
// raw-proportional without a curve), draws no S scale marks, and its
// accessible name is the bare `S meter` — never a status word.
describe('ReceiverNeedleSMeter uncalibrated profile (MOR-2720)', () => {
  it('moves the needle with the raw fraction, with no scale marks and no status word', () => {
    clearCapabilities();
    const target = renderReading(53);

    expect(target.querySelector('svg')?.getAttribute('aria-label')).toBe('S meter');
    // The geometry needs no calibration curve: raw 53 maps raw-proportionally
    // to (53 / 127.5) * 11 ≈ 4.572549019607844 segments, and the needle
    // rotates on that fraction like the bars do.
    expect(calibratedToSegments(53)).toBeCloseTo(4.572549019607844, 12);
    expect(smoothing.instances[0]?.update).toHaveBeenCalledWith(4.572549019607844);
    expect(target.querySelector('[data-needle]')?.getAttribute('transform'))
      .toBe(`rotate(${-62 + (4.572549019607844 / 20) * 124} 120 88)`);
    // No S scale marks — but the arc track stays, like the bar track.
    expect(target.querySelectorAll('svg text')).toHaveLength(0);
    expect(target.querySelectorAll('.meter-arc')).toHaveLength(2);
  });

  it('keeps the S scale marks and the moving needle on a calibrated profile', () => {
    setCapabilities(CALIBRATED_CAPS);
    const target = renderReading(0);

    expect(target.querySelector('svg')?.getAttribute('aria-label')).toBe('S meter');
    // 0 dB rel S9 is the S9 anchor: raw 130 → 11 segments → angle 6.2°.
    expect(smoothing.instances[0]?.update).toHaveBeenCalledWith(11);
    expect(target.querySelector('[data-needle]')?.getAttribute('transform'))
      .toBe(`rotate(${-62 + (11 / 20) * 124} 120 88)`);
    expect([...target.querySelectorAll('svg text')].map((text) => text.textContent))
      .toEqual(['S', '9', '+40']);
  });
});
