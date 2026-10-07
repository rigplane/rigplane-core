import { describe, expect, it, vi } from 'vitest';
import { createManagedTxGesture } from '../managed-tx-gesture';

function fixture(latchAllowed = true) {
  const mode = { latchAllowed };
  let pending: (() => void) | null = null;
  const commands = { pttOn: vi.fn(), pttOff: vi.fn(), transmitOn: vi.fn(), forceOff: vi.fn() };
  const deps = {
    schedule: vi.fn((callback: () => void) => { pending = callback; return callback; }),
    cancel: vi.fn(() => { pending = null; }),
  };
  const gesture = createManagedTxGesture({
    latched: () => false, transmitAvailable: () => true,
    latchAllowed: () => mode.latchAllowed,
  }, commands, deps);
  return { gesture, commands, mode, deps, flush: () => { const next = pending; pending = null; next?.(); } };
}

describe('managed PTT gesture remote latch permission', () => {
  it('preserves the local double-tap latch and cancels its delayed release', () => {
    const f = fixture();
    f.gesture.down(); f.gesture.up(); f.gesture.down(); f.gesture.up(); f.flush();
    expect(f.commands.transmitOn).toHaveBeenCalledOnce();
    expect(f.commands.pttOff).not.toHaveBeenCalled();
    f.gesture.destroy();
  });

  it('releases each remote tap immediately and never turns rapid taps into a latch', () => {
    const f = fixture(false);
    f.gesture.down(); f.gesture.up();
    expect(f.commands.pttOff).toHaveBeenCalledOnce();
    f.gesture.down(); f.gesture.up(); f.flush();
    expect(f.commands.pttOn).toHaveBeenCalledTimes(2);
    expect(f.commands.pttOff).toHaveBeenCalledTimes(2);
    expect(f.commands.transmitOn).not.toHaveBeenCalled();
    expect(f.deps.schedule).not.toHaveBeenCalled();
    f.gesture.destroy();
  });

  it('cancels a remote hold immediately without scheduling a future intent', () => {
    const f = fixture(false);
    f.gesture.down(); f.gesture.cancel();
    expect(f.commands.pttOff).toHaveBeenCalledOnce();
    f.gesture.destroy(); f.flush();
    expect(f.commands.pttOff).toHaveBeenCalledOnce();
    expect(f.commands.transmitOn).not.toHaveBeenCalled();
  });

  it('drops a pending local latch when permission changes and requires a fresh press', () => {
    const f = fixture();
    f.gesture.down(); f.gesture.up();
    f.mode.latchAllowed = false;
    f.gesture.down(); f.gesture.up(); f.flush();
    expect(f.commands.transmitOn).not.toHaveBeenCalled();
    expect(f.commands.pttOn).toHaveBeenCalledOnce();
    expect(f.commands.pttOff).toHaveBeenCalledOnce();
    f.gesture.down(); f.gesture.up();
    expect(f.commands.pttOn).toHaveBeenCalledTimes(2);
    expect(f.commands.pttOff).toHaveBeenCalledTimes(2);
    f.gesture.destroy();
  });
});
