import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createManagedTxGesture, createPttHold } from '../managed-tx-gesture';

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

describe('managed PTT gesture release cause (MOR-3753)', () => {
  it('a user release stays explicit', () => {
    const f = fixture(false);
    f.gesture.down(); f.gesture.up();
    expect(f.commands.pttOff.mock.calls).toEqual([[]]);
    f.gesture.destroy();
    expect(f.commands.pttOff.mock.calls).toEqual([[]]);
  });

  it('aborting a press or pending release releases automatically and leaves the gesture usable', () => {
    const f = fixture();
    f.gesture.down();
    f.gesture.abort();
    expect(f.commands.pttOff.mock.calls).toEqual([[{ automatic: true }]]);
    f.gesture.down(); f.gesture.up();
    f.gesture.abort();
    f.flush();
    expect(f.commands.pttOn).toHaveBeenCalledTimes(2);
    expect(f.commands.pttOff.mock.calls).toEqual([[{ automatic: true }], [{ automatic: true }]]);
    f.gesture.abort();
    expect(f.commands.pttOff).toHaveBeenCalledTimes(2);
    f.gesture.destroy();
  });

  it('the delayed user release at the end of the double-tap window stays explicit', () => {
    const f = fixture();
    f.gesture.down(); f.gesture.up();
    f.flush();
    expect(f.commands.pttOff.mock.calls).toEqual([[]]);
    f.gesture.destroy();
  });

  it('destroying an active press releases automatically', () => {
    const f = fixture();
    f.gesture.down();
    f.gesture.destroy();
    expect(f.commands.pttOff.mock.calls).toEqual([[{ automatic: true }]]);
  });

  it('destroying a pending delayed release releases automatically and fires nothing later', () => {
    const f = fixture();
    f.gesture.down(); f.gesture.up();
    f.gesture.destroy();
    expect(f.commands.pttOff.mock.calls).toEqual([[{ automatic: true }]]);
    f.flush();
    expect(f.commands.pttOff).toHaveBeenCalledOnce();
  });

  it('destroying without a press or pending release releases nothing', () => {
    const f = fixture();
    f.gesture.destroy();
    expect(f.commands.pttOff).not.toHaveBeenCalled();
  });
});

describe('shared PTT hold (MOR-3817)', () => {
  let nodes: HTMLElement[] = [];
  let holds: ReturnType<typeof createPttHold>[] = [];
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => {
    for (const hold of holds) hold.destroy();
    for (const node of nodes) node.remove();
    nodes = []; holds = [];
    Reflect.deleteProperty(document, 'visibilityState');
    vi.useRealTimers();
  });

  function setup(guard: (() => { delayMs: number; moveCancelPx: number } | null) | undefined = undefined) {
    const target = { press: vi.fn(), release: vi.fn(), abort: vi.fn() };
    const hold = createPttHold(target);
    holds.push(hold);
    const mountButton = () => {
      const node = document.createElement('button');
      document.body.appendChild(node);
      nodes.push(node);
      const action = hold.attach(node, guard ? { guard } : undefined);
      return { node, detach: () => { action.destroy(); node.remove(); } };
    };
    return { target, hold, mountButton };
  }
  const pointer = (el: EventTarget, type: string, init: PointerEventInit = {}) =>
    el.dispatchEvent(new PointerEvent(type, { bubbles: true, pointerId: 1, button: 0, ...init }));
  const key = (el: EventTarget, type: string, init: KeyboardEventInit = {}) =>
    el.dispatchEvent(new KeyboardEvent(type, { bubbles: true, key: ' ', ...init }));

  it('blocks browser panning on every attached button', () => {
    const { mountButton } = setup();
    expect(mountButton().node.style.touchAction).toBe('none');
  });

  it('presses on pointerdown and releases on the pointerup of that pointer, wherever it lands', () => {
    const { target, mountButton } = setup();
    const { node } = mountButton();
    pointer(node, 'pointerdown');
    expect(target.press).toHaveBeenCalledOnce();
    pointer(document.body, 'pointerup', { pointerId: 2 });
    expect(target.release).not.toHaveBeenCalled();
    pointer(document.body, 'pointerup');
    expect(target.release).toHaveBeenCalledOnce();
    pointer(document.body, 'pointerup');
    expect(target.release).toHaveBeenCalledOnce();
  });

  it('releases on pointercancel of the captured pointer only', () => {
    const { target, mountButton } = setup();
    const { node } = mountButton();
    pointer(node, 'pointerdown');
    pointer(node, 'pointercancel', { pointerId: 2 });
    expect(target.release).not.toHaveBeenCalled();
    pointer(node, 'pointercancel');
    expect(target.release).toHaveBeenCalledOnce();
  });

  it('keeps a pointer hold through pointerleave, lost capture, element blur and finger travel', () => {
    const { target, mountButton } = setup();
    const { node } = mountButton();
    pointer(node, 'pointerdown');
    pointer(node, 'pointermove', { clientX: 200, clientY: 200 });
    pointer(node, 'pointerleave');
    pointer(node, 'lostpointercapture');
    node.dispatchEvent(new FocusEvent('blur'));
    expect(target.release).not.toHaveBeenCalled();
    expect(target.abort).not.toHaveBeenCalled();
  });

  it('survives its button detaching, and the next button neither re-presses nor misses the lift', () => {
    const { target, mountButton } = setup();
    const first = mountButton();
    pointer(first.node, 'pointerdown');
    first.detach();
    const second = mountButton();
    pointer(second.node, 'pointerdown', { pointerId: 2 });
    expect(target.press).toHaveBeenCalledOnce();
    expect(target.release).not.toHaveBeenCalled();
    pointer(second.node, 'pointerup');
    expect(target.release).toHaveBeenCalledOnce();
  });

  it.each([
    ['the page is hidden', () => {
      Object.defineProperty(document, 'visibilityState', { configurable: true, value: 'hidden' });
      document.dispatchEvent(new Event('visibilitychange'));
    }],
    ['the window loses focus', () => window.dispatchEvent(new Event('blur'))],
  ] as const)('aborts a hold when %s', (_name, loseThePage) => {
    const { target, mountButton } = setup();
    pointer(mountButton().node, 'pointerdown');
    loseThePage();
    expect(target.abort).toHaveBeenCalledOnce();
    pointer(document.body, 'pointerup');
    expect(target.release).not.toHaveBeenCalled();
  });

  it('a visible-again visibilitychange does not end the hold', () => {
    const { target, mountButton } = setup();
    pointer(mountButton().node, 'pointerdown');
    document.dispatchEvent(new Event('visibilitychange'));
    expect(target.abort).not.toHaveBeenCalled();
  });

  it('ignores a non-primary button', () => {
    const { target, mountButton } = setup();
    pointer(mountButton().node, 'pointerdown', { button: 2 });
    expect(target.press).not.toHaveBeenCalled();
  });

  it('guard: a lift before the delay never presses', () => {
    const { target, mountButton } = setup(() => ({ delayMs: 50, moveCancelPx: 8 }));
    const { node } = mountButton();
    pointer(node, 'pointerdown');
    vi.advanceTimersByTime(49);
    pointer(node, 'pointerup');
    vi.advanceTimersByTime(100);
    expect(target.press).not.toHaveBeenCalled();
    expect(target.release).not.toHaveBeenCalled();
  });

  it('guard: moving past the limit before the delay drops the press; moving after it does not', () => {
    const { target, mountButton } = setup(() => ({ delayMs: 50, moveCancelPx: 8 }));
    const { node } = mountButton();
    pointer(node, 'pointerdown');
    pointer(node, 'pointermove', { clientX: 9 });
    vi.advanceTimersByTime(100);
    pointer(node, 'pointerup');
    expect(target.press).not.toHaveBeenCalled();
    expect(target.release).not.toHaveBeenCalled();

    pointer(node, 'pointerdown');
    vi.advanceTimersByTime(50);
    pointer(node, 'pointermove', { clientX: 100 });
    expect(target.press).toHaveBeenCalledOnce();
    expect(target.release).not.toHaveBeenCalled();
    pointer(document.body, 'pointerup', { clientX: 100 });
    expect(target.release).toHaveBeenCalledOnce();
  });

  it('guard returning null presses at once', () => {
    const { target, mountButton } = setup(() => null);
    pointer(mountButton().node, 'pointerdown');
    expect(target.press).toHaveBeenCalledOnce();
  });

  it('keyboard: press on key down, ignore repeats, release on key up', () => {
    const { target, mountButton } = setup();
    const { node } = mountButton();
    key(node, 'keydown');
    key(node, 'keydown', { repeat: true });
    expect(target.press).toHaveBeenCalledOnce();
    key(node, 'keyup');
    expect(target.release).toHaveBeenCalledOnce();
    key(node, 'keydown', { key: 'a' });
    expect(target.press).toHaveBeenCalledOnce();
  });

  it('keyboard: a key hold releases when its button loses focus', () => {
    const { target, mountButton } = setup();
    const { node } = mountButton();
    key(node, 'keydown');
    node.dispatchEvent(new FocusEvent('blur'));
    expect(target.release).toHaveBeenCalledOnce();
  });

  it('keyboard: a key hold aborts, not releases, when its button detaches', () => {
    const { target, mountButton } = setup();
    const button = mountButton();
    key(button.node, 'keydown');
    button.detach();
    expect(target.abort).toHaveBeenCalledOnce();
    expect(target.release).not.toHaveBeenCalled();
  });

  it('abort cancels a press still inside its guard and leaves the hold usable', () => {
    const { target, hold, mountButton } = setup(() => ({ delayMs: 50, moveCancelPx: 8 }));
    const { node } = mountButton();
    pointer(node, 'pointerdown');
    vi.advanceTimersByTime(20);
    hold.abort();
    vi.advanceTimersByTime(100);
    expect(target.press).not.toHaveBeenCalled();
    expect(target.abort).not.toHaveBeenCalled();
    pointer(node, 'pointerdown', { pointerId: 2 });
    vi.advanceTimersByTime(50);
    expect(target.press).toHaveBeenCalledOnce();
  });

  it('abort ends an engaged hold as abort and ignores that pointer\'s later lift', () => {
    const { target, hold, mountButton } = setup();
    pointer(mountButton().node, 'pointerdown');
    hold.abort();
    pointer(document.body, 'pointerup');
    expect(target.abort).toHaveBeenCalledOnce();
    expect(target.release).not.toHaveBeenCalled();
  });

  it('destroy aborts an engaged hold, then ignores further input', () => {
    const { target, hold, mountButton } = setup();
    const { node } = mountButton();
    pointer(node, 'pointerdown');
    hold.destroy();
    expect(target.abort).toHaveBeenCalledOnce();
    pointer(document.body, 'pointerup');
    pointer(node, 'pointerdown', { pointerId: 3 });
    expect(target.release).not.toHaveBeenCalled();
    expect(target.press).toHaveBeenCalledOnce();
  });
});
