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

/** IC-7610 S-meter fallback mirrors rigs/ic7610.toml. */
const S9_RAW_DEFAULT = 130;
const S9_SCALE_MAX_RAW_DEFAULT = 240;
const S_METER_KNOTS_DEFAULT: [number, number][] = [
  [0, -54],
  [26, -48],
  [52, -36],
  [78, -24],
  [103, -12],
  [S9_RAW_DEFAULT, 0],
  [165, 10],
  [200, 20],
  [S9_SCALE_MAX_RAW_DEFAULT, 40],
];

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
 * Returns S-meter calibration boundaries: [s9Raw, scaleMaxRaw].
 */
function getSmeterBounds(): [number, number] {
  const cal = getMeterCalibration('s_meter');
  if (cal && cal.length >= 2) {
    const s9 = cal.find((p) => p.label === 'S9');
    if (s9) return [s9.raw, cal[cal.length - 1].raw];
    // Fallback: first point is floor, last point is max.
    if (cal.length >= 2) return [cal[0].raw, cal[cal.length - 1].raw];
  }
  return [S9_RAW_DEFAULT, S9_SCALE_MAX_RAW_DEFAULT];
}

function getSmeterKnots(): [number, number][] {
  return calToKnots('s_meter') ?? S_METER_KNOTS_DEFAULT;
}

function getSmeterMaxRaw(): number {
  const knots = getSmeterKnots();
  return knots[knots.length - 1][0];
}

function calibratedSmeterToRaw(actual: number): number {
  const knots = getSmeterKnots();
  const minActual = knots[0][1];
  const maxActual = knots[knots.length - 1][1];
  const clamped = Math.max(minActual, Math.min(maxActual, actual));

  for (let i = 0; i < knots.length - 1; i++) {
    const [raw0, actual0] = knots[i];
    const [raw1, actual1] = knots[i + 1];
    if (clamped <= actual1) {
      const span = actual1 - actual0;
      const t = span === 0 ? 0 : (clamped - actual0) / span;
      return raw0 + t * (raw1 - raw0);
    }
  }

  return knots[knots.length - 1][0];
}

/**
 * Formats calibrated S-meter value (dB relative to S9) as an S-unit string.
 */
export function formatSMeter(actual: number): string {
  const knots = getSmeterKnots();
  const minActual = knots[0][1];
  const maxActual = knots[knots.length - 1][1];
  const clamped = Math.max(minActual, Math.min(maxActual, actual));

  if (clamped >= 0) {
    const over = Math.round(clamped);
    return over > 0 ? `S9+${over}` : 'S9';
  }

  const s = Math.max(0, Math.min(9, Math.floor((clamped - minActual) / 6)));
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
 * IC-7610 CI-V Reference p.4 lists 00 00=0 V, 00 13=10 V, 02 41=16 V, but the
 * manual's raw-13=10 V point is anomalous: interpolating it against the 16 V
 * top knot reads 14.5 V at raw 184, whereas the operator's bench supply is
 * exactly 13.8 V at that same raw value (live-confirmed on a real IC-7610 via
 * /api/v1/state vdMeter:184). The empirical anchor (raw 184 = 13.8 V) corrects
 * the curve while preserving the origin, the documented top, and monotonicity.
 */
const VD_KNOTS: [number, number][] = [
  [0, 0],
  [13, 10],
  [184, 13.8], // operator-measured: raw 184 = 13.8 V supply (the manual's
  // raw-13=10 V point gave a wrong 14.5 V at this reading)
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

/** Bar level for calibrated S-meter values relative to the UI scale full-scale. */
export function sLevel(actual: number): number {
  const scaleMaxRaw = getSmeterMaxRaw();
  const scaled = calibratedSmeterToRaw(actual);
  return scaleMaxRaw > 0 ? Math.max(0, Math.min(1, scaled / scaleMaxRaw)) : 0;
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
      const maxRaw = getSmeterMaxRaw();
      return getSmeterKnots()
        .filter(([, actual]) => [-48, -36, -24, -12, 0, 20, 40].includes(Math.round(actual)))
        .map(([raw, actual]) => ({
          pos: maxRaw > 0 ? Math.max(0, Math.min(1, raw / maxRaw)) : 0,
          label: actual > 0 ? `+${Math.round(actual)}` : `S${Math.round((actual + 54) / 6)}`,
        }));
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
