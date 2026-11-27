import { browserRenderTiming } from '../canvas/scope-render-scheduler';

let motionState: { reduced: boolean } | null = null;
let motionQuery: MediaQueryList | null = null;
let queryFactory: typeof window.matchMedia | null = null;
let detachMotionQuery: (() => void) | null = null;
const motionSubscribers = new Set<(reduced: boolean) => void>();

function ensureMotionQuery(): void {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return;
  if (motionQuery && queryFactory === window.matchMedia) return;
  detachMotionQuery?.();
  queryFactory = window.matchMedia;
  const query = window.matchMedia('(prefers-reduced-motion: reduce)');
  motionQuery = query;
  const state = $state({ reduced: query.matches });
  motionState = state;
  const handler = () => {
    state.reduced = query.matches;
    for (const callback of [...motionSubscribers]) callback(state.reduced);
  };
  if (typeof query.addEventListener === 'function') {
    query.addEventListener('change', handler);
    detachMotionQuery = () => query.removeEventListener('change', handler);
  } else if (typeof query.addListener === 'function') {
    query.addListener(handler);
    detachMotionQuery = () => query.removeListener?.(handler);
  } else {
    detachMotionQuery = null;
  }
}

export function prefersReducedMotion(): boolean {
  ensureMotionQuery();
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? (motionState?.reduced ?? false) : false;
}

/**
 * MOR-1233 fix cycle 1 (verifier F1/F2): the preference can change while a
 * loop is already live — reading it once at mount is not enough. Subscribes
 * to the OS `change` event and returns an unsubscribe function; callers MUST
 * invoke it on teardown (no leak). No-op subscription when matchMedia is
 * unavailable.
 */
export function onReducedMotionChange(callback: (reduced: boolean) => void): () => void {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') {
    return () => {};
  }
  ensureMotionQuery();
  const subscriber = (reduced: boolean) => callback(reduced);
  motionSubscribers.add(subscriber);
  return () => {
    motionSubscribers.delete(subscriber);
    if (motionSubscribers.size !== 0) return;
    detachMotionQuery?.();
    detachMotionQuery = null;
    motionQuery = null;
    queryFactory = null;
  };
}

/**
 * Exponential smoothing for meter values.
 * React useSmoothedValue → Svelte $state + rAF loop.
 *
 * MOR-1233: under `prefers-reduced-motion`, ballistics are disabled — the
 * value snaps directly to target instead of animating, and no rAF loop is
 * scheduled. Fix cycle 1: the loop also reacts to the preference changing
 * mid-session in both directions (start()/stop() alone only decide at
 * mount).
 *
 * MOR-2613: exponential approach never lands on the target, so the frame loop
 * used to assign `current` forever. Once the remaining gap is under
 * SETTLE_EPSILON the value snaps to the target and the loop stops. A later
 * update that differs from the settled value restarts it.
 */
const SETTLE_EPSILON = 1e-4;

export function createSmoother(attack = 0.12, release = 0.32, initialValue = 0) {
  let current = $state(initialValue);
  let target = initialValue;
  let frameId = 0;
  let lastTime = 0;
  let started = false;
  let unsubscribe: (() => void) | null = null;

  function update(newTarget: number) {
    target = newTarget;
    if (prefersReducedMotion()) {
      current = newTarget;
      return;
    }
    if (started && frameId === 0 && newTarget !== current) {
      loop();
    }
  }

  function reset(value: number) {
    current = target = value;
  }

  function tick(now: number) {
    const dt = Math.min(0.05, (now - lastTime) / 1000);
    lastTime = now;

    const tau = target >= current ? attack : release;
    const alpha = 1 - Math.exp(-dt / tau);
    current += (target - current) * alpha;

    if (Math.abs(target - current) < SETTLE_EPSILON) {
      frameId = 0;
      current = target;
      return;
    }

    frameId = browserRenderTiming.requestFrame(tick);
  }

  function loop() {
    lastTime = performance.now();
    frameId = browserRenderTiming.requestFrame(tick);
  }

  function start() {
    if (frameId) {
      browserRenderTiming.cancelFrame(frameId);
      frameId = 0;
    }
    started = true;

    if (prefersReducedMotion()) {
      current = target;
    } else {
      loop();
    }

    unsubscribe?.();
    unsubscribe = onReducedMotionChange((reduced) => {
      if (reduced) {
        if (frameId) {
          browserRenderTiming.cancelFrame(frameId);
          frameId = 0;
        }
        current = target;
      } else if (!frameId) {
        loop();
      }
    });
  }

  function stop() {
    started = false;
    if (frameId) {
      browserRenderTiming.cancelFrame(frameId);
      frameId = 0;
    }
    unsubscribe?.();
    unsubscribe = null;
  }

  return {
    get value() { return current; },
    get settled() { return frameId === 0 && current === target; },
    update,
    reset,
    start,
    stop,
  };
}
