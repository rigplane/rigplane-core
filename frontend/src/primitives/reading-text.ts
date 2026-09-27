/**
 * MOR-2688 (slice S1) — the ONE unread-display rule for a reading: text for
 * a read value, `''` for anything not read. An unread value is an unlit LCD
 * slot, never a placeholder glyph (owner rule, 2026-09-21/2026-09-26).
 *
 * Drawing code must not decide "known/unknown" itself; the read/unread
 * distinction stays in behaviour interlocks and the external API (owner
 * decision, 2026-09-27). For the `{reading}` status vocabulary the
 * predicate is EXACTLY `reading.status === 'known'` —
 * `.claude/audits/2026-09-27-mechanism-audit-mor2688-design.md`, Q7, R1
 * pins it: widening to `usable`/operational changes behaviour, so no such
 * widening belongs here.
 *
 * MOR-2688 (slice S4a) — one core rule, `valueText`: a value or nothing
 * in, text out (`''` for nothing). Each unread vocabulary enters through
 * its own entry point, which yields value-or-nothing:
 * - `readingValue` — the `{reading}` status vocabulary (`status === 'known'`
 *   → the value).
 * - `observationValue` — the `display.state` vocabulary (current or stale
 *   → the value, anything else → nothing).
 * - `finiteValue` — the legacy NaN/null/Infinity marker vocabulary (a
 *   finite number → the number, NaN/±Infinity/null/undefined → nothing).
 *
 * A read-but-falsy value (`0`, `false`, `''`) still renders;
 * `readingText`'s formatter defaults to `String`.
 *
 * Purity: this module imports TYPES ONLY (`InstrumentReading` from the
 * behaviour contract it shares the shape with, type-only). The pin lives
 * in `__tests__/reading-text.test.ts` — do not add a value import here.
 */
import type { InstrumentReading } from './control-instruments/control-instrument-behavior';

/** The ONE core rule: a value or nothing → text; `''` for nothing. */
export function valueText<T>(
  value: T | null | undefined,
  format: (value: T) => string,
): string {
  return value === null || value === undefined ? '' : format(value);
}

/**
 * Vocabulary 1 — the `{reading}` status: `known` → the value, `unknown`
 * (or no source at all) → nothing. `readingText`'s predicate, unchanged.
 */
export function readingValue<T>(
  field: { readonly reading: InstrumentReading<T> } | undefined,
): T | null {
  return field !== undefined && field.reading.status === 'known'
    ? field.reading.value
    : null;
}

export function readingText<T>(
  source: { readonly reading: InstrumentReading<T> },
  format: (value: T) => string = String,
): string {
  return valueText(readingValue(source), format);
}

/**
 * Vocabulary 2 — the display observation, as a structural type:
 * `DisplayObservation<T>` is owned by `semantic/`, which `primitives/`
 * may not import, so a structural shape lives here. Current or stale
 * carries a value; anything else (unknown, unsupported) is nothing.
 */
export type ValueObservation<T> = Readonly<{
  readonly state: string;
  readonly value?: T;
}>;

export function observationValue<T>(
  observation: ValueObservation<T> | undefined,
): T | null {
  if (observation === undefined) return null;
  return observation.state === 'current' || observation.state === 'stale'
    ? observation.value ?? null
    : null;
}

/**
 * Vocabulary 3 — the legacy NaN/null/Infinity marker: a finite number →
 * the number; NaN, ±Infinity, null and undefined → nothing.
 */
export function finiteValue(value: number | null | undefined): number | null {
  return value !== null && value !== undefined && Number.isFinite(value) ? value : null;
}
