/**
 * Render-boundary freshness gate for the waterfall (MOR-3471).
 *
 * A frame burst deferred below the app while the page was inactive is
 * delivered back-to-back once the event loop resumes; painting one
 * waterfall row per delivered frame would replay that burst as fake
 * history at drain speed. The gate classifies each delivery by its
 * spacing from the previous delivery — every delivery updates the
 * baseline, including ones the canvas drops while hidden — collapses
 * post-gap bursts, and lets live-spaced frames re-earn painting. Pinned
 * by __tests__/waterfall-freshness.test.ts and the delayed case of
 * components/spectrum/__tests__/scope-hidden-delivery.characterization.test.ts.
 */

/** Delivery silence that separates live streaming from a deferred backlog. */
export const WATERFALL_DISCONTINUITY_MS = 3_000;

/**
 * Spacing under which a delivery counts as burst drain rather than a live
 * frame. Above the ~16 ms back-to-back drain rate and at or below the 50 ms
 * audio-fft cadence (`fps=20`, src/rigplane/audio/fft_scope.py) and the
 * ~75–83 ms scope cadences.
 */
export const WATERFALL_RESUME_SPACING_MS = 40;

/** Consecutive live-spaced deliveries needed before painting resumes. */
export const WATERFALL_RESUME_FRAMES = 5;

export type WaterfallClock = { now(): number };

export class WaterfallFreshnessGate {
  private lastDeliveryAt: number | null = null;
  // A gate with no delivery history trusts the stream: a fresh
  // registration paints its first frames instead of withholding them.
  private liveSpaced = WATERFALL_RESUME_FRAMES;

  constructor(private readonly clock: WaterfallClock = { now: () => performance.now() }) {}

  /**
   * Classify one delivery: `true` paints, `false` suppresses. Must run for
   * every frame delivered to the waterfall, painted or not, so the spacing
   * baseline tracks the transport instead of the paint history.
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
    if (spacing < WATERFALL_RESUME_SPACING_MS) return false;
    this.liveSpaced += 1;
    return this.liveSpaced >= WATERFALL_RESUME_FRAMES;
  }
}
