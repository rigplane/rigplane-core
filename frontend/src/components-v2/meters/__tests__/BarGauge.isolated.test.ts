import { describe, it, expect, vi } from 'vitest';
import { flushSync, mount, unmount } from 'svelte';
import BarGauge from '../BarGauge.svelte';
import type { BarMeterFrame } from '../bar-meter-motion.svelte';

describe('BarGauge hosted frame input', () => {
  it('renders a passive frame and delegates reset without creating schedules', () => {
    const requestFrame = vi.spyOn(window, 'requestAnimationFrame');
    const setInterval = vi.spyOn(window, 'setInterval');
    const onResetPeak = vi.fn();
    const frame: BarMeterFrame = { smoothedFraction: 0.45, peakFraction: 0.8 };
    const target = document.createElement('div');
    document.body.appendChild(target);
    const component = mount(BarGauge, {
      target,
      props: { frame, label: 'Po', displayValue: '45W', onResetPeak },
    });
    flushSync();
    try {
      expect(target.querySelectorAll('[data-gauge-fill]')).toHaveLength(5);
      expect(target.querySelector('[data-testid="bar-gauge-peak-marker"]')?.getAttribute('x'))
        .toBe('211');
      expect(requestFrame).not.toHaveBeenCalled();
      expect(setInterval).not.toHaveBeenCalled();

      target.querySelector('svg')!.dispatchEvent(new Event('dblclick', { bubbles: true }));
      expect(onResetPeak).toHaveBeenCalledOnce();
    } finally {
      unmount(component);
      target.remove();
      requestFrame.mockRestore();
      setInterval.mockRestore();
    }
  });

  it('rejects mixed live-value and hosted-frame ownership', () => {
    const target = document.createElement('div');
    document.body.appendChild(target);
    let component: ReturnType<typeof mount> | undefined;
    const frame: BarMeterFrame = { smoothedFraction: 0.4, peakFraction: null };
    try {
      expect(() => {
        component = mount(BarGauge as never, {
          target,
          props: { value: 0.4, frame, label: 'Po', displayValue: '40W' },
        });
        flushSync();
      }).toThrowError('BarGauge requires exactly one of frame or value');
    } finally {
      if (component !== undefined) unmount(component);
      target.remove();
    }
  });
});
