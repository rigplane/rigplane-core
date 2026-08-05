/**
 * MOR-1262 decomposition slice 4A (MOR-1280) — `modeFilter` optional fact
 * group (validator half).
 *
 * Facts only: current mode, capability-derived mode choice set, current
 * filter selection, capability-derived filter choice set, filter width and
 * its min/max bounds. See `radio-view-model.ts`'s `ModeFilterViewModel`/
 * `validateModeFilter` for the shape and `radio-view-model-adapter.ts`'s
 * `deriveModeFilter` for the live derivation (covered by the companion
 * adapter test file).
 *
 * Mirrors the companion families' (`tx-aux.test.ts`, `rx-audio.test.ts`)
 * kill-tests for this family:
 *  1. a modeFilter-absent model validates and round-trips byte-identically
 *  2. `"modeFilter": null` / `{}` throw (absent ≠ malformed)
 *  3. a malformed inner field throws with a precise `$.modeFilter....` path
 *  5. an unknown extra key inside modeFilter (or a field) throws
 * (Kill-test 4, the adapter's evidence gate, lives in the adapter test file.)
 *
 * Additionally pins the one shape this family has that the others don't:
 * `modeChoices`/`filterChoices` are plain string arrays, not
 * `ModeFilterField`-wrapped — a malformed entry must reject with an
 * indexed path (`$.modeFilter.modeChoices[N]`), and the arrays themselves
 * must reject a non-array value.
 */
import { describe, expect, it } from 'vitest';
import {
  validateRadioViewModel, type ModeFilterField, type ModeFilterViewModel,
} from '../radio-view-model';
import { topologyFixtures, withModeFilter } from '../fixtures/topologies';

const AVAIL = { structural: true, operational: true } as const;
const base = topologyFixtures['1/single'];

describe('modeFilter (MOR-1262 slice 4A)', () => {
  // ── Kill-test 1: absence is byte-identical, not a new default shape ──────
  it('validates a modeFilter-absent model and never adds the key', () => {
    expect(Object.keys(base)).not.toContain('modeFilter');
    const validated = validateRadioViewModel(base);
    expect(Object.keys(validated)).not.toContain('modeFilter');
    expect(validated.modeFilter).toBeUndefined();
    expect(JSON.parse(JSON.stringify(validated))).toEqual(JSON.parse(JSON.stringify(base)));
  });

  it('validates a fully-populated modeFilter group and returns it unchanged', () => {
    const withMf = withModeFilter(base);
    expect(validateRadioViewModel(withMf).modeFilter).toEqual(withMf.modeFilter);
  });

  // ── Kill-test 2: null / {} are not absent ────────────────────────────────
  it('rejects an explicit modeFilter: null', () => {
    expect(() => validateRadioViewModel({ ...base, modeFilter: null })).toThrow(TypeError);
  });

  it('rejects an explicit modeFilter: {} — present, not absent, must satisfy its own shape', () => {
    expect(() => validateRadioViewModel({ ...base, modeFilter: {} })).toThrow(TypeError);
  });

  // ── Kill-test 3: malformed inner field, precise path ─────────────────────
  it('rejects a non-string currentMode reading value with a precise error path', () => {
    const withMf = withModeFilter(base);
    const malformed = {
      ...withMf,
      modeFilter: {
        ...withMf.modeFilter,
        currentMode: { reading: { status: 'known', value: 7 }, availability: AVAIL },
      },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(/\$\.modeFilter\.currentMode\.reading\.value/);
  });

  it('rejects a non-numeric filterWidth reading value with a precise error path', () => {
    const withMf = withModeFilter(base);
    const malformed = {
      ...withMf,
      modeFilter: {
        ...withMf.modeFilter,
        filterWidth: { reading: { status: 'known', value: 'wide' }, availability: AVAIL },
      },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(/\$\.modeFilter\.filterWidth\.reading\.value/);
  });

  it('rejects a non-array modeChoices value', () => {
    const withMf = withModeFilter(base);
    const malformed = { ...withMf, modeFilter: { ...withMf.modeFilter, modeChoices: 'USB' } };
    expect(() => validateRadioViewModel(malformed)).toThrow(/\$\.modeFilter\.modeChoices/);
  });

  it('rejects a non-string entry inside filterChoices with an indexed path', () => {
    const withMf = withModeFilter(base);
    const malformed = {
      ...withMf, modeFilter: { ...withMf.modeFilter, filterChoices: ['FIL1', 2, 'FIL3'] },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(/\$\.modeFilter\.filterChoices\[1\]/);
  });

  it('rejects a non-boolean field inside a modeFilter field Availability', () => {
    const withMf = withModeFilter(base);
    const malformed = {
      ...withMf,
      modeFilter: {
        ...withMf.modeFilter,
        currentFilter: { reading: { status: 'unknown' }, availability: { structural: 'yes', operational: true } },
      },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(TypeError);
  });

  it('accepts an unknown reading distinct from a known one, independently per field', () => {
    const withMf = withModeFilter(base);
    const partial = {
      ...withMf,
      modeFilter: {
        ...withMf.modeFilter,
        filterWidthMax: { reading: { status: 'unknown' }, availability: { structural: true, operational: false } },
      },
    };
    const validated = validateRadioViewModel(partial);
    expect(validated.modeFilter?.filterWidthMax).toEqual({
      reading: { status: 'unknown' }, availability: { structural: true, operational: false },
    });
    // The rest of the group is untouched by the one field's degrade.
    expect(validated.modeFilter?.filterWidth.reading).toEqual({ status: 'known', value: 2400 });
  });

  // ── Kill-test 5: no speculative keys (N4) ─────────────────────────────────
  it('rejects an unknown extra key inside modeFilter', () => {
    const withMf = withModeFilter(base);
    const malformed = { ...withMf, modeFilter: { ...withMf.modeFilter, bogus: 1 } };
    expect(() => validateRadioViewModel(malformed)).toThrow(TypeError);
  });

  it('rejects an unknown extra key inside a single modeFilter field', () => {
    const withMf = withModeFilter(base);
    const malformed = {
      ...withMf,
      modeFilter: {
        ...withMf.modeFilter,
        currentFilter: { reading: { status: 'known', value: 1 }, availability: AVAIL, extra: true },
      },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(TypeError);
  });

  it('rejects a reading that carries a value key while unknown', () => {
    const withMf = withModeFilter(base);
    const malformed = {
      ...withMf,
      modeFilter: {
        ...withMf.modeFilter,
        filterWidthMin: { reading: { status: 'unknown', value: 50 }, availability: AVAIL },
      },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(TypeError);
  });

  it('every field of a fully-populated group round-trips its own value', () => {
    const withMf = withModeFilter(base);
    const validated = validateRadioViewModel(withMf).modeFilter as ModeFilterViewModel;
    const knownValue = <T>(f: ModeFilterField<T>): T | undefined =>
      f.reading.status === 'known' ? f.reading.value : undefined;
    expect(knownValue(validated.currentMode)).toBe('USB');
    expect(validated.modeChoices).toEqual(['USB', 'LSB', 'CW', 'RTTY', 'FM']);
    expect(knownValue(validated.currentFilter)).toBe(1);
    expect(validated.filterChoices).toEqual(['FIL1', 'FIL2', 'FIL3']);
    expect(knownValue(validated.filterWidth)).toBe(2400);
    expect(knownValue(validated.filterWidthMin)).toBe(50);
    expect(knownValue(validated.filterWidthMax)).toBe(3600);
  });
});
