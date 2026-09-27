/**
 * MOR-2688 — the ONE unread-display rule for a reading, extracted after the
 * placeholder wave finally moved past the rule of three (`FilterSurface`,
 * `FilterInstrumentHost` and `ScopeControlsSurface` each re-derived the
 * same ternary in-file; see the mechanism audit of 2026-09-27, F1).
 *
 * Literal-string pins of the rule, plus the purity pin that keeps the
 * module runtime-import-free, modelled on `semantic/pressed-of.test.ts`.
 */
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import {
  finiteValue, observationValue, readingText, readingValue, valueText,
  type ValueObservation,
} from '../reading-text';
import type { InstrumentReading } from '../control-instruments/control-instrument-behavior';

const unread = <T>(): { reading: InstrumentReading<T> } => ({ reading: { status: 'unknown' } });
const known = <T>(value: T): { reading: InstrumentReading<T> } => ({ reading: { status: 'known', value } });
/** Marks the value so a pin can tell "formatted the value" from "printed nothing". */
const mark = (value: unknown): string => `‹${String(value)}›`;

describe('readingText (MOR-2688)', () => {
  it('renders a read value as text', () => {
    expect(readingText(known('USB'))).toBe('USB');
  });

  it('renders an unread reading as EMPTY — the unlit slot, never a placeholder', () => {
    expect(readingText(unread<string>())).toBe('');
  });

  it('applies the formatter argument when given', () => {
    expect(readingText(known(14.28), (v) => v.toFixed(2))).toBe('14.28');
  });

  it('renders a read-but-falsy value (0 and false) — falsy is not unread', () => {
    expect(readingText(known(0))).toBe('0');
    expect(readingText(known(false))).toBe('false');
  });

  /** The "no runtime import" pin, modelled on `semantic/pressed-of.test.ts`:
   *  surfaces that pass their WHOLE field object to this rule must never
   *  acquire a runtime edge into whatever a field import could reach. */
  it('has no runtime import', () => {
    const source = readFileSync('src/primitives/reading-text.ts', 'utf8');
    const statements = [...source.matchAll(/^import\b[^;]*;/gm)].map((m) => m[0]);
    expect(statements.length).toBeGreaterThan(0);
    for (const statement of statements) expect(statement.startsWith('import type ')).toBe(true);
    for (const forbidden of ['import(', 'require(']) expect(source).not.toContain(forbidden);
  });
});

/**
 * MOR-2688 (slice S4a) — the ONE shared table: every entry point drives
 * the same cases through its own vocabulary, so the vocabularies cannot
 * drift apart on `0`, `false`, `''`, `NaN`, a stale observation, or an
 * absent source. `read` is "there is a value"; the vocabularies differ
 * only in how they say it.
 */
const TABLE = [
  { name: 'a read value', value: 'USB', read: true, stale: false, absent: false },
  { name: 'nothing (unread / never observed)', value: 'USB', read: false, stale: false, absent: false },
  { name: '0 — falsy is not unread', value: 0, read: true, stale: false, absent: false },
  { name: 'false — falsy is not unread', value: false, read: true, stale: false, absent: false },
  { name: "'' — a read empty string still formats", value: '', read: true, stale: false, absent: false },
  { name: 'NaN — the vocabulary decides, never the value', value: Number.NaN, read: true, stale: false, absent: false },
  { name: 'a stale observation keeps its value', value: 3600, read: true, stale: true, absent: false },
  { name: 'an absent source is nothing', value: 'USB', read: false, stale: false, absent: true },
  {
    name: 'a value on a not-value state is nothing — the state decides, never the value',
    value: 3600, read: false, stale: false, absent: false, carryValue: true,
  },
] as const;

describe.each(TABLE)('the shared unread table (MOR-2688 S4a): $name', (row) => {
  const expectedText = row.read ? mark(row.value) : '';
  const expectedValue = row.read ? row.value : null;
  const field = row.read ? known(row.value) : unread<typeof row.value>();
  const observation: ValueObservation<typeof row.value> | undefined = row.absent
    ? undefined
    : row.read
      ? { state: row.stale ? 'stale' : 'current', value: row.value }
      : 'carryValue' in row
        ? { state: 'unknown', value: row.value }
        : { state: 'unknown' };

  it('valueText: value or nothing → text, "" for nothing', () => {
    expect(valueText(row.read ? row.value : null, mark)).toBe(expectedText);
    expect(valueText(undefined, mark)).toBe('');
  });

  it('readingValue / readingText: the {reading} status vocabulary', () => {
    expect(readingValue(field)).toBe(expectedValue);
    expect(readingText(field, mark)).toBe(expectedText);
    if (row.absent) expect(readingValue(undefined)).toBe(null);
  });

  it('observationValue: the display.state vocabulary', () => {
    expect(observationValue(observation)).toBe(expectedValue);
    expect(valueText(observationValue(observation), mark)).toBe(expectedText);
    // an absent observation and one that carries no value are both nothing
    expect(observationValue(undefined)).toBe(null);
    expect(observationValue({ state: 'unsupported' })).toBe(null);
    expect(observationValue({ state: 'unknown' })).toBe(null);
  });
});

/**
 * MOR-2688 (slice S4b) — vocabulary 3, the legacy NaN/null/Infinity
 * marker, through `finiteValue`. A finite number formats; NaN, ±Infinity,
 * null and undefined are nothing. Goes red under a mutation that accepts
 * any marker value (for example NaN) — the marker value would format.
 */
const FINITE_TABLE = [
  { name: 'a read positive value formats', value: 3.5, expected: mark(3.5) },
  { name: '0 — a read zero formats', value: 0, expected: mark(0) },
  { name: 'a negative value formats', value: -12.5, expected: mark(-12.5) },
  { name: 'NaN is nothing', value: Number.NaN, expected: '' },
  { name: 'Infinity is nothing', value: Number.POSITIVE_INFINITY, expected: '' },
  { name: '-Infinity is nothing', value: Number.NEGATIVE_INFINITY, expected: '' },
  { name: 'null is nothing', value: null, expected: '' },
  { name: 'undefined is nothing', value: undefined, expected: '' },
] as const;

describe.each(FINITE_TABLE)('the shared finite table (MOR-2688 S4b): $name', (row) => {
  it('finiteValue: a finite number or nothing', () => {
    const expectedValue = row.expected === '' ? null : row.value;
    expect(finiteValue(row.value)).toBe(expectedValue);
    expect(valueText(finiteValue(row.value), mark)).toBe(row.expected);
  });
});
