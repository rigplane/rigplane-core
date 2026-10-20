/**
 * Render-boundary freshness gate for the waterfall (MOR-3471).
 *
 * Suspected fix candidate from code analysis, not a confirmed root-cause
 * fix: a frame burst deferred below the app while the page was inactive
 * is delivered back-to-back once the event loop resumes; painting one
 * waterfall row per delivered frame would replay that burst as fake
 * history at drain speed. The gate classifies each delivery by its
 * spacing from the previous delivery, collapses post-gap bursts, and
 * lets live-spaced frames re-earn painting. Pinned by
 * __tests__/waterfall-freshness.test.ts and the delayed case of
 * components/spectrum/__tests__/scope-hidden-delivery.characterization.test.ts;
 * the SpectrumPanel wiring is pinned separately by the 'wires the
 * waterfall freshness gate into the waterfallPush registration' case in
 * SpectrumPanel.component.test.ts.
 */

/**
 * Delivery silence that separates live streaming from a deferred backlog.
 * Provisional and unmeasured: no MOR-3471 trace on a real macOS Space
 * switch exists yet; revisit against the PR #4246 transition trace.
 */
export const WATERFALL_DISCONTINUITY_MS = 3_000;

/**
 * Spacing under which a delivery counts as burst drain rather than a live
 * frame. Precondition, not a measurement: no drain-spacing trace exists
 * for MOR-3471 yet. The configured producer cadences are the audio-fft
 * scope and the SDR IQ scope, both fps=20 (50 ms production spacing;
 * src/rigplane/audio/fft_scope.py, src/rigplane/sdr/iq_scope.py); the
 * hardware CI-V scope stream has no configured cadence, only an upper
 * bound of ~15 fps (~67 ms; src/rigplane/runtime/_civ_rx.py). Those are
 * production/emission spacings — deliveries at this boundary can batch
 * tighter — so the gate is correct only while a deferred burst drains
 * below this threshold and live deliveries arrive at or above it. If a
 * trace contradicts either side, revisit this constant against it.
 */
export const WATERFALL_RESUME_SPACING_MS = 40;

/** Consecutive live-spaced deliveries needed before painting resumes. */
export const WATERFALL_RESUME_FRAMES = 5;

export type WaterfallClock = { now(): number };

export class WaterfallFreshnessGate {
  private lastDeliveryAt: number | null = null;
  // A fresh gate starts unsaturated: its first delivery paints (no
  // spacing history to judge), then painting is withheld until five
  // consecutive live-spaced deliveries re-earn it. A burst straddling a
  // fresh registration (the providerGeneration remount replaces the
  // gate) therefore paints at most one row instead of replaying fully;
  // a steady stream pays a four-frame suppression after every
  // registration.
  private liveSpaced = 0;

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
