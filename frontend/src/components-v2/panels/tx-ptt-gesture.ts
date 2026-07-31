/**
 * PTT gesture recognizer — translates raw press/release input (down / up /
 * cancel) into TX intent commands (start / latch / release).
 *
 * This is pure gesture-to-intent translation: it knows nothing about the DOM,
 * transport, or the TX controller itself. It only talks to the small
 * `view`/`commands` seams below, so it can be unit-tested without mounting
 * anything and reused by any input surface (mouse, touch, hardware key).
 *
 * Gesture semantics:
 *  - A fresh `down()` (no pending window, not latched) starts a new lease.
 *  - `up()` after a successful start arms a `doubleTapMs` release window
 *    instead of releasing immediately (see WHY below). If nothing else
 *    happens, the window firing releases the lease — a plain hold-and-let-go.
 *  - A second `down()` that lands inside that window cancels the pending
 *    release and latches the SAME lease instead (double-tap-to-lock).
 *  - A `down()` while latched releases immediately (tap-to-unlock) and does
 *    not start a new lease.
 *  - `cancel()` is exactly `up()` — a physical release, however it was
 *    detected.
 *  - `destroy()` cancels any pending window and releases the live lease
 *    (if any) exactly once, then every method becomes an inert no-op.
 *
 * WHY the window holds the lease instead of releasing on pointerup:
 * releasing flips the TX model to phase `'releasing'`, and starting a new
 * lease while the model is anywhere but `'idle'` is a silent no-op
 * (tx-controller/model.ts `transition()`, the `'start'` branch). If `up()`
 * released right away, a fast double-tap would try to `start()` a second
 * lease while the first was still winding down — that call would simply be
 * swallowed, and double-tap-to-latch would never be reachable. Holding the
 * lease open behind a deferred, cancellable release keeps it alive long
 * enough for a same-lease `latch()` to land instead.
 */

/** Structurally identical to `TxGuard` in `$lib/runtime/tx-controller/model` — declared locally so this file stays import-free. */
export type Guard = Readonly<{
  leaseId: string;
  generation: number;
  authorityEpoch: number;
}>;

export interface PttGestureView {
  /** Current live TX lease guard, or `null` when no lease is held. */
  guard(): Guard | null;
  /** Whether the current lease is latched (locked key-down). */
  latched(): boolean;
}

export interface PttGestureCommands {
  start(): void;
  latch(guard: Guard): void;
  release(guard: Guard): void;
}

export interface PttGestureDeps {
  schedule(callback: () => void, ms: number): unknown;
  cancel(handle: unknown): void;
  /** Double-tap-to-latch window, in ms. Defaults to 300. */
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
  view: PttGestureView,
  commands: PttGestureCommands,
  deps: PttGestureDeps,
): PttGesture {
  const doubleTapMs = deps.doubleTapMs ?? DEFAULT_DOUBLE_TAP_MS;

  let pending: { handle: unknown; guard: Guard } | null = null;
  let token = 0;
  let destroyed = false;
  // True only right after a fresh, successful start() — gates whether the
  // next up() is allowed to arm a release window at all. False after a
  // latch, a tap-while-latched release, or a refused start, so the up()
  // that follows any of those is a plain no-op.
  let armableOnUp = false;

  function clearPending(): void {
    if (pending) {
      deps.cancel(pending.handle);
      pending = null;
    }
  }

  function down(): void {
    if (destroyed) return;
    // Defence #2 (token): bump on every down(), independent of the explicit
    // `clearPending()` cancel below. A timer already in flight from an
    // earlier up() compares its captured token against this on fire — if a
    // new down() happened since, the bump alone makes that fire inert even
    // if cancellation somehow didn't stick.
    token += 1;

    if (pending) {
      // Second tap landed inside the window a prior up() armed: cancel the
      // deferred release and latch onto the still-live lease instead.
      clearPending();
      const guard = view.guard();
      if (guard) commands.latch(guard);
      armableOnUp = false;
      return;
    }

    if (view.latched()) {
      // Tap-to-unlatch: release immediately, no window, no start().
      const guard = view.guard();
      if (guard) commands.release(guard);
      armableOnUp = false;
      return;
    }

    // Fresh press. Starting while a lease is already in flight anywhere but
    // idle is a silent no-op in the model, so a refused start just leaves
    // view.guard() null below — armableOnUp then stays false and the
    // following up() does nothing.
    commands.start();
    armableOnUp = view.guard() !== null;
  }

  function up(): void {
    if (destroyed) return;
    if (!armableOnUp) return;
    armableOnUp = false;

    const guard = view.guard();
    if (!guard) return;

    // Assign `pending` BEFORE calling deps.schedule(): if `schedule` ever
    // fired synchronously, filling `pending` only from its return value
    // would strand a non-null `pending` behind an already-fired timer —
    // a phantom window that would misread the next down() as a double-tap.
    const myToken = token;
    const entry: { handle: unknown; guard: Guard } = { handle: null, guard };
    pending = entry;
    entry.handle = deps.schedule(() => {
      // Defence #2 (token): a down() since arming bumps `token` — a stale
      // fire no-ops instead of releasing a lease that's moved on.
      if (myToken !== token) return;
      pending = null;
      // Defence #1 (captured guard): release the guard snapshotted AT ARM
      // TIME, never a fresh `view.guard()` read here — the live lease may
      // already be a different generation by the time this fires.
      commands.release(guard);
    }, doubleTapMs);
  }

  function cancel(): void {
    up();
  }

  function destroy(): void {
    if (destroyed) return;
    clearPending();
    const guard = view.guard();
    if (guard) commands.release(guard);
    destroyed = true;
  }

  return { down, up, cancel, destroy };
}
