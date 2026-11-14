/**
 * Shared analysis for the MOR-4451 held-key acceptance
 * (frontend/tests/e2e/held-key/panorama-held-key.spec.ts).
 *
 * The spec samples the VFO readout, the tune marker and the rendered view
 * centre at rAF rate while ArrowRight is held. `checkMonotonic` alone cannot
 * reject a run that measured nothing (empty or all-missing series) or a
 * viewport that never moved (frozen series): both are trivially monotonic.
 * These helpers close those gaps, and `classifyHeldKeyCatch` keeps the
 * spec's skip rules explicit and unit-pinned.
 */

export interface HeldKeyPoint {
  t: number;
  v: number | null;
}

export interface HeldKeySeriesOutcome {
  samples: number;
  missing: number;
}

/** Minimum valid samples every measured series must contribute over the burst. */
export const MIN_HELD_KEY_SAMPLES = 10;

/** A measured series with too few valid samples is unmeasured, never passing. */
export function requirePopulatedSeries(
  name: string,
  outcome: HeldKeySeriesOutcome,
  minimumSamples: number = MIN_HELD_KEY_SAMPLES,
): void {
  if (outcome.samples < minimumSamples) {
    throw new Error(
      `${name}: only ${outcome.samples} valid samples (${outcome.missing} missing); ` +
      `need at least ${minimumSamples} — the series was never measured`,
    );
  }
}

/** A rendered series must move with the burst and rest at the confirmed frequency. */
export function checkAdvanceAndSettle(input: {
  name: string;
  values: HeldKeyPoint[];
  confirmedHz: number;
  toleranceHz: number;
  minimumAdvanceHz: number;
}): { advanceHz: number; settleHz: number } {
  const measured = input.values
    .map((point) => point.v)
    .filter((v): v is number => v !== null && Number.isFinite(v));
  if (measured.length === 0) {
    throw new Error(`${input.name}: no valid samples; cannot prove advance or settle`);
  }
  const advanceHz = Math.max(...measured) - Math.min(...measured);
  if (advanceHz < input.minimumAdvanceHz) {
    throw new Error(
      `${input.name}: frozen — advanced only ${advanceHz}Hz over the burst, ` +
      `need at least ${input.minimumAdvanceHz}Hz`,
    );
  }
  const lastHz = measured[measured.length - 1];
  const settleHz = Math.abs(lastHz - input.confirmedHz);
  if (settleHz > input.toleranceHz) {
    throw new Error(
      `${input.name}: did not settle — last ${lastHz}Hz vs confirmed ` +
      `${input.confirmedHz}Hz (off ${settleHz}Hz, tolerance ${input.toleranceHz}Hz)`,
    );
  }
  return { advanceHz, settleHz };
}

export interface HeldKeyTransientInput {
  /** Configured CI-V response delay in seconds for the row (0 = no lag). */
  responseDelayS: number;
  /** Frames in the burst window whose coverage is narrower than the view. */
  partialCount: number;
  /** Burst-window rAF samples showing scope hatch. */
  hatchedCount: number;
}

/**
 * Positive proof that the latency seam was exercised: a row with response
 * lag must have OBSERVED partially covered frames during the burst (the
 * transient itself), and none of them may hatch. A lagged row with zero
 * partials never met the transient — it passes vacuously without this
 * requirement — and any hatch during the transient is the old rendering
 * behaviour. Throws on both failures; a delay-free row has no transient to
 * observe and always passes.
 */
export function requireTransientSeamObserved(input: HeldKeyTransientInput): void {
  if (!(input.responseDelayS > 0)) return;
  if (input.partialCount === 0) {
    throw new Error(
      `transient not exercised: response delay ${input.responseDelayS}s but zero ` +
      `partially covered frames in the burst window — the lag seam was never observed`,
    );
  }
  if (input.hatchedCount > 0) {
    throw new Error(
      `transient margin hatched in ${input.hatchedCount} burst samples with ` +
      `${input.partialCount} partially covered frames — the lag seam must stay unhatched`,
    );
  }
}

export interface HeldKeyCatchInput {
  setupDone: boolean;
  serverReady: boolean;
  framesObserved: number;
  error: unknown;
  row: string;
}

export type HeldKeyCatchVerdict =
  | { disposition: 'skip'; reason: string }
  | { disposition: 'fail'; error: unknown };

/**
 * Skip only for a genuine connect failure (the server never started, or no
 * scope frames arrived at all). Anything else fails with the original error.
 */
export function classifyHeldKeyCatch(input: HeldKeyCatchInput): HeldKeyCatchVerdict {
  const detail = input.error instanceof Error ? input.error.message.split('\n')[0] : String(input.error);
  if (!input.serverReady) {
    return {
      disposition: 'skip',
      reason:
        `MOR-4451 held-key acceptance (${input.row}): the journey server failed to ` +
        `start on this tree (${detail})`,
    };
  }
  if (!input.setupDone && input.framesObserved === 0) {
    return {
      disposition: 'skip',
      reason:
        `MOR-4451 held-key acceptance (${input.row}): zero scope frames arrived ` +
        `within the timeout on this tree (${detail})`,
    };
  }
  return { disposition: 'fail', error: input.error };
}
