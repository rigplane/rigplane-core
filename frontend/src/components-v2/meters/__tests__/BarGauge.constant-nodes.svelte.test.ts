/**
 * MOR-2521 — BarGauge keeps a constant node set across value updates.
 *
 * Mirrors the LinearSMeter sweep from #3557: the hosted-frame input drives
 * segment geometry directly (no ballistics), so every step is a
 * deterministic render. The sweep crosses full-segment boundaries, the
 * sub-1% fractional guard (0.1005 → fracSeg 0.005), an exact segment
 * boundary (0.7 → fracSeg 0) and both range ends. The peak marker holds a
 * constant peakFraction, so no node may appear or disappear mid-sweep.
 */
import { describe, it, expect } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import BarGauge from '../BarGauge.svelte';

describe('MOR-2521 — BarGauge never adds or removes nodes across a value sweep', () => {
  const SWEEP = [0, 0.05, 0.1005, 0.275, 0.55, 0.7, 0.9275, 1] as const;

  it('one node count for every sweep step, while the lit count still tracks the reading', () => {
    const state = $state({
      frame: { smoothedFraction: SWEEP[0], peakFraction: 0.8 },
      label: 'Po',
      displayValue: '50W',
    });
    const target = document.createElement('div');
    document.body.appendChild(target);
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const component = mount(BarGauge as any, { target, props: state });
    try {
      const steps = SWEEP.map((smoothedFraction) => {
        state.frame = { smoothedFraction, peakFraction: 0.8 };
        flushSync();
        return {
          rect: target.querySelectorAll('svg rect').length,
          fill: target.querySelectorAll('[data-gauge-fill]').length,
          visibleFills: [...target.querySelectorAll<SVGRectElement>('[data-gauge-fill]')]
            .filter((rect) => rect.getAttribute('visibility') !== 'hidden').length,
        };
      });
      for (const step of steps) {
        expect(step.rect).toBe(steps[0].rect);
        expect(step.fill).toBe(steps[0].fill);
      }
      // Container background + bar track + 10 dim + 10 permanent fill
      // rects + the peak marker = 23 rects; no lines.
      expect(steps[0]).toEqual({ rect: 23, fill: 10, visibleFills: 0 });
      expect(steps.map((step) => step.visibleFills)).toEqual([0, 1, 1, 3, 6, 7, 10, 10]);
    } finally {
      unmount(component);
      target.remove();
    }
  });
});
