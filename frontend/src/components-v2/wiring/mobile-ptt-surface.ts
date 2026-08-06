/**
 * Mobile PTT surface orchestration (MOR-1378) — the per-input-surface
 * recognizer wiring MobileRadioLayout re-runs on every rotation, lifted out of
 * the component so it is independently mountable.
 *
 * Same seam-only doctrine as `tx-ptt-gesture.ts`, which this composes: a small
 * host facade in, a binding out. No Svelte, no `$lib/runtime` store singletons,
 * no DOM — so a unit test or the MOR-1088 evidence harness can drive the REAL
 * orchestration instead of hand-mirroring it.
 *
 * WHY ONE RECOGNIZER PER SURFACE. The portrait FAB and the landscape strip are
 * never mounted together and a rotation swaps one for the other; a shared
 * recognizer would carry an armed release window across that swap, where the
 * first press on the new surface would be misread as the second half of a
 * double-tap. Each surface therefore gets its own binding, and `destroy()`
 * releases a live lease exactly once — which is why rotating away while keyed
 * or latched drops TX rather than stranding it.
 *
 * WHY THE SOURCE ID IS PER SURFACE. The App TX controller keys lease ownership
 * by sourceId; a shared id would let a torn-down surface release — or latch —
 * a lease the freshly created one, or another source entirely, owns.
 */
import { createPttGesture, type Guard, type PttGesture } from './tx-ptt-gesture';

export type MobilePttSurface = 'portrait' | 'landscape';

/** The slice of the App TX controller facade this orchestration needs. */
export interface MobilePttHost {
  snapshot(): {
    readonly guard: Guard | null;
    readonly intent: 'momentary' | 'latched' | null;
    readonly phase: string;
  };
  resetFault(): void;
  start(sourceId: string, leaseId: string, intent: 'momentary'): void;
  setIntent(sourceId: string, guard: Guard, intent: 'latched'): void;
  release(sourceId: string, guard: Guard): void;
}

export type MobilePttSurfaceDeps = {
  schedule(callback: () => void, ms: number): unknown;
  cancel(handle: unknown): void;
  doubleTapMs?: number;
};

export interface MobilePttBinding {
  /** Direct, undelayed entry points — the landscape strip's own handlers. */
  down(): void;
  up(): void;
  /**
   * Portrait FAB entry points. PttFab's 50 ms hold timer is not cleared when it
   * unmounts, so a press begun in portrait can still fire after a rotation has
   * already swapped the recognizer — these are guarded, `down`/`up` are not.
   *
   * MOR-1376: the guard is THIS binding's own identity, not a live orientation
   * label. Hand these two out per generation (the FAB must capture them while
   * the press is armed) and a stranded press can only ever complete against the
   * generation it started on — which, once rotated away from, is destroyed and
   * inert. A shared forwarder reading a mutable `binding` slot reintroduces the
   * bug: a portrait → landscape → portrait double flip inside the 50 ms delay
   * puts the label back to 'portrait' and spuriously keys a generation the
   * operator never pressed (ResourceDemand handle-identity trap class).
   */
  fabDown(): void;
  fabUp(): void;
  destroy(): void;
}

/** Monotonic across every surface generation this module ever hands out. */
let surfaceSeq = 0;

export function createMobilePttSurface(
  surface: MobilePttSurface,
  host: MobilePttHost,
  deps: MobilePttSurfaceDeps,
): MobilePttBinding {
  const sourceId = `mobile-ptt-${surface}-${++surfaceSeq}`;
  let leaseSeq = 0;
  let alive = true;

  const gesture: PttGesture = createPttGesture(
    // Read the host DIRECTLY rather than a subscribed snapshot: teardown runs
    // after the component's subscription has already been dropped, and a stale
    // snapshot there would release a guard that has since moved on.
    {
      guard: () => host.snapshot().guard,
      latched: () => host.snapshot().intent === 'latched',
    },
    {
      start: () => {
        // A stale fault would swallow the start (the model only leaves
        // 'failed' on an explicit reset), so clear it as part of the press —
        // otherwise one denied press bricks mobile TX for the session.
        if (host.snapshot().phase === 'failed') host.resetFault();
        host.start(sourceId, `${sourceId}-${++leaseSeq}`, 'momentary');
      },
      latch: (guard) => host.setIntent(sourceId, guard, 'latched'),
      release: (guard) => host.release(sourceId, guard),
    },
    deps,
  );

  // `alive` is this generation's identity, captured in the closures below.
  // `createPttGesture` already no-ops once destroyed, so this is belt-and-
  // braces — but it is the assertion that makes the guard readable, and it also
  // covers the landscape binding (whose fabDown/fabUp are never wired to a FAB)
  // without consulting anything mutable outside this call.
  const live = (): boolean => alive && surface === 'portrait';
  return {
    down: () => gesture.down(),
    up: () => gesture.up(),
    fabDown: () => { if (live()) gesture.down(); },
    fabUp: () => { if (live()) gesture.up(); },
    destroy: () => { alive = false; gesture.destroy(); },
  };
}
