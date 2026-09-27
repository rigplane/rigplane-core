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
 * A `null` means the control does not act and the lit state stays unlit;
 * nothing here ever invents a placeholder value.
 *
 * This is a semantic module, not `components/spectrum/spectrum-toolbar-logic`
 * (VfoHeader must not depend on `components/spectrum/`) and not the adapter
 * (`scope-adapter.ts` must not import primitives). Both `components/…` and
 * `components-v2/…` may import `semantic/`, as AmberCockpit already imports
 * `semantic/format-level`.
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
  if (!usable(field)) return null;
  const { value } = field.reading;
  if (!Number.isSafeInteger(value) || value < min || value > max) return null;
  return value;
}

/**
 * The known reading of a boolean scope field, or `null` when the gate fails
 * or the value is not a real boolean.
 */
export function acceptedBoolean(field: InstrumentField<boolean> | undefined): boolean | null {
  if (!usable(field)) return null;
  const { value } = field.reading;
  if (typeof value !== 'boolean') return null;
  return value;
}
