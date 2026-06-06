// Capability-derived calibration and redline data is routed through the
// runtime adapter (Tier 2 batch 2) so this helper no longer reaches into
// `$lib/stores/*` directly. The adapter returns `null` when capabilities
// haven't loaded — formatters fall back to the hardcoded IC-7610 knots
// defined below.
import {
  getMeterCalibration,
  getMeterRedline,
} from '$lib/runtime/adapters/capabilities-adapter';

export type MeterSource = 'S' | 'SWR' | 'POWER' | 'po';

export interface Mark {
  pos: number;
  label: string;
  color?: string;
}

/**
 * Clamps and normalizes a raw BCD meter value to 0-1.
 * IC-7610 CI-V meters use 0-255 BCD range (00 00 to 02 55).
 */
export function normalize(raw: number): number {
  return Math.max(0, Math.min(255, raw)) / 255;
}

/**
 * Piecewise linear interpolation over knot points.
 */
function piecewise(raw: number, knots: [number, number][]): number {
  const clamped = Math.max(knots[0][0], Math.min(knots[knots.length - 1][0], raw));
  for (let i = 0; i < knots.length - 1; i++) {
    const [r0, v0] = knots[i];
    const [r1, v1] = knots[i + 1];
    if (clamped <= r1) {
      const t = r1 === r0 ? 0 : (clamped - r0) / (r1 - r0);
      return v0 + t * (v1 - v0);
    }
  }
  return knots[knots.length - 1][1];
}

/**
 * Converts a capabilities MeterCalPoint[] to piecewise knots [raw, actual][].
 * Returns null if calibration data is unavailable.
 */
function calToKnots(meterType: string): [number, number][] | null {
  const cal = getMeterCalibration(meterType);
  if (!cal || cal.length < 2) return null;
  return cal.map((p) => [p.raw, p.actual] as [number, number]);
}

/**
 * Returns knots from capabilities, falling back to hardcoded defaults.
 */
function getKnots(meterType: string, fallback: [number, number][]): [number, number][] {
  return calToKnots(meterType) ?? fallback;
}

// ---- Hardcoded IC-7610 fallback constants ----

/**
 * IC-7610 CI-V Reference p.4: 00 00=0%, 01 43=50%, 02 12=100%
 */
const PO_KNOTS: [number, number][] = [
  [0, 0],
  [143, 50],
  [212, 100],
];

/**
 * IC-7610 CI-V Reference p.4: 00 00=1.0, 00 48=1.5, 00 80=2.0, 01 20=3.0
 */
const SWR_KNOTS: [number, number][] = [
  [0, 1.0],
  [48, 1.5],
  [80, 2.0],
  [120, 3.0],
];

/** IC-7610 ALC max raw value */
const ALC_MAX_DEFAULT = 120;

/** IC-7610 S-meter: S9 = raw 120, S9+60 = raw 241 */
const S9_RAW_DEFAULT = 120;
const S9_PLUS60_RAW_DEFAULT = 241;

// ---- Public formatters ----

/**
 * Formats raw RF power (BCD 0-255) as watts string.
 */
export function formatPowerWatts(raw: number): string {
  const knots = getKnots('power', PO_KNOTS);
  const watts = Math.round(piecewise(raw, knots));
  return `${watts}W`;
}

/**
 * Normalizes RF power for bar gauge (0-1 scale).
 */
export function normalizePower(raw: number): number {
  const knots = getKnots('power', PO_KNOTS);
  const maxWatts = knots[knots.length - 1][1];
  return maxWatts > 0 ? piecewise(raw, knots) / maxWatts : 0;
}

/**
 * Formats raw SWR value (BCD 0-255) as SWR ratio string.
 */
export function formatSwr(raw: number): string {
  if (raw >= 255) return '∞';
  const knots = getKnots('swr', SWR_KNOTS);
  return piecewise(raw, knots).toFixed(1);
}

/**
 * Formats raw ALC value (BCD 0-255) as percentage string.
 */
export function formatAlc(raw: number): string {
  const alcMax = getMeterRedline('alc') ?? ALC_MAX_DEFAULT;
  const pct = Math.round((Math.max(0, Math.min(alcMax, raw)) / alcMax) * 100);
  return `${pct}%`;
}

/**
 * Returns S-meter calibration boundaries: [s9Raw, s9Plus60Raw].
 */
function getSmeterBounds(): [number, number] {
  const cal = getMeterCalibration('s_meter');
  if (cal && cal.length >= 2) {
    // Find S9 and S9+60 calibration points
    const s9 = cal.find((p) => p.label === 'S9');
    const s9p60 = cal.find((p) => p.label === 'S9+60dB' || p.label === 'S9+60');
    if (s9 && s9p60) return [s9.raw, s9p60.raw];
    // Fallback: last two points as S9 boundary and max
    if (cal.length >= 2) return [cal[cal.length - 2].raw, cal[cal.length - 1].raw];
  }
  return [S9_RAW_DEFAULT, S9_PLUS60_RAW_DEFAULT];
}

/**
 * Formats raw S-meter value (BCD 0-255) as an S-unit string.
 */
export function formatSMeter(raw: number): string {
  const [s9Raw, s9Plus60Raw] = getSmeterBounds();
  if (raw >= s9Raw) {
    // S9+ range
    const span = s9Plus60Raw - s9Raw;
    const db = span > 0 ? Math.round(((raw - s9Raw) / span) * 60) : 0;
    return db > 0 ? `S9+${db}` : 'S9';
  }
  // S0-S9 range
  const s = s9Raw > 0 ? Math.round((raw / s9Raw) * 9) : 0;
  return `S${s}`;
}

/**
 * Returns needle gauge mark positions and labels for the given meter source.
 * Uses capabilities calibration data when available, IC-7610 defaults otherwise.
 */
/**
 * Formats raw Id (drain current) value as amps string.
 * IC-7610 CI-V Reference p.4: 00 00=0 A, 00 97=10 A, 01 43=15 A, 02 12=25 A.
 * Falls back to capabilities calibration when present.
 */
const ID_KNOTS: [number, number][] = [
  [0, 0],
  [151, 10],
  [195, 15],
  [212, 25],
];

export function formatAmps(raw: number): string {
  const knots = getKnots('id', ID_KNOTS);
  const amps = piecewise(raw, knots);
  return `${amps.toFixed(1)} A`;
}

/**
 * Formats raw Vd (drain voltage) value as volts string.
 * IC-7610 CI-V Reference p.4: 00 00=0 V, 00 13=10 V, 02 41=16 V.
 * Nominal reading on-air is ~13.8 V.
 */
const VD_KNOTS: [number, number][] = [
  [0, 0],
  [13, 10],
  [241, 16],
];

export function formatVolts(raw: number): string {
  const knots = getKnots('vd', VD_KNOTS);
  const volts = piecewise(raw, knots);
  return `${volts.toFixed(1)} V`;
}

/**
 * Formats raw COMP (speech compressor) value as dB string.
 * IC-7610 CI-V Reference p.4: 00 00=0 dB, 00 75=15 dB, 01 50=30 dB.
 */
const COMP_KNOTS: [number, number][] = [
  [0, 0],
  [75, 15],
  [150, 30],
];

export function formatCompDb(raw: number): string {
  const knots = getKnots('comp', COMP_KNOTS);
  const db = Math.round(piecewise(raw, knots));
  return `${db} dB`;
}

/**
 * Returns the interpolated SWR ratio for a raw BCD value. Pure numeric
 * companion to `formatSwr` — used for threshold comparisons.
 */
export function swrRatio(raw: number): number {
  if (raw >= 255) return Infinity;
  const knots = getKnots('swr', SWR_KNOTS);
  return piecewise(raw, knots);
}

/**
 * Returns the normalized ALC level (0-1) for a raw BCD value, relative to
 * the ALC redline from capabilities (fallback: IC-7610 default 120).
 */
export function alcLevel(raw: number): number {
  const alcMax = getMeterRedline('alc') ?? ALC_MAX_DEFAULT;
  return Math.max(0, Math.min(alcMax, raw)) / alcMax;
}

/**
 * Bar-fill level normalizers (0-1) in the CALIBRATED domain (MOR-482).
 *
 * The numeric readouts (`formatSwr`/`formatAmps`/…) are already calibrated via
 * the piecewise knots, but the bar fill historically used `normalize(raw)` =
 * raw/255, so the bar disagreed with the number (e.g. Vd 13.8 V → ~5% bar,
 * SWR 3.0 → 47% bar). These helpers convert the raw value to its engineering
 * quantity, then normalize against that meter's full-scale / redline knot so
 * the bar matches the number. Written in the calibrated domain so the Phase-2
 * cutover (backend emitting engineering units) is a one-line change per meter.
 */

/** Bar level for SWR: ratio relative to the 3.0 full-scale knot. ∞ → 1.0. */
export function swrLevel(raw: number): number {
  const ratio = swrRatio(raw);
  if (!Number.isFinite(ratio)) return 1;
  const knots = getKnots('swr', SWR_KNOTS);
  const maxRatio = knots[knots.length - 1][1];
  return maxRatio > 0 ? Math.max(0, Math.min(1, ratio / maxRatio)) : 0;
}

/** Bar level for Id: amps relative to the 25 A full-scale knot. */
export function idLevel(raw: number): number {
  const knots = getKnots('id', ID_KNOTS);
  const maxAmps = knots[knots.length - 1][1];
  return maxAmps > 0 ? Math.max(0, Math.min(1, piecewise(raw, knots) / maxAmps)) : 0;
}

/** Bar level for Vd: volts relative to the 16 V full-scale knot. */
export function vdLevel(raw: number): number {
  const knots = getKnots('vd', VD_KNOTS);
  const maxVolts = knots[knots.length - 1][1];
  return maxVolts > 0 ? Math.max(0, Math.min(1, piecewise(raw, knots) / maxVolts)) : 0;
}

/** Bar level for COMP: dB relative to the 30 dB full-scale knot. */
export function compLevel(raw: number): number {
  const knots = getKnots('comp', COMP_KNOTS);
  const maxDb = knots[knots.length - 1][1];
  return maxDb > 0 ? Math.max(0, Math.min(1, piecewise(raw, knots) / maxDb)) : 0;
}

/** Bar level for S-meter: raw relative to the S9+60 full-scale calibration. */
export function sLevel(raw: number): number {
  const [, s9Plus60Raw] = getSmeterBounds();
  return s9Plus60Raw > 0 ? Math.max(0, Math.min(1, raw / s9Plus60Raw)) : 0;
}

/** True when SWR exceeds the 2.0 TX-safety threshold. */
export function isSwrFault(raw: number): boolean {
  return swrRatio(raw) > 2.0;
}

/** True when ALC is driven past 90% of the redline. */
export function isAlcFault(raw: number): boolean {
  return alcLevel(raw) > 0.9;
}

/**
 * Peak-hold state tracker (#823).
 *
 * Holds the latched peak value and its timestamp. The decayed display value
 * is computed per-render from the elapsed time (see `peakHoldDisplay`) so
 * the decay is strictly linear across the `decayMs` window — storing a
 * pre-decayed value and repeatedly decaying it would produce exponential
 * (compounding) decay instead.
 *
 * Pure function over state — callers schedule the tick.
 */
export interface PeakHoldState {
  latchedPeak: number;
  latchedAt: number;
}

export function updatePeakHold(
  state: PeakHoldState | undefined,
  current: number,
  now: number,
  decayMs = 2000,
): PeakHoldState {
  if (!state || current > state.latchedPeak) {
    return { latchedPeak: current, latchedAt: now };
  }
  // Once the decay window has fully elapsed the latched peak is no longer
  // visible; re-seat the anchor to `current` so future samples decay from a
  // fresh baseline.
  if (now - state.latchedAt >= decayMs) {
    return { latchedPeak: current, latchedAt: now };
  }
  return state;
}

/**
 * Computes the displayed peak value for the current render frame.
 * The latched peak decays linearly to 0 across `decayMs`; the live
 * `current` sample floors the result so a rising signal is never masked
 * by the hold marker.
 */
export function peakHoldDisplay(
  state: PeakHoldState | undefined,
  current: number,
  now: number,
  decayMs = 2000,
): number {
  if (!state) return current;
  const elapsed = now - state.latchedAt;
  if (elapsed >= decayMs) return current;
  const factor = 1 - elapsed / decayMs;
  const decayed = state.latchedPeak * factor;
  return Math.max(current, decayed);
}

export function getNeedleMarks(source: MeterSource): Mark[] {
  switch (source) {
    case 'S': {
      const [s9Raw, s9Plus60Raw] = getSmeterBounds();
      return [
        { pos: (s9Raw * (1 / 9)) / 255, label: 'S1' },
        { pos: (s9Raw * (3 / 9)) / 255, label: 'S3' },
        { pos: (s9Raw * (5 / 9)) / 255, label: 'S5' },
        { pos: (s9Raw * (7 / 9)) / 255, label: 'S7' },
        { pos: s9Raw / 255, label: 'S9' },
        { pos: (s9Raw + (s9Plus60Raw - s9Raw) * (20 / 60)) / 255, label: '+20' },
        { pos: (s9Raw + (s9Plus60Raw - s9Raw) * (40 / 60)) / 255, label: '+40' },
      ];
    }
    case 'SWR': {
      const knots = getKnots('swr', SWR_KNOTS);
      return knots.map(([rawVal, swrVal]) => ({
        pos: rawVal / 255,
        label: swrVal.toFixed(1),
      }));
    }
    case 'POWER':
    case 'po': {
      return [
        { pos: 0.0, label: '0' },
        { pos: 0.25, label: '25' },
        { pos: 0.5, label: '50' },
        { pos: 0.75, label: '75' },
        { pos: 1.0, label: '100' },
      ];
    }
  }
}
