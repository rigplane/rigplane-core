/**
 * MOR-2688 (slice S1) — the ONE unread-display rule for a reading: text for
 * a read value, `''` for anything not read. An unread value is an unlit LCD
 * slot, never a placeholder glyph (owner rule, 2026-09-21/2026-09-26).
 *
 * Drawing code must not decide "known/unknown" itself; the read/unread
 * distinction stays in behaviour interlocks and the external API (owner
 * decision, 2026-09-27). The predicate is EXACTLY
 * `reading.status === 'known'` — the audit (R1) pins it: widening to
 * `usable`/operational changes behaviour, so no such widening belongs here.
 *
 * The formatter parameter defaults to `String`; a read-but-falsy value
 * (`0`, `false`) still renders.
 *
 * Purity: this module imports TYPES ONLY (`InstrumentReading` from the
 * behaviour contract it shares the shape with, type-only). The pin lives
 * in `__tests__/reading-text.test.ts` — do not add a value import here.
 */
import type { InstrumentReading } from './control-instruments/control-instrument-behavior';

export function readingText<T>(
  source: { readonly reading: InstrumentReading<T> },
  format: (value: T) => string = String,
): string {
  return source.reading.status === 'known' ? format(source.reading.value) : '';
}
