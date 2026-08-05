/**
 * filter-controls — pure filter-math helpers shared between runtime props
 * and UI wiring layers.
 *
 * No imports from `components-v2/*`. Safe to use in `$lib/runtime/`.
 */

// PBT raw <-> display conversion
// Reads range from capabilities if available, falls back to IC-7610 defaults
import { getControlRange } from '$lib/stores/capabilities.svelte';
import type { Capabilities } from '$lib/types/capabilities';

export const FILTER_BIPOLAR_MIN = -1200;
export const FILTER_BIPOLAR_MAX = 1200;
export const FILTER_WIDTH_MIN = 50;
export const FILTER_WIDTH_MAX = 3600;
export const FILTER_WIDTH_STEP = 50;

// Default PBT range (IC-7610 / standard CI-V)
const PBT_DEFAULTS = { rawCenter: 128, displayMin: -1200, displayMax: 1200 } as const;

export type PbtRange = { rawCenter: number; displayMin: number; displayMax: number };

function pbtRange(): PbtRange {
  try {
    const ctrl = getControlRange('pbt_inner');
    if (
      ctrl &&
      ctrl.raw_center !== undefined &&
      ctrl.display_min !== undefined &&
      ctrl.display_max !== undefined
    ) {
      return {
        rawCenter: ctrl.raw_center,
        displayMin: ctrl.display_min,
        displayMax: ctrl.display_max,
      };
    }
  } catch {
    // capabilities store not available (e.g. in tests)
  }
  return PBT_DEFAULTS;
}

/**
 * Derives a `PbtRange` explicitly from a `Capabilities` object's own
 * `controls.pbt_inner` entry (MOR-1284 F1) — the same shape `pbtRange()`
 * reads from the capabilities STORE singleton, but sourced from an argument
 * a caller already has in hand rather than a module-global. Returns
 * `undefined` when the caps object carries no usable range, so `pbtRawToHz`/
 * `pbtHzToRaw` fall through to their own store-lookup default.
 */
export function pbtRangeFromCaps(caps: Capabilities | null | undefined): PbtRange | undefined {
  const ctrl = caps?.controls?.pbt_inner;
  if (
    ctrl &&
    ctrl.raw_center !== undefined &&
    ctrl.display_min !== undefined &&
    ctrl.display_max !== undefined
  ) {
    return { rawCenter: ctrl.raw_center, displayMin: ctrl.display_min, displayMax: ctrl.display_max };
  }
  return undefined;
}

/**
 * `range`, when supplied (MOR-1284 F1), is used INSTEAD of the capabilities
 * STORE lookup — pass `pbtRangeFromCaps(caps)` from a caller that already
 * holds a `caps` argument so the conversion is a pure function of that
 * argument rather than a hidden dependency on module-global store state
 * (the fabrication class MOR-1280's F2 fix closed for filterWidthMin/Max).
 * Every EXISTING call site omits `range` and keeps today's store-lookup
 * behavior unchanged — this parameter is strictly additive.
 */
export function pbtRawToHz(raw: number, range?: PbtRange): number {
  const { rawCenter, displayMax } = range ?? pbtRange();
  return Math.round((raw - rawCenter) * (displayMax / rawCenter));
}

export function pbtHzToRaw(hz: number): number {
  const { rawCenter, displayMax } = pbtRange();
  const raw = Math.round(hz * (rawCenter / displayMax) + rawCenter);
  return Math.max(0, Math.min(255, raw));
}

// Generic control display <-> CI-V wire conversion (MOR-490 / MOR-498)
// Some IC-7610 controls expose a CI-V wire value on a different scale than the
// front-panel / slider display (e.g. NR level wire 0-255 vs display 0-15; NB
// depth wire 0-9 vs display 1-10).  Read the range from capabilities if
// available, falling back to a per-control default so the helper stays correct
// in tests and before a server restart (stale capabilities).
//
// NOTE: when the wire scale is wider than the display scale the mapping
// quantises wire steps onto fewer physical steps, so individual steps may need
// hardware fine-tuning if the operator sees off-by-one step drift.
type ControlRange = {
  rawMin: number;
  rawMax: number;
  displayMin: number;
  displayMax: number;
};

const CONTROL_DEFAULTS: Record<string, ControlRange> = {
  nr_level: { rawMin: 0, rawMax: 255, displayMin: 0, displayMax: 15 },
  nb_depth: { rawMin: 0, rawMax: 9, displayMin: 1, displayMax: 10 },
};

function controlRange(key: string, fallback: ControlRange): ControlRange {
  try {
    const ctrl = getControlRange(key);
    if (
      ctrl &&
      ctrl.display_min !== undefined &&
      ctrl.display_max !== undefined &&
      ctrl.display_max > ctrl.display_min
    ) {
      return {
        rawMin: ctrl.raw_min,
        rawMax: ctrl.raw_max,
        displayMin: ctrl.display_min,
        displayMax: ctrl.display_max,
      };
    }
  } catch {
    // capabilities store not available (e.g. in tests)
  }
  return fallback;
}

/** Same shape as the internal (unexported) `ControlRange`, exported under its
 *  own name (MOR-1290) so `controlRangeFromCaps`'s return type — and the
 *  optional `range` parameter it feeds `controlRawToDisplay` — can be named
 *  by callers outside this module, the same reason `PbtRange` (MOR-1284) is
 *  exported rather than the conversion functions staying un-parameterisable. */
export type ControlDisplayRange = ControlRange;

/**
 * Derives a `ControlDisplayRange` explicitly from a `Capabilities` object's
 * own `controls[key]` entry (MOR-1290, following the `pbtRangeFromCaps`
 * precedent, MOR-1284 F1) — the same shape `controlRange()` reads from the
 * capabilities STORE singleton, but sourced from an argument a caller
 * already has in hand rather than a module-global. Returns `undefined` when
 * the caps object carries no usable range for `key`, so `controlRawToDisplay`
 * falls through to its own store-lookup default.
 */
export function controlRangeFromCaps(
  key: string, caps: Capabilities | null | undefined,
): ControlDisplayRange | undefined {
  const ctrl = caps?.controls?.[key];
  if (
    ctrl &&
    ctrl.display_min !== undefined &&
    ctrl.display_max !== undefined &&
    ctrl.display_max > ctrl.display_min
  ) {
    return {
      rawMin: ctrl.raw_min, rawMax: ctrl.raw_max,
      displayMin: ctrl.display_min, displayMax: ctrl.display_max,
    };
  }
  return undefined;
}

/**
 * `controlRangeFromCaps(key, caps)`, falling back to this module's own
 * `CONTROL_DEFAULTS[key]` when caps carries no range for `key` at all
 * (MOR-1290 F1, verify round 1). `controlRangeFromCaps` alone still has one
 * honest "I don't know" outcome — `undefined` — and a caller that passes
 * that straight into `controlRawToDisplay`'s optional `range` falls through
 * to that function's OWN store lookup, making the result a function of
 * module-global state again for exactly the caps-omits-the-key case. This
 * wrapper closes that residual: every caller gets a CONCRETE range either
 * way, so passing its result as `range` never reaches the store — the
 * conversion becomes a pure function of `(raw, caps)` with no residual
 * dependency. Only defined for keys with a `CONTROL_DEFAULTS` entry
 * (`nr_level`, `nb_depth` today); throws for any other key so a typo fails
 * loudly rather than silently degrading to `undefined` mid-computation.
 */
export function controlRangeFromCapsOrDefault(
  key: string, caps: Capabilities | null | undefined,
): ControlDisplayRange {
  const fallback = CONTROL_DEFAULTS[key];
  if (!fallback) throw new Error(`controlRangeFromCapsOrDefault: no CONTROL_DEFAULTS entry for '${key}'`);
  return controlRangeFromCaps(key, caps) ?? fallback;
}

/**
 * `range`, when supplied (MOR-1290, mirroring `pbtRawToHz`'s own `range`
 * parameter), is used INSTEAD of the capabilities STORE lookup — pass
 * `controlRangeFromCaps(key, caps)` from a caller that already holds a
 * `caps` argument so the conversion is a pure function of that argument
 * rather than a hidden dependency on module-global store state. Every
 * EXISTING call site omits `range` and keeps today's store-lookup behavior
 * unchanged — this parameter is strictly additive. */
export function controlRawToDisplay(
  key: string, raw: number, fallback: ControlRange, range?: ControlDisplayRange,
): number {
  const { rawMin, rawMax, displayMin, displayMax } = range ?? controlRange(key, fallback);
  const span = rawMax - rawMin;
  if (span <= 0) return displayMin;
  const display = Math.round(((raw - rawMin) / span) * (displayMax - displayMin) + displayMin);
  return Math.max(displayMin, Math.min(displayMax, display));
}

/** Convert a slider display value to the raw CI-V wire value for `key`. */
export function controlDisplayToRaw(key: string, display: number, fallback: ControlRange): number {
  const { rawMin, rawMax, displayMin, displayMax } = controlRange(key, fallback);
  const span = displayMax - displayMin;
  if (span <= 0) return rawMin;
  const raw = Math.round(((display - displayMin) / span) * (rawMax - rawMin) + rawMin);
  return Math.max(rawMin, Math.min(rawMax, raw));
}

/** Convert a raw 0-255 NR wire value to the 0-15 display value. `range`
 *  (MOR-1290) is strictly additive — see `controlRawToDisplay`. */
export function nrRawToDisplay(raw: number, range?: ControlDisplayRange): number {
  return controlRawToDisplay('nr_level', raw, CONTROL_DEFAULTS.nr_level, range);
}

/** Convert a 0-15 display value to the raw 0-255 NR wire value. */
export function nrDisplayToRaw(display: number): number {
  return controlDisplayToRaw('nr_level', display, CONTROL_DEFAULTS.nr_level);
}

/** Convert a raw 0-9 NB-depth wire value to the 1-10 display value. `range`
 *  (MOR-1290) is strictly additive — see `controlRawToDisplay`. */
export function nbDepthRawToDisplay(raw: number, range?: ControlDisplayRange): number {
  return controlRawToDisplay('nb_depth', raw, CONTROL_DEFAULTS.nb_depth, range);
}

/** Convert a 1-10 display value to the raw 0-9 NB-depth wire value. */
export function nbDepthDisplayToRaw(display: number): number {
  return controlDisplayToRaw('nb_depth', display, CONTROL_DEFAULTS.nb_depth);
}

function clampToBipolarRange(value: number): number {
  return Math.max(FILTER_BIPOLAR_MIN, Math.min(FILTER_BIPOLAR_MAX, Math.round(value)));
}

export function clampFilterWidth(
  value: number,
  maxHz: number = FILTER_WIDTH_MAX,
  stepHz: number = FILTER_WIDTH_STEP,
): number {
  const clamped = Math.max(FILTER_WIDTH_MIN, Math.min(maxHz, value));
  return Math.round(clamped / stepHz) * stepHz;
}

export function deriveIfShift(pbtInner: number, pbtOuter: number): number {
  return clampToBipolarRange((pbtInner + pbtOuter) / 2);
}

export function mapIfShiftToPbt(
  targetIfShift: number,
  currentPbtInner: number,
  currentPbtOuter: number,
): { pbtInner: number; pbtOuter: number } {
  const currentIfShift = deriveIfShift(currentPbtInner, currentPbtOuter);
  const delta = clampToBipolarRange(targetIfShift) - currentIfShift;

  return {
    pbtInner: clampToBipolarRange(currentPbtInner + delta),
    pbtOuter: clampToBipolarRange(currentPbtOuter + delta),
  };
}
