/**
 * MOR-2704 (G5a) — one `acceptedNumber` and one `acceptedBoolean` for the
 * scope controls.
 *
 * The three scope surfaces (`components-v2/layout/VfoHeader.svelte`,
 * `components/spectrum/ScopeSettingsPopover.svelte` and
 * `components/spectrum/SpectrumToolbar.svelte`) each grew the same pair of
 * local helpers: the fail-closed gate (`usable` from G0,
 * `primitives/control-instruments/control-instrument-behavior`) plus a value
 * check — a safe integer inside the declared `[min, max]` domain for
 * numbers, a real boolean for booleans — returning the value or `null`.
 * A field whose `availability` is itself undefined is rejected too, exactly
 * like the former local copies' `fact?.availability?.structural` tolerance.
 * A `null` means the control does not act and the lit state stays unlit;
 * nothing here ever invents a placeholder value.
 *
 * This is a semantic module, not `components/spectrum/spectrum-toolbar-logic`
 * and not the adapter (`scope-adapter.ts` must not import primitives).
 */
import {
  usable,
  type InstrumentField,
} from '../primitives/control-instruments/control-instrument-behavior';

/**
 * The known reading of a numeric scope field, or `null` when the gate fails
 * or the value falls outside the declared `[min, max]` domain or is not a
 * safe integer (e.g. a wire float or NaN).
 */
export function acceptedNumber(
  field: InstrumentField<number> | undefined,
  min: number,
  max: number,
): number | null {
  if (field === undefined || field.availability === undefined) return null;
  if (!usable(field)) return null;
  const { value } = field.reading;
  if (!Number.isSafeInteger(value) || value < min || value > max) return null;
  return value;
}

/**
 * MOR-3694 — the same gate for a DECIMAL level readback: the value must be
 * a finite number inside the declared `[min, max]` range, but not an
 * integer (the REF readback is half-dB on the shipped Icom rigs, e.g.
 * -4.5). The integer-only `acceptedNumber` above stays the gate for the
 * ordinal fields (mode/span/speed/edge).
 */
export function acceptedLevel(
  field: InstrumentField<number> | undefined,
  min: number,
  max: number,
): number | null {
  if (field === undefined || field.availability === undefined) return null;
  if (!usable(field)) return null;
  const { value } = field.reading;
  if (typeof value !== 'number' || !Number.isFinite(value)
    || value < min || value > max) return null;
  return value;
}

/**
 * The known reading of a boolean scope field, or `null` when the gate fails
 * or the value is not a real boolean.
 */
export function acceptedBoolean(field: InstrumentField<boolean> | undefined): boolean | null {
  if (field === undefined || field.availability === undefined) return null;
  if (!usable(field)) return null;
  const { value } = field.reading;
  if (typeof value !== 'boolean') return null;
  return value;
}
