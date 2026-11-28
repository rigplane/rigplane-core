import { browserRenderTiming } from '../canvas/scope-render-scheduler';

// MOR-2551 P2a: ONE shared reactive state object for the life of the module.
// Recreating it on every re-subscribe stranded surviving reactive readers of
// prefersReducedMotion() on the discarded object. The unsubscribe/subscribe
// cycle below only adds/removes the native media-query listener; the cached
// query and this state survive it, so getter consumers (which own no
// teardown handle) and late subscribers always observe the same object.
const motionState = $state({ reduced: false });
let motionQuery: MediaQueryList | null = null;
let queryFactory: typeof window.matchMedia | null = null;
let detachMotionQuery: (() => void) | null = null;
let motionListening = false;
const motionSubscribers = new Set<(reduced: boolean) => void>();

function attachMotionListener(query: MediaQueryList): void {
  const handler = () => {
    motionState.reduced = query.matches;
    // MOR-2551 P2b: re-check membership per callback — a subscriber removed
    // by an earlier callback in this same dispatch must not run (its
    // snapshotted entry is stale and could resurrect disposed animation).
    for (const callback of [...motionSubscribers]) {
      if (motionSubscribers.has(callback)) callback(motionState.reduced);
    }
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
  motionListening = true;
}

function ensureMotionQuery(): void {
  if (typeof window === 'undefined' || typeof window.matchMedia !== 'function') return;
  if (motionQuery && queryFactory === window.matchMedia) {
    if (!motionListening) {
      // Re-attaching after a detached spell: resync first so a change that
      // landed while detached is not missed, then listen again. Reads stay
      // cached while the listener is attached (no .matches polling here).
      motionState.reduced = motionQuery.matches;
      attachMotionListener(motionQuery);
    }
    return;
  }
  detachMotionQuery?.();
  detachMotionQuery = null;
  motionListening = false;
  queryFactory = window.matchMedia;
  const query = window.matchMedia('(prefers-reduced-motion: reduce)');
  motionQuery = query;
  motionState.reduced = query.matches;
  attachMotionListener(query);
}

export function prefersReducedMotion(): boolean {
  ensureMotionQuery();
  return typeof window !== 'undefined' && typeof window.matchMedia === 'function'
    ? motionState.reduced : false;
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
    // MOR-2551 P2a: only the native listener is removed here. The cached
    // query and the shared $state survive so surviving reactive readers and
    // getter consumers keep observing the same object; re-subscribing only
    // re-attaches the listener (see ensureMotionQuery).
    detachMotionQuery?.();
    detachMotionQuery = null;
    motionListening = false;
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
      } else if (started && !frameId) {
        // MOR-2551 P2b: never restart the loop after stop(). A stale
        // dispatch entry removed mid-dispatch is already skipped by the
        // membership check, but started === false means this callback is
        // teardown-orphaned — looping here would resurrect disposed work.
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
