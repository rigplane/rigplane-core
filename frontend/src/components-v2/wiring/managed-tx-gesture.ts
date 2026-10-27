export interface ManagedTxGestureCommands {
  pttOn(): void;
  /** User release by default; { automatic: true } for teardown-owned cleanup. */
  pttOff(options?: { automatic?: boolean }): void;
  transmitOn(): void;
  forceOff(): void;
}
export interface ManagedTxGestureView {
  latched(): boolean;
  transmitAvailable(): boolean;
  latchAllowed?(): boolean;
}

export interface ManagedTxGestureDeps {
  schedule(callback: () => void, ms: number): unknown;
  cancel(handle: unknown): void;
  doubleTapMs?: number;
}

export interface ManagedTxGesture {
  down(): void;
  up(): void;
  /** Ends a press or pending release at once as automatic cleanup. */
  abort(): void;
  destroy(): void;
}

/** Interprets pointer timing only; it never infers or changes radio state. */
export function createManagedTxGesture(
  view: ManagedTxGestureView,
  commands: ManagedTxGestureCommands,
  deps: ManagedTxGestureDeps,
): ManagedTxGesture {
  let pending: unknown | null = null;
  let pressed = false;
  let destroyed = false;
  let token = 0;
  const delay = deps.doubleTapMs ?? 300;

  const clearPending = () => {
    if (pending === null) return;
    deps.cancel(pending);
    pending = null;
  };

  const down = () => {
    if (destroyed) return;
    token += 1;
    if (pending !== null) {
      clearPending();
      pressed = false;
      if (view.latchAllowed?.() !== false && view.transmitAvailable()) commands.transmitOn();
      else commands.pttOff();
      return;
    }
    if (view.latched()) {
      commands.forceOff();
      return;
    }
    pressed = true;
    commands.pttOn();
  };

  const up = () => {
    if (destroyed || !pressed) return;
    pressed = false;
    if (view.latchAllowed?.() === false) {
      clearPending();
      commands.pttOff();
      return;
    }
    const myToken = token;
    pending = deps.schedule(() => {
      if (destroyed || myToken !== token) return;
      pending = null;
      commands.pttOff();
    }, delay);
  };

  const abort = () => {
    if (destroyed) return;
    const releasePtt = pressed || pending !== null;
    clearPending();
    pressed = false;
    // Teardown and page loss are not a user release: they must preserve a
    // latched failure instead of acknowledging it (MOR-3753).
    if (releasePtt) commands.pttOff({ automatic: true });
  };

  const destroy = () => {
    abort();
    destroyed = true;
  };

  return { down, up, abort, destroy };
}

/** What a PTT hold drives; `abort` is page loss (hidden, window blur) or teardown. */
export interface PttHoldTarget {
  press(): void;
  release(): void;
  abort(): void;
}

/** A press engages only after `delayMs`, and moving `moveCancelPx` before then drops it. */
export interface PttHoldGuard {
  delayMs: number;
  moveCancelPx: number;
}

export interface PttHoldOptions {
  /** Read at each pointer press; `null` engages at once. */
  guard?: () => PttHoldGuard | null;
}

export interface PttHold {
  /** Svelte action for a PTT button; detaching it does not end a pointer hold. */
  attach(node: HTMLElement, options?: PttHoldOptions): { update(options?: PttHoldOptions): void; destroy(): void };
  destroy(): void;
}

type HoldState =
  | { kind: 'pointer'; pointerId: number; engaged: boolean; timer: ReturnType<typeof setTimeout> | null;
    x: number; y: number; moveCancelPx: number }
  | { kind: 'key'; node: HTMLElement };

const isPttKey = (event: KeyboardEvent) => event.key === ' ' || event.key === 'Enter';

/**
 * The one PTT press/hold mechanism (MOR-3817). The hold lives here, not in a
 * button, so it outlives the button that started it. A pointer hold ends only
 * on pointerup or pointercancel of the pointer that started it, on the page
 * becoming hidden, or on window blur; pointerleave, lost capture and element
 * blur do not end it. A keyboard hold ends on key up or when its button loses
 * focus or detaches.
 */
export function createPttHold(target: PttHoldTarget): PttHold {
  let hold: HoldState | null = null;
  let destroyed = false;

  const end = (outcome: 'release' | 'abort' | 'drop') => {
    const current = hold;
    if (current === null) return;
    hold = null;
    window.removeEventListener('pointerup', onPointerEnd, true);
    window.removeEventListener('pointercancel', onPointerEnd, true);
    window.removeEventListener('pointermove', onPointerMove, true);
    window.removeEventListener('blur', onPageLoss);
    document.removeEventListener('visibilitychange', onVisibility);
    if (current.kind === 'pointer') {
      if (current.timer !== null) clearTimeout(current.timer);
      if (!current.engaged) return;
    }
    if (outcome === 'release') target.release();
    else if (outcome === 'abort') target.abort();
  };

  const begin = (next: HoldState) => {
    hold = next;
    window.addEventListener('pointerup', onPointerEnd, true);
    window.addEventListener('pointercancel', onPointerEnd, true);
    window.addEventListener('pointermove', onPointerMove, true);
    window.addEventListener('blur', onPageLoss);
    document.addEventListener('visibilitychange', onVisibility);
  };

  function onPointerEnd(event: PointerEvent) {
    if (hold?.kind === 'pointer' && event.pointerId === hold.pointerId) end('release');
  }
  function onPointerMove(event: PointerEvent) {
    if (hold?.kind !== 'pointer' || hold.engaged || event.pointerId !== hold.pointerId) return;
    const dx = event.clientX - hold.x;
    const dy = event.clientY - hold.y;
    if (dx * dx + dy * dy > hold.moveCancelPx * hold.moveCancelPx) end('drop');
  }
  function onPageLoss() { end('abort'); }
  function onVisibility() { if (document.visibilityState === 'hidden') end('abort'); }

  const attach: PttHold['attach'] = (node, initial) => {
    let options = initial;
    node.style.touchAction = 'none';
    const onPointerDown = (event: PointerEvent) => {
      if (destroyed || hold !== null || event.button !== 0) return;
      event.preventDefault();
      node.setPointerCapture?.(event.pointerId);
      const guard = options?.guard?.() ?? null;
      const next: HoldState = {
        kind: 'pointer', pointerId: event.pointerId, engaged: guard === null, timer: null,
        x: event.clientX, y: event.clientY, moveCancelPx: guard?.moveCancelPx ?? Infinity,
      };
      begin(next);
      if (guard === null) {
        target.press();
        return;
      }
      next.timer = setTimeout(() => {
        next.timer = null;
        next.engaged = true;
        target.press();
      }, guard.delayMs);
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (!isPttKey(event)) return;
      event.preventDefault();
      if (event.repeat || destroyed || hold !== null) return;
      begin({ kind: 'key', node });
      target.press();
    };
    const onKeyUp = (event: KeyboardEvent) => {
      if (!isPttKey(event)) return;
      event.preventDefault();
      if (hold?.kind === 'key' && hold.node === node) end('release');
    };
    const onBlur = () => { if (hold?.kind === 'key' && hold.node === node) end('release'); };
    const onContextMenu = (event: Event) => event.preventDefault();
    node.addEventListener('pointerdown', onPointerDown);
    node.addEventListener('keydown', onKeyDown);
    node.addEventListener('keyup', onKeyUp);
    node.addEventListener('blur', onBlur);
    node.addEventListener('contextmenu', onContextMenu);
    return {
      update(next) { options = next; },
      destroy() {
        node.removeEventListener('pointerdown', onPointerDown);
        node.removeEventListener('keydown', onKeyDown);
        node.removeEventListener('keyup', onKeyUp);
        node.removeEventListener('blur', onBlur);
        node.removeEventListener('contextmenu', onContextMenu);
        if (hold?.kind === 'key' && hold.node === node) end('release');
      },
    };
  };

  return {
    attach,
    destroy() {
      if (destroyed) return;
      destroyed = true;
      end('abort');
    },
  };
}
