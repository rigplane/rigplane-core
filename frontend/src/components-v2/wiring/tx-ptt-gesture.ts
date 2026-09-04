/** Browser-local gesture translation. Radio state remains server-owned. */
export interface PttGestureCommands {
  pttOn(): void;
  pttOff(): void;
}

export interface PttGestureDeps {
  schedule(callback: () => void, ms: number): unknown;
  cancel(handle: unknown): void;
  doubleTapMs?: number;
}

export interface PttGesture {
  down(): void;
  up(): void;
  cancel(): void;
  destroy(): void;
}

const DEFAULT_DOUBLE_TAP_MS = 300;

export function createPttGesture(
  commands: PttGestureCommands,
  deps: PttGestureDeps,
): PttGesture {
  const doubleTapMs = deps.doubleTapMs ?? DEFAULT_DOUBLE_TAP_MS;
  let pending: unknown | null = null;
  let pressed = false;
  let latched = false;
  let destroyed = false;

  const clearPending = () => {
    if (pending === null) return;
    deps.cancel(pending);
    pending = null;
  };

  const down = () => {
    if (destroyed) return;
    if (pending !== null) {
      clearPending();
      latched = true;
      pressed = false;
      return;
    }
    if (latched) {
      latched = false;
      commands.pttOff();
      return;
    }
    pressed = true;
    commands.pttOn();
  };

  const up = () => {
    if (destroyed || !pressed) return;
    pressed = false;
    pending = deps.schedule(() => {
      pending = null;
      if (!destroyed && !latched) commands.pttOff();
    }, doubleTapMs);
  };

  const destroy = () => {
    if (destroyed) return;
    const active = pressed || latched || pending !== null;
    clearPending();
    pressed = false;
    latched = false;
    destroyed = true;
    if (active) commands.pttOff();
  };

  return { down, up, cancel: up, destroy };
}
