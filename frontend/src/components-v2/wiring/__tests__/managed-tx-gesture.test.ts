import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createManagedTxGesture } from '../managed-tx-gesture';

describe('managed TX gesture', () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  const setup = () => {
    let latched = false;
    const commands = { pttOn: vi.fn(), pttOff: vi.fn(), transmitOn: vi.fn(), forceOff: vi.fn() };
    const gesture = createManagedTxGesture(
      { latched: () => latched, transmitAvailable: () => true }, commands,
      { schedule: (fn, ms) => setTimeout(fn, ms), cancel: (handle) => clearTimeout(handle as number) },
    );
    return { commands, gesture, setLatched: (value: boolean) => { latched = value; } };
  };

  it('maps an ordinary press to exactly one WS ON/OFF pair', () => {
    const h = setup(); h.gesture.down(); h.gesture.up(); vi.advanceTimersByTime(300);
    expect(h.commands.pttOn).toHaveBeenCalledTimes(1);
    expect(h.commands.pttOff).toHaveBeenCalledTimes(1);
    expect(h.commands.transmitOn).not.toHaveBeenCalled();
    expect(h.commands.forceOff).not.toHaveBeenCalled();
  });

  it('maps the second tap to one HTTP TRANSMIT without a second WS lease', () => {
    const h = setup(); h.gesture.down(); h.gesture.up(); h.gesture.down();
    expect(h.commands.pttOn).toHaveBeenCalledTimes(1);
    expect(h.commands.pttOff).not.toHaveBeenCalled();
    expect(h.commands.transmitOn).toHaveBeenCalledTimes(1);
    vi.runAllTimers(); expect(h.commands.pttOff).not.toHaveBeenCalled();
  });

  it('reads canonical latch truth for explicit unlock and preserves it on presentation destroy', () => {
    const h = setup(); h.setLatched(true); h.gesture.down(); h.gesture.up();
    expect(h.commands.forceOff).toHaveBeenCalledTimes(1);
    const presentation = setup(); presentation.setLatched(true); presentation.gesture.destroy();
    expect(presentation.commands.forceOff).not.toHaveBeenCalled();
    expect(presentation.commands.pttOff).not.toHaveBeenCalled();
  });

  it('coalesces cancel/lost-capture/destroy to one PTT OFF', () => {
    const h = setup(); h.gesture.down(); h.gesture.cancel(); h.gesture.destroy(); vi.runAllTimers();
    expect(h.commands.pttOn).toHaveBeenCalledTimes(1);
    expect(h.commands.pttOff).toHaveBeenCalledTimes(1);
  });

  it('fails a stale latch conversion closed by releasing the existing PTT', () => {
    const h = setup();
    const gesture = createManagedTxGesture(
      { latched: () => false, transmitAvailable: () => false }, h.commands,
      { schedule: (fn, ms) => setTimeout(fn, ms), cancel: (handle) => clearTimeout(handle as number) },
    );
    gesture.down(); gesture.up(); gesture.down();
    expect(h.commands.transmitOn).not.toHaveBeenCalled();
    expect(h.commands.pttOff).toHaveBeenCalledTimes(1);
  });
});
