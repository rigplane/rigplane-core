/**
 * Pure functions and constants extracted from SpectrumToolbar.svelte.
 * All functions are side-effect-free and independently testable.
 */

/* ── Constants ── */

/**
 * Static label for the sweep-speed stepper.
 * Renamed from "SPD" → "SPEED" per design plan 2026-04-18-spectrum-controls §9.
 */
export const SPEED_STATIC_LABEL = 'SPEED';

export function formatSpanHz(spanHz: number): string {
  return `±${spanHz / 2000}k`;
}

export const SPEED_LABELS: Record<number, string> = { 0: 'FST', 1: 'MID', 2: 'SLO' };

/** Scope mode buttons: [modeIndex, label] */
export const MODE_BUTTONS: [number, string][] = [[0, 'CTR'], [1, 'FIX'], [2, 'S-C'], [3, 'S-F']];

/* ── Layer visibility ── */

export function toggleLayer(hiddenLayers: string[], layer: string): string[] {
  return hiddenLayers.includes(layer)
    ? hiddenLayers.filter(l => l !== layer)
    : [...hiddenLayers, layer];
}

export function isLayerVisible(hiddenLayers: string[], layer: string): boolean {
  return !hiddenLayers.includes(layer);
}

/* ── Scope mode predicates ── */

/** SPAN is only meaningful in center modes (0=CTR, 2=S-C) */
export function isSpanApplicable(mode: number | undefined): boolean {
  return mode === 0 || mode === 2;
}

/** EDGE selector is shown in FIX (1) and SCROLL-F (3) modes */
export function isEdgeApplicable(mode: number | undefined): boolean {
  return mode === 1 || mode === 3;
}

/* ── Range clamps ── */

/**
 * Clamp speed value after applying delta.
 * Note: delta is inverted — higher speed value = slower sweep.
 */
export function clampSpeed(current: number, delta: number): number {
  return Math.max(0, Math.min(2, current - delta));
}

export function clampBrt(current: number, delta: number): number {
  return Math.max(-30, Math.min(30, current + delta));
}

/* ── Scope REF stepping (MOR-3694) ── */

/** The radio's declared scope REF adjustable range, from the profile's
 *  `[scope]` section published through capabilities. */
export interface RefRange {
  readonly min: number;
  readonly max: number;
}

/** The REF −/+ button grid, in dB. A UI constant: `ref_step_db` is the
 *  radio's own resolution (0.5 dB on the shipped Icom rigs), not the
 *  button step. */
export const REF_STEP_DB = 5;

/**
 * Next REF value on the {@link REF_STEP_DB} grid from ANY current value,
 * clamped to the declared range.
 *
 * From an off-grid readback the step lands on the NEIGHBOURING grid point
 * in the step's direction (−4 down → −5, −4 up → 0; the half-dB −4.5
 * behaves the same), and from a grid point it moves one full step. The
 * result is always an in-range integer.
 */
export function stepRef(current: number, direction: -1 | 1, range: RefRange): number {
  const stepped = direction < 0
    ? Math.ceil(current / REF_STEP_DB) * REF_STEP_DB - REF_STEP_DB
    : Math.floor(current / REF_STEP_DB) * REF_STEP_DB + REF_STEP_DB;
  return Math.max(range.min, Math.min(range.max, stepped));
}

/**
 * The declared REF range from a capabilities payload, or `null` when the
 * active radio publishes none (older server, profile without `[scope]`
 * range fields). `null` means the REF steppers FAIL CLOSED — there is no
 * fallback range.
 */
export function refRangeFromCaps(
  caps: { ref_min_db?: unknown; ref_max_db?: unknown } | null | undefined,
): RefRange | null {
  const { ref_min_db: min, ref_max_db: max } = caps ?? {};
  return typeof min === 'number' && typeof max === 'number'
    && Number.isFinite(min) && Number.isFinite(max) && min <= max
    ? { min, max }
    : null;
}
