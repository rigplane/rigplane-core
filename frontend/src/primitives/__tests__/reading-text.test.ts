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
import { readingText } from '../reading-text';
import type { InstrumentReading } from '../control-instruments/control-instrument-behavior';

const unread = <T>(): { reading: InstrumentReading<T> } => ({ reading: { status: 'unknown' } });
const known = <T>(value: T): { reading: InstrumentReading<T> } => ({ reading: { status: 'known', value } });

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
