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
 * its own entry point, which yields value-or-nothing; only this module
 * knows how a vocabulary maps to nothing:
 * - `readingValue` — the `{reading}` status vocabulary (`status === 'known'`
 *   → the value). Call sites: `readingText` itself,
 *   `ReceiverInstrumentCluster: bwRaw` and `meterValue`, and
 *   `VfoIndicatorRow: rfGainShown` (its no-observation fallback).
 * - `observationValue` — the `display.state` vocabulary (current or stale
 *   → the value, anything else → nothing). Call sites:
 *   `VfoIndicatorRow: rfGainShown`, `ReceiverInstrumentHost:
 *   displayFrequency`, `MetersSurface: swrLowerScale`.
 * The third vocabulary — the legacy NaN/null marker on a plain number —
 * has no entry point yet: every drawing site that reads it (the three
 * scalar hosts' `formatValue`s, `meter-renderer-view.ts`,
 * `FilterSurface: numberOf`, the legacy panel guards) is S4b/S5 scope,
 * and an entry point without a caller would be an orphan. It lands in S4b
 * with its first callers.
 *
 * The formatter parameter defaults to `String`; a read-but-falsy value
 * (`0`, `false`, `''`) still renders.
 *
 * Purity: this module imports TYPES ONLY (`InstrumentReading` from the
 * behaviour contract it shares the shape with, type-only). The pin lives
 * in `__tests__/reading-text.test.ts` — do not add a value import here.
 */
import type { InstrumentReading } from './control-instruments/control-instrument-behavior';

/** The ONE core rule: a value or nothing → text; `''` for nothing. */
export function valueText<T>(
  value: T | null | undefined,
  format: (value: T) => string = String,
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
 * may not import, so the shape is restated here. Current or stale carries a
 * value; anything else (unknown, unsupported, idle) is nothing.
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
