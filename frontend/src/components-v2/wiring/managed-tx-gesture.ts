export interface ManagedTxGestureCommands {
  pttOn(): void;
  pttOff(): void;
  transmitOn(): void;
  forceOff(): void;
}
export interface ManagedTxGestureView {
  latched(): boolean;
  transmitAvailable(): boolean;
}

export interface ManagedTxGestureDeps {
  schedule(callback: () => void, ms: number): unknown;
  cancel(handle: unknown): void;
  doubleTapMs?: number;
}

export interface ManagedTxGesture {
  down(): void;
  up(): void;
  cancel(): void;
  destroy(): void;
}

export type ManagedMobilePttSurface = 'portrait' | 'landscape';
export interface ManagedMobilePttBinding {
  down(): void;
  up(): void;
  fabDown(): void;
  fabUp(): void;
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
      if (view.transmitAvailable()) commands.transmitOn();
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
    const myToken = token;
    pending = deps.schedule(() => {
      if (destroyed || myToken !== token) return;
      pending = null;
      commands.pttOff();
    }, delay);
  };

  const destroy = () => {
    if (destroyed) return;
    const releasePtt = pressed || pending !== null;
    clearPending();
    if (releasePtt) commands.pttOff();
    pressed = false;
    destroyed = true;
  };

  return { down, up, cancel: up, destroy };
}

/** Gives every mounted orientation an isolated, teardown-safe gesture generation. */
export function createManagedMobilePttSurface(
  surface: ManagedMobilePttSurface,
  view: ManagedTxGestureView,
  commands: ManagedTxGestureCommands,
  deps: ManagedTxGestureDeps,
): ManagedMobilePttBinding {
  let alive = true;
  const gesture = createManagedTxGesture(view, commands, deps);
  const portraitLive = () => alive && surface === 'portrait';
  return {
    down: gesture.down,
    up: gesture.up,
    fabDown: () => { if (portraitLive()) gesture.down(); },
    fabUp: () => { if (portraitLive()) gesture.up(); },
    destroy: () => { alive = false; gesture.destroy(); },
  };
}
