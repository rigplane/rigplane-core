/**
 * Render-boundary freshness gate for the waterfall (MOR-3471).
 *
 * A frame burst deferred below the app while the page was inactive is
 * delivered back-to-back once the event loop resumes; painting one
 * waterfall row per delivered frame would replay that burst as fake
 * history at drain speed. The gate classifies each delivery by its
 * spacing from the previous delivery, collapses post-gap bursts, and
 * lets live-spaced frames re-earn painting. Pinned by
 * __tests__/waterfall-freshness.test.ts and the delayed case of
 * components/spectrum/__tests__/scope-hidden-delivery.characterization.test.ts.
 */

/**
 * Delivery silence that separates live streaming from a deferred backlog.
 */
export const WATERFALL_DISCONTINUITY_MS = 3_000;

/**
 * Spacing under which a delivery counts as burst drain rather than a live
 * frame. Precondition, not a measurement: no drain-spacing trace exists
 * for MOR-3471 yet, and the only configured live cadence is the audio-fft
 * scope at fps=20 (50 ms minimum spacing, src/rigplane/audio/fft_scope.py);
 * the hardware scope has no single configured cadence. The gate is correct
 * only while a deferred burst drains below this threshold and live frames
 * arrive at or above it — if a hardware trace contradicts either side,
 * revisit this constant against that trace.
 */
export const WATERFALL_RESUME_SPACING_MS = 40;

/** Consecutive live-spaced deliveries needed before painting resumes. */
export const WATERFALL_RESUME_FRAMES = 5;

export type WaterfallClock = { now(): number };

export class WaterfallFreshnessGate {
  private lastDeliveryAt: number | null = null;
  // A gate with no delivery history trusts the stream: a fresh
  // registration paints its first frames instead of withholding them.
  // Residual: the providerGeneration remount replaces this gate, so the
  // first delivery after a provider switch paints unconditionally —
  // one row painted before any spacing history exists.
  private liveSpaced = WATERFALL_RESUME_FRAMES;

  constructor(private readonly clock: WaterfallClock = { now: () => performance.now() }) {}

  /**
   * Classify one delivery: `true` paints, `false` suppresses. Must run for
   * every delivery that reaches this registration — painted or not,
   * hidden or not, TX-frozen or not (TX-frozen callers discard the paint
   * verdict but still classify) — so the spacing baseline tracks the
   * transport instead of the paint history. Time with no deliveries at
   * all (scope demand off, source unavailable) updates nothing; the first
   * delivery after such a stretch trips the discontinuity and is
   * suppressed.
   */
  admit(): boolean {
    const now = this.clock.now();
    const last = this.lastDeliveryAt;
    this.lastDeliveryAt = now;
    if (last === null) return true;
    const spacing = now - last;
    if (spacing > WATERFALL_DISCONTINUITY_MS) {
      this.liveSpaced = 0;
      return false;
    }
    if (this.liveSpaced >= WATERFALL_RESUME_FRAMES) return true;
    if (spacing < WATERFALL_RESUME_SPACING_MS) {
      // A burst-spaced delivery mid-resume breaks the consecutive run;
      // the count never survives interleaved drain.
      this.liveSpaced = 0;
      return false;
    }
    this.liveSpaced += 1;
    return this.liveSpaced >= WATERFALL_RESUME_FRAMES;
  }
}
