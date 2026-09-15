import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import {
  clamp,
  snapToStep,
  enumerateDiscreteValues,
  positionToValue,
  valueToPosition,
  getFillPercent,
  getCenterPercent,
  getBipolarFill,
  calculateDragValue,
  calculateClickValue,
  handleKeyboardStep,
  handleWheelStep,
  debounce,
  formatBipolarValue,
  DUAL_PARAM_CENTER_NORM,
  DUAL_PARAM_DEAD_ZONE,
  dualParamValuesFromNormX,
  dualParamNormXFromValues,
  dualParamThumbPercent,
  dualParamDeviationFromValues,
  dualParamStepAlongAxis,
  calculateArcPath,
  calculateIndicatorPosition,
  generateTickPositions,
  normalizedPercentDisplay,
  rawToPercentDisplay,
} from '../value-control-core';

describe('clamp', () => {
  it('returns value when within range', () => {
    expect(clamp(50, 0, 100)).toBe(50);
  });

  it('returns min when value is below range', () => {
    expect(clamp(-10, 0, 100)).toBe(0);
  });

  it('returns max when value is above range', () => {
    expect(clamp(150, 0, 100)).toBe(100);
  });

  it('handles equal min and max', () => {
    expect(clamp(50, 50, 50)).toBe(50);
  });
});

describe('snapToStep', () => {
  it('snaps to nearest step', () => {
    expect(snapToStep(23, 10, 0)).toBe(20);
    expect(snapToStep(27, 10, 0)).toBe(30);
  });

  it('respects min offset', () => {
    expect(snapToStep(25, 10, 5)).toBe(25);
    expect(snapToStep(28, 10, 5)).toBe(25);
  });

  it('handles step of 1', () => {
    expect(snapToStep(5.4, 1, 0)).toBe(5);
    expect(snapToStep(5.6, 1, 0)).toBe(6);
  });

  it('handles zero step', () => {
    expect(snapToStep(5.5, 0, 0)).toBe(5.5);
  });
});

describe('enumerateDiscreteValues', () => {
  it('lists integer steps from min to max inclusive', () => {
    expect(enumerateDiscreteValues(0, 15, 1)).toEqual([
      0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15,
    ]);
    expect(enumerateDiscreteValues(1, 3, 1)).toEqual([1, 2, 3]);
  });

  it('handles a single step range', () => {
    expect(enumerateDiscreteValues(5, 5, 1)).toEqual([5]);
  });

  it('returns min when step is zero', () => {
    expect(enumerateDiscreteValues(3, 10, 0)).toEqual([3]);
  });
});

describe('positionToValue', () => {
  it('converts 0 position to min', () => {
    expect(positionToValue(0, 0, 100, 1)).toBe(0);
  });

  it('converts 1 position to max', () => {
    expect(positionToValue(1, 0, 100, 1)).toBe(100);
  });

  it('converts 0.5 position to middle', () => {
    expect(positionToValue(0.5, 0, 100, 1)).toBe(50);
  });

  it('snaps to step', () => {
    expect(positionToValue(0.33, 0, 100, 10)).toBe(30);
  });

  it('works with negative ranges', () => {
    expect(positionToValue(0.5, -100, 100, 10)).toBe(0);
  });
});

describe('valueToPosition', () => {
  it('converts min to 0', () => {
    expect(valueToPosition(0, 0, 100)).toBe(0);
  });

  it('converts max to 1', () => {
    expect(valueToPosition(100, 0, 100)).toBe(1);
  });

  it('converts middle to 0.5', () => {
    expect(valueToPosition(50, 0, 100)).toBe(0.5);
  });

  it('handles zero range', () => {
    expect(valueToPosition(50, 50, 50)).toBe(0);
  });

  it('clamps to 0-1', () => {
    expect(valueToPosition(-10, 0, 100)).toBe(0);
    expect(valueToPosition(150, 0, 100)).toBe(1);
  });
});

describe('getFillPercent', () => {
  it('returns 0 at min', () => {
    expect(getFillPercent(0, 0, 100)).toBe(0);
  });

  it('returns 100 at max', () => {
    expect(getFillPercent(100, 0, 100)).toBe(100);
  });

  it('returns 50 at middle', () => {
    expect(getFillPercent(50, 0, 100)).toBe(50);
  });
});

describe('getCenterPercent', () => {
  it('returns 50% for symmetric range', () => {
    expect(getCenterPercent(-100, 100)).toBe(50);
  });

  it('returns 0% when range starts at zero', () => {
    expect(getCenterPercent(0, 100)).toBe(0);
  });

  it('returns correct percent for asymmetric range', () => {
    expect(getCenterPercent(-50, 100)).toBeCloseTo(33.33, 1);
  });
});

describe('getBipolarFill', () => {
  it('returns center to position for positive values', () => {
    const result = getBipolarFill(50, -100, 100);
    expect(result.fillStart).toBe(50);
    expect(result.fillEnd).toBe(75);
  });

  it('returns position to center for negative values', () => {
    const result = getBipolarFill(-50, -100, 100);
    expect(result.fillStart).toBe(25);
    expect(result.fillEnd).toBe(50);
  });

  it('returns center to center for zero', () => {
    const result = getBipolarFill(0, -100, 100);
    expect(result.fillStart).toBe(50);
    expect(result.fillEnd).toBe(50);
  });
});

describe('calculateClickValue', () => {
  it('calculates value from click position', () => {
    expect(calculateClickValue(50, 0, 100, 0, 100, 1)).toBe(50);
  });

  it('snaps to step', () => {
    expect(calculateClickValue(33, 0, 100, 0, 100, 10)).toBe(30);
  });

  it('clamps to range', () => {
    expect(calculateClickValue(-10, 0, 100, 0, 100, 1)).toBe(0);
    expect(calculateClickValue(150, 0, 100, 0, 100, 1)).toBe(100);
  });
});

describe('handleKeyboardStep', () => {
  it('increases value on ArrowRight', () => {
    expect(handleKeyboardStep(50, 'ArrowRight', 10, 10, 0, 100, false)).toBe(60);
  });

  it('decreases value on ArrowLeft', () => {
    expect(handleKeyboardStep(50, 'ArrowLeft', 10, 10, 0, 100, false)).toBe(40);
  });

  it('increases value on ArrowUp', () => {
    expect(handleKeyboardStep(50, 'ArrowUp', 10, 10, 0, 100, false)).toBe(60);
  });

  it('decreases value on ArrowDown', () => {
    expect(handleKeyboardStep(50, 'ArrowDown', 10, 10, 0, 100, false)).toBe(40);
  });

  it('uses fine step with shift key', () => {
    expect(handleKeyboardStep(50, 'ArrowRight', 10, 10, 0, 100, true)).toBe(51);
  });

  it('goes to min on Home', () => {
    expect(handleKeyboardStep(50, 'Home', 10, 10, 0, 100, false)).toBe(0);
  });

  it('goes to max on End', () => {
    expect(handleKeyboardStep(50, 'End', 10, 10, 0, 100, false)).toBe(100);
  });

  it('returns null for unhandled keys', () => {
    expect(handleKeyboardStep(50, 'a', 10, 10, 0, 100, false)).toBe(null);
  });

  it('clamps to range', () => {
    expect(handleKeyboardStep(95, 'ArrowRight', 10, 10, 0, 100, false)).toBe(100);
    expect(handleKeyboardStep(5, 'ArrowLeft', 10, 10, 0, 100, false)).toBe(0);
  });
});

describe('handleWheelStep', () => {
  it('increases value on scroll up (negative deltaY)', () => {
    expect(handleWheelStep(50, -100, 10, 10, 0, 100, false)).toBe(60);
  });

  it('decreases value on scroll down (positive deltaY)', () => {
    expect(handleWheelStep(50, 100, 10, 10, 0, 100, false)).toBe(40);
  });

  it('uses fine step with shift key', () => {
    expect(handleWheelStep(50, -100, 10, 10, 0, 100, true)).toBe(51);
  });

  it('clamps to range', () => {
    expect(handleWheelStep(95, -100, 10, 10, 0, 100, false)).toBe(100);
    expect(handleWheelStep(5, 100, 10, 10, 0, 100, false)).toBe(0);
  });
});

describe('debounce', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('delays function execution', () => {
    const fn = vi.fn();
    const debounced = debounce(fn, 100);

    debounced();
    expect(fn).not.toHaveBeenCalled();

    vi.advanceTimersByTime(100);
    expect(fn).toHaveBeenCalledOnce();
  });

  it('resets timer on repeated calls', () => {
    const fn = vi.fn();
    const debounced = debounce(fn, 100);

    debounced();
    vi.advanceTimersByTime(50);
    debounced();
    vi.advanceTimersByTime(50);
    expect(fn).not.toHaveBeenCalled();

    vi.advanceTimersByTime(50);
    expect(fn).toHaveBeenCalledOnce();
  });

  it('can be cancelled', () => {
    const fn = vi.fn();
    const debounced = debounce(fn, 100);

    debounced();
    debounced.cancel();
    vi.advanceTimersByTime(100);
    expect(fn).not.toHaveBeenCalled();
  });

  it('can be flushed', () => {
    const fn = vi.fn();
    const debounced = debounce(fn, 100);

    debounced('arg');
    debounced.flush();
    expect(fn).toHaveBeenCalledWith('arg');
  });
});

describe('dual RF/SQL single-thumb mapping', () => {
  it('exposes center constant at 0.5', () => {
    expect(DUAL_PARAM_CENTER_NORM).toBe(0.5);
  });

  it('maps norm X to RF/SQL with center dead zone', () => {
    // Extremes: unchanged
    expect(dualParamValuesFromNormX(0, 0, 255, 1)).toEqual({ rf: 0, sql: 0 });
    expect(dualParamValuesFromNormX(1, 0, 255, 1)).toEqual({ rf: 255, sql: 255 });
    // Center: RF=max, SQL=min
    expect(dualParamValuesFromNormX(0.5, 0, 255, 1)).toEqual({ rf: 255, sql: 0 });
    // Inside dead zone (0.46–0.54): snaps to center
    expect(dualParamValuesFromNormX(0.47, 0, 255, 1)).toEqual({ rf: 255, sql: 0 });
    expect(dualParamValuesFromNormX(0.53, 0, 255, 1)).toEqual({ rf: 255, sql: 0 });
    // Left of dead zone: RF active
    const leftResult = dualParamValuesFromNormX(0.23, 0, 255, 1);
    expect(leftResult.sql).toBe(0);
    expect(leftResult.rf).toBe(128); // 0.23/0.46 * 255 = 127.5 → 128
    // Right of dead zone: SQL active
    const rightResult = dualParamValuesFromNormX(0.77, 0, 255, 1);
    expect(rightResult.rf).toBe(255);
    expect(rightResult.sql).toBe(128); // (0.77-0.54)/(1-0.54) * 255 = 127.5 → 128
  });

  it('inverts values to norm X with dead zone', () => {
    // Far left: RF=0, SQL=0 → 0
    expect(dualParamNormXFromValues(0, 0, 0, 255)).toBeCloseTo(0, 5);
    // Center: RF=max, SQL=min → 0.5
    expect(dualParamNormXFromValues(255, 0, 0, 255)).toBeCloseTo(0.5, 5);
    // Far right: RF=max, SQL=max → 1.0
    expect(dualParamNormXFromValues(255, 255, 0, 255)).toBeCloseTo(1, 5);
    // SQL active: maps through right zone (0.54–1.0)
    const rightEdge = 0.54;
    expect(dualParamNormXFromValues(255, 128, 0, 255)).toBeCloseTo(
      rightEdge + (1 - rightEdge) * (128 / 255), 2,
    );
  });

  it('thumb percent follows norm X', () => {
    expect(dualParamThumbPercent(0, 0, 0, 255)).toBeCloseTo(0, 5);
    expect(dualParamThumbPercent(255, 0, 0, 255)).toBeCloseTo(50, 5);
    expect(dualParamThumbPercent(255, 255, 0, 255)).toBeCloseTo(100, 5);
  });

  it('deviation is zero at center and one at extremes', () => {
    expect(dualParamDeviationFromValues(255, 0, 0, 255)).toBeCloseTo(0, 5);
    expect(dualParamDeviationFromValues(0, 0, 0, 255)).toBeCloseTo(1, 5);
    expect(dualParamDeviationFromValues(255, 255, 0, 255)).toBeCloseTo(1, 5);
  });

  it('steps along axis from center (keyboard-style)', () => {
    expect(dualParamStepAlongAxis(255, 0, -1, 1, 10, 0, 255, false)).toEqual({ rf: 254, sql: 0 });
    expect(dualParamStepAlongAxis(255, 0, 1, 1, 10, 0, 255, false)).toEqual({ rf: 255, sql: 1 });
    expect(dualParamStepAlongAxis(255, 5, -1, 1, 10, 0, 255, false)).toEqual({ rf: 255, sql: 4 });
    expect(dualParamStepAlongAxis(100, 0, 1, 1, 10, 0, 255, false)).toEqual({ rf: 101, sql: 0 });
  });
});

describe('formatBipolarValue', () => {
  it('formats positive values with plus sign', () => {
    expect(formatBipolarValue(50)).toBe('+50');
  });

  it('formats negative values with minus sign', () => {
    expect(formatBipolarValue(-50)).toBe('-50');
  });

  it('formats zero without sign', () => {
    expect(formatBipolarValue(0)).toBe('0');
  });
});

describe('calculateArcPath', () => {
  it('generates valid SVG path', () => {
    const path = calculateArcPath(50, 50, 40, -135, 135);
    expect(path).toMatch(/^M\s+[\d.]+\s+[\d.]+\s+A/);
  });
});

describe('calculateIndicatorPosition', () => {
  it('calculates position on arc', () => {
    const pos = calculateIndicatorPosition(50, 50, 30, 50, 0, 100, 270);
    expect(pos.x).toBeCloseTo(50, 0);
    expect(pos.y).toBeGreaterThan(0);
  });
});

describe('generateTickPositions', () => {
  it('generates correct number of ticks', () => {
    const ticks = generateTickPositions(50, 50, 35, 40, 5, 270);
    expect(ticks).toHaveLength(6); // tickCount + 1
  });

  it('generates tick lines with valid coordinates', () => {
    const ticks = generateTickPositions(50, 50, 35, 40, 2, 270);
    ticks.forEach(tick => {
      expect(tick.x1).toBeDefined();
      expect(tick.y1).toBeDefined();
      expect(tick.x2).toBeDefined();
      expect(tick.y2).toBeDefined();
    });
  });
});

describe('calculateDragValue', () => {
  it('increases value on rightward bar drag', () => {
    expect(calculateDragValue(50, 50, 0, 100, 0, 100, 1)).toBe(100);
  });

  it('decreases value on leftward bar drag', () => {
    expect(calculateDragValue(50, -25, 0, 100, 0, 100, 1)).toBe(25);
  });

  it('clamps to min on large negative drag', () => {
    expect(calculateDragValue(50, -200, 0, 100, 0, 100, 1)).toBe(0);
  });

  it('clamps to max on large positive drag', () => {
    expect(calculateDragValue(50, 200, 0, 100, 0, 100, 1)).toBe(100);
  });

  it('snaps to step', () => {
    expect(calculateDragValue(50, 13, 0, 100, 0, 100, 10)).toBe(60);
  });

  it('uses vertical drag for knob mode (up = increase)', () => {
    // Negative deltaY = drag up = increase
    expect(calculateDragValue(50, 0, -50, 100, 0, 100, 1, true)).toBe(100);
  });

  it('uses vertical drag for knob mode (down = decrease)', () => {
    // Positive deltaY = drag down = decrease
    expect(calculateDragValue(50, 0, 25, 100, 0, 100, 1, true)).toBe(25);
  });

  it('ignores deltaX in knob mode', () => {
    expect(calculateDragValue(50, 999, -10, 100, 0, 100, 1, true)).toBe(60);
  });
});

describe('rawToPercentDisplay', () => {
  it('returns 0% for min value', () => {
    expect(rawToPercentDisplay(0)).toBe('0%');
  });

  it('returns 100% for max value (255)', () => {
    expect(rawToPercentDisplay(255)).toBe('100%');
  });

  it('returns 50% for midpoint (128)', () => {
    expect(rawToPercentDisplay(128)).toBe('50%');
  });

  it('rounds to nearest integer percent', () => {
    expect(rawToPercentDisplay(1)).toBe('0%');
    expect(rawToPercentDisplay(3)).toBe('1%');
  });

  it('handles custom min/max range', () => {
    expect(rawToPercentDisplay(50, 0, 100)).toBe('50%');
    expect(rawToPercentDisplay(100, 0, 100)).toBe('100%');
  });

  it('returns 0% when range is zero', () => {
    expect(rawToPercentDisplay(5, 5, 5)).toBe('0%');
  });
});

describe('normalizedPercentDisplay', () => {
  it('formats normalized level values as percent', () => {
    expect(normalizedPercentDisplay(0)).toBe('0%');
    expect(normalizedPercentDisplay(0.5)).toBe('50%');
    expect(normalizedPercentDisplay(1)).toBe('100%');
  });

  it('clamps out-of-range normalized values', () => {
    expect(normalizedPercentDisplay(-0.25)).toBe('0%');
    expect(normalizedPercentDisplay(1.25)).toBe('100%');
  });
});

describe('DUAL_PARAM_DEAD_ZONE', () => {
  it('is 0.04', () => {
    expect(DUAL_PARAM_DEAD_ZONE).toBe(0.04);
  });
});
