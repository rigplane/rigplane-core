/**
 * MOR-1233: live (uncached) prefers-reduced-motion check. Shared by the
 * ballistics loop below and by rAF-driven peak-hold consumers (e.g.
 * LinearSMeter) so both respect the same signal without duplicating the
 * matchMedia guard.
 */
export function prefersReducedMotion(): boolean {
  return (
    typeof window !== 'undefined' &&
    typeof window.matchMedia === 'function' &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches
  );
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
  const mql = window.matchMedia('(prefers-reduced-motion: reduce)');
  const handler = () => callback(mql.matches);
  if (typeof mql.addEventListener === 'function') {
    mql.addEventListener('change', handler);
    return () => mql.removeEventListener('change', handler);
  }
  if (typeof mql.addListener === 'function') {
    mql.addListener(handler);
    return () => mql.removeListener?.(handler);
  }
  return () => {};
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
 */
export function createSmoother(attack = 0.12, release = 0.32, initialValue = 0) {
  let current = $state(initialValue);
  let target = initialValue;
  let frameId = 0;
  let lastTime = 0;
  let unsubscribe: (() => void) | null = null;

  function update(newTarget: number) {
    target = newTarget;
    if (prefersReducedMotion()) {
      current = newTarget;
    }
  }

  function tick(now: number) {
    const dt = Math.min(0.05, (now - lastTime) / 1000);
    lastTime = now;

    const tau = target >= current ? attack : release;
    const alpha = 1 - Math.exp(-dt / tau);
    current += (target - current) * alpha;

    frameId = requestAnimationFrame(tick);
  }

  function loop() {
    lastTime = performance.now();
    frameId = requestAnimationFrame(tick);
  }

  function start() {
    if (frameId) {
      cancelAnimationFrame(frameId);
      frameId = 0;
    }

    if (prefersReducedMotion()) {
      current = target;
    } else {
      loop();
    }

    unsubscribe?.();
    unsubscribe = onReducedMotionChange((reduced) => {
      if (reduced) {
        if (frameId) {
          cancelAnimationFrame(frameId);
          frameId = 0;
        }
        current = target;
      } else if (!frameId) {
        loop();
      }
    });
  }

  function stop() {
    if (frameId) {
      cancelAnimationFrame(frameId);
      frameId = 0;
    }
    unsubscribe?.();
    unsubscribe = null;
  }

  return {
    get value() { return current; },
    update,
    start,
    stop,
  };
}
