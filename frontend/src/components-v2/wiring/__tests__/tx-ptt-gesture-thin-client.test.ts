import { afterEach, describe, expect, it, vi } from 'vitest';
import { createPttGesture } from '../tx-ptt-gesture';

describe('PTT gesture intent translation', () => {
  afterEach(() => vi.useRealTimers());

  it('maps a hold to session PTT ON then ordinary PTT OFF', () => {
    vi.useFakeTimers();
    const pttOn = vi.fn();
    const pttOff = vi.fn();
    const gesture = createPttGesture(
      { pttOn, pttOff },
      {
        schedule: (callback, ms) => setTimeout(callback, ms),
        cancel: (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>),
      },
    );

    gesture.down();
    expect(pttOn).toHaveBeenCalledOnce();
    expect(pttOff).not.toHaveBeenCalled();

    gesture.up();
    vi.advanceTimersByTime(299);
    expect(pttOff).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(pttOff).toHaveBeenCalledOnce();
  });

  it('uses its timer only for double-tap latch and unlocks with owner PTT OFF', () => {
    vi.useFakeTimers();
    const pttOn = vi.fn();
    const pttOff = vi.fn();
    const gesture = createPttGesture(
      { pttOn, pttOff },
      {
        schedule: (callback, ms) => setTimeout(callback, ms),
        cancel: (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>),
      },
    );

    gesture.down();
    gesture.up();
    gesture.down();
    vi.runAllTimers();

    expect(pttOn).toHaveBeenCalledOnce();
    expect(pttOff).not.toHaveBeenCalled();

    gesture.destroy();
    expect(pttOff).toHaveBeenCalledOnce();
    gesture.down();
    vi.runAllTimers();
    expect(pttOn).toHaveBeenCalledOnce();
  });
});
