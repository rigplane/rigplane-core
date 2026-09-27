/**
 * MOR-2704 (G5a) — the one `acceptedNumber` / `acceptedBoolean` for the
 * scope controls.
 *
 * Table over the three former copies' inputs (all read-but-rejected must be
 * `null`, never a placeholder): an undefined field, a structurally or
 * operationally unavailable field, an unread field, a known value outside
 * the declared domain, and a value of the wrong runtime shape. Dropping
 * `operational` from the exported `usable` gate turns the
 * `operational: false` rows red (verified by mutation on the mini).
 */
import { describe, expect, it } from 'vitest';
import { acceptedBoolean, acceptedNumber } from '../accepted-scope-values';
import type { InstrumentField } from '../../primitives/control-instruments/control-instrument-behavior';

function numField(
  structural: boolean,
  operational: boolean,
  reading: { status: 'known'; value: unknown } | { status: 'unknown' },
): InstrumentField<number> | undefined {
  return { availability: { structural, operational }, reading } as InstrumentField<number>;
}

function boolField(
  structural: boolean,
  operational: boolean,
  reading: { status: 'known'; value: unknown } | { status: 'unknown' },
): InstrumentField<boolean> | undefined {
  return { availability: { structural, operational }, reading } as InstrumentField<boolean>;
}

describe('acceptedNumber (MOR-2704 G5a)', () => {
  const cases: Array<[string, InstrumentField<number> | undefined, number, number, number | null]> = [
    ['undefined field', undefined, 0, 7, null],
    ['structural false', numField(false, true, { status: 'known', value: 3 }), 0, 7, null],
    ['operational false', numField(true, false, { status: 'known', value: 3 }), 0, 7, null],
    ['unread (unknown)', numField(true, true, { status: 'unknown' }), 0, 7, null],
    ['known, domain minimum', numField(true, true, { status: 'known', value: 0 }), 0, 7, 0],
    ['known, domain maximum', numField(true, true, { status: 'known', value: 7 }), 0, 7, 7],
    ['known below min', numField(true, true, { status: 'known', value: -1 }), 0, 7, null],
    ['known above max', numField(true, true, { status: 'known', value: 8 }), 0, 7, null],
    ['known non-integer', numField(true, true, { status: 'known', value: 2.5 }), 0, 7, null],
    ['known NaN', numField(true, true, { status: 'known', value: NaN }), 0, 7, null],
  ];
  for (const [name, field, min, max, expected] of cases) {
    it(`${name} → ${expected === null ? 'null' : expected}`, () => {
      expect(acceptedNumber(field, min, max)).toBe(expected);
    });
  }
});

describe('acceptedBoolean (MOR-2704 G5a)', () => {
  const cases: Array<[string, InstrumentField<boolean> | undefined, boolean | null]> = [
    ['undefined field', undefined, null],
    ['structural false', boolField(false, true, { status: 'known', value: true }), null],
    ['operational false', boolField(true, false, { status: 'known', value: true }), null],
    ['unread (unknown)', boolField(true, true, { status: 'unknown' }), null],
    ['known true', boolField(true, true, { status: 'known', value: true }), true],
    ['known false', boolField(true, true, { status: 'known', value: false }), false],
    ['known non-boolean (wire 1)', boolField(true, true, { status: 'known', value: 1 }), null],
  ];
  for (const [name, field, expected] of cases) {
    it(`${name} → ${expected === null ? 'null' : expected}`, () => {
      expect(acceptedBoolean(field)).toBe(expected);
    });
  }
});
