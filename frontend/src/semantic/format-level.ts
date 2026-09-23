/**
 * Shared readout formatter for semantic-surface level sliders (MOR-1447).
 *
 * A KNOWN reading on a control declared with a 0..1-wide domain (RF gain,
 * squelch, RF power, …) is a wire-protocol fraction, not a number meant for
 * display — rendering it with `String(value)` is what produced the live
 * IC-7300 regression (`0.8196078431372549` on screen instead of "82%").
 * Every OTHER declared domain (raw 0..255 levels, second counts, …) already
 * reads as a clean integer and is left untouched.
 *
 * Generic over the declared `[min, max]` domain — the same tuple the surface
 * already passes to its `<input type="range">` — rather than a field name or
 * a radio-vendor branch, so this stays correct for any future control that
 * declares the same 0..1 fraction shape.
 */
export function formatKnownLevel(value: number, min: number, max: number): string {
  if (min === 0 && max === 1) {
    return `${Math.round(value * 100)}%`;
  }
  return String(value);
}

/**
 * Whether a KNOWN level FORMATS below its domain maximum — decided on what
 * the operator reads, not on the raw wire value. On a 0..1 fraction domain
 * a raw 254 of 255 is strictly below 1, yet `formatKnownLevel` rounds it to
 * "100%"; for a reduced-gain annunciator a reading that formats as the
 * maximum IS the maximum, so this compares the two formatted strings
 * (single rounding, no second copy of the rule) instead of the raw numbers.
 */
export function levelFormatsBelowMax(value: number, min: number, max: number): boolean {
  return formatKnownLevel(value, min, max) !== formatKnownLevel(max, min, max);
}
