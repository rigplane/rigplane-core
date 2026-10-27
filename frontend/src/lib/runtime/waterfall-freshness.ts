/**
 * Render-boundary freshness gate for the waterfall (MOR-3471).
 *
 * Fix candidate from code analysis, not a confirmed root-cause fix
 * (accepted risk, stated in PR #4267): a frame burst deferred below the
 * app while the page was inactive would be delivered back-to-back once
 * the event loop resumes; painting one waterfall row per delivered frame
 * would replay that burst as fake history at drain speed. The gate
 * classifies each delivery by its spacing from the previous delivery,
 * suppresses burst-spaced frames inside a wall-time-bounded skip window,
 * and paints everything else — including the first frame after every
 * gap, so a normal silence (scope demand off/on, source gap, TX freeze
 * with a silent source) costs no live rows. Pinned by
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
 * for MOR-3471 yet. The configured producers are the audio-fft scope and
 * the SDR IQ scope, both fps=20 (50 ms production spacing; explicit
 * fps=20 in src/rigplane/web/server.py, default fps=20 in
 * src/rigplane/sdr/iq_scope.py); the hardware CI-V scope stream has no
 * configured cadence, only an upper bound of ~15 fps (~67 ms;
 * src/rigplane/runtime/_civ_rx.py). Those are production/emission
 * spacings — live deliveries at this boundary can batch tighter — so the
 * gate collapses only strictly sub-40 ms drains and never suppresses a
 * live stream indefinitely (see WATERFALL_SKIP_BUDGET_MS). If a trace
 * contradicts either side, revisit this constant against it.
 */
export const WATERFALL_BURST_SPACING_MS = 40;

/**
 * Wall-time budget bounding skip mode so it can never freeze the
 * waterfall. The window opens at the first delivery of a fresh
 * registration and re-opens at every discontinuity; inside it,
 * burst-spaced deliveries are suppressed and the first live-spaced
 * delivery latches steady painting. Once the budget is spent, painting
 * resumes regardless of spacing — a live stream whose deliveries batch
 * tighter than WATERFALL_BURST_SPACING_MS (two-frame render batches)
 * still resumes within this budget.
 */
export const WATERFALL_SKIP_BUDGET_MS = 1_500;

export type WaterfallClock = { now(): number };

export class WaterfallFreshnessGate {
  private lastDeliveryAt: number | null = null;
  // Deadline of the currently open skip window. A fresh gate starts
  // unsaturated: its first delivery paints (no spacing history to judge)
  // and opens the window, so a burst straddling a fresh registration
  // (the providerGeneration remount replaces the gate) paints at most
  // one row — while it drains at burst spacing inside the budget.
  private skipUntil = 0;
  // Latched once a live-spaced delivery arrives inside the window or the
  // budget expires; painting then continues regardless of spacing until
  // the next discontinuity.
  private steady = false;

  constructor(private readonly clock: WaterfallClock = { now: () => performance.now() }) {}

  /**
   * Classify one delivery: `true` paints, `false` suppresses. Must run for
   * every delivery that reaches this registration — painted or not,
   * hidden or not, TX-frozen or not (TX-frozen callers discard the paint
   * verdict but still classify) — so the spacing baseline tracks the
   * transport instead of the paint history. A >3 s delivery gap (scope
   * demand off/on, source unavailable, a deferred backlog starting to
   * drain) is a discontinuity: its entering frame paints and re-opens
   * the skip window; inside the window burst-spaced deliveries are
   * suppressed, the first live-spaced delivery paints and latches steady
   * painting, and the budget forces painting back on even if every
   * delivery stays burst-spaced.
   */
  admit(): boolean {
    const now = this.clock.now();
    const last = this.lastDeliveryAt;
    this.lastDeliveryAt = now;
    if (last === null) {
      this.skipUntil = now + WATERFALL_SKIP_BUDGET_MS;
      this.steady = false;
      return true;
    }
    const spacing = now - last;
    if (spacing > WATERFALL_DISCONTINUITY_MS) {
      this.skipUntil = now + WATERFALL_SKIP_BUDGET_MS;
      this.steady = false;
      return true;
    }
    if (this.steady) return true;
    // The forcing timeout bounds skip mode: even a stream that never
    // slows to live spacing resumes painting within the budget.
    if (now >= this.skipUntil || spacing >= WATERFALL_BURST_SPACING_MS) {
      this.steady = true;
      return true;
    }
    return false;
  }
}
