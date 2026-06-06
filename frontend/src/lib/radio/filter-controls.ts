/**
 * filter-controls — pure filter-math helpers shared between runtime props
 * and UI wiring layers.
 *
 * No imports from `components-v2/*`. Safe to use in `$lib/runtime/`.
 */

// PBT raw <-> display conversion
// Reads range from capabilities if available, falls back to IC-7610 defaults
import { getControlRange } from '$lib/stores/capabilities.svelte';

export const FILTER_BIPOLAR_MIN = -1200;
export const FILTER_BIPOLAR_MAX = 1200;
export const FILTER_WIDTH_MIN = 50;
export const FILTER_WIDTH_MAX = 3600;
export const FILTER_WIDTH_STEP = 50;

// Default PBT range (IC-7610 / standard CI-V)
const PBT_DEFAULTS = { rawCenter: 128, displayMin: -1200, displayMax: 1200 } as const;

function pbtRange() {
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

export function pbtRawToHz(raw: number): number {
  const { rawCenter, displayMax } = pbtRange();
  return Math.round((raw - rawCenter) * (displayMax / rawCenter));
}

export function pbtHzToRaw(hz: number): number {
  const { rawCenter, displayMax } = pbtRange();
  const raw = Math.round(hz * (rawCenter / displayMax) + rawCenter);
  return Math.max(0, Math.min(255, raw));
}

// NR-level display <-> CI-V wire conversion (MOR-490)
// The NR-level wire value is a 0-255 BCD level, but the IC-7610 front panel
// (and our slider) shows NR as 0-15.  Read the range from capabilities if
// available, falling back to the IC-7610 mapping so the helper stays safe in
// tests where the runtime is absent.
//
// NOTE: the 0-15 <-> 0-255 mapping quantises 256 wire steps onto 16 physical
// steps, so individual steps may need hardware fine-tuning if the operator
// sees off-by-one step drift on the front panel.
const NR_DEFAULTS = { rawMin: 0, rawMax: 255, displayMin: 0, displayMax: 15 } as const;

function nrRange() {
  try {
    const ctrl = getControlRange('nr_level');
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
  return NR_DEFAULTS;
}

/** Convert a raw 0-255 NR wire value to the 0-15 display value. */
export function nrRawToDisplay(raw: number): number {
  const { rawMin, rawMax, displayMin, displayMax } = nrRange();
  const span = rawMax - rawMin;
  if (span <= 0) return displayMin;
  const display = Math.round(((raw - rawMin) / span) * (displayMax - displayMin) + displayMin);
  return Math.max(displayMin, Math.min(displayMax, display));
}

/** Convert a 0-15 display value to the raw 0-255 NR wire value. */
export function nrDisplayToRaw(display: number): number {
  const { rawMin, rawMax, displayMin, displayMax } = nrRange();
  const span = displayMax - displayMin;
  if (span <= 0) return rawMin;
  const raw = Math.round(((display - displayMin) / span) * (rawMax - rawMin) + rawMin);
  return Math.max(rawMin, Math.min(rawMax, raw));
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
