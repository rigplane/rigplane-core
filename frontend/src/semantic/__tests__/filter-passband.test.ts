/**
 * MOR-1262 decomposition slice 4A′ (MOR-1284) — `filterPassband` optional
 * fact group (validator half).
 *
 * Facts only: filter shape, IF-shift, PBT inner/outer, DATA-mode. A SEPARATE
 * group from `modeFilter` (MOR-1280, slice 4A) — see `radio-view-model.ts`'s
 * `FilterPassbandViewModel` doc comment for the group-shape rationale, and
 * `radio-view-model-adapter.ts`'s `deriveFilterPassband` for the live
 * derivation (covered by the companion adapter test file).
 *
 * Mirrors the companion families' (`tx-aux.test.ts`, `mode-filter.test.ts`)
 * kill-tests for this family:
 *  1. a filterPassband-absent model validates and round-trips byte-identically
 *  2. `"filterPassband": null` / `{}` throw (absent ≠ malformed)
 *  3. a malformed inner field throws with a precise `$.filterPassband....` path
 *  5. an unknown extra key inside filterPassband (or a field) throws
 * (Kill-test 4, the adapter's evidence gate, lives in the adapter test file.)
 */
import { describe, expect, it } from 'vitest';
import {
  validateRadioViewModel, type FilterPassbandField, type FilterPassbandViewModel,
} from '../radio-view-model';
import { topologyFixtures, withFilterPassband } from '../fixtures/topologies';

const AVAIL = { structural: true, operational: true } as const;
const base = topologyFixtures['1/single'];

describe('filterPassband (MOR-1262 slice 4A′)', () => {
  // ── Kill-test 1: absence is byte-identical, not a new default shape ──────
  it('validates a filterPassband-absent model and never adds the key', () => {
    expect(Object.keys(base)).not.toContain('filterPassband');
    const validated = validateRadioViewModel(base);
    expect(Object.keys(validated)).not.toContain('filterPassband');
    expect(validated.filterPassband).toBeUndefined();
    expect(JSON.parse(JSON.stringify(validated))).toEqual(JSON.parse(JSON.stringify(base)));
  });

  it('validates a fully-populated filterPassband group and returns it unchanged', () => {
    const withFp = withFilterPassband(base);
    expect(validateRadioViewModel(withFp).filterPassband).toEqual(withFp.filterPassband);
  });

  // ── Kill-test 2: null / {} are not absent ────────────────────────────────
  it('rejects an explicit filterPassband: null', () => {
    expect(() => validateRadioViewModel({ ...base, filterPassband: null })).toThrow(TypeError);
  });

  it('rejects an explicit filterPassband: {} — present, not absent, must satisfy its own shape', () => {
    expect(() => validateRadioViewModel({ ...base, filterPassband: {} })).toThrow(TypeError);
  });

  // ── Kill-test 3: malformed inner field, precise path ─────────────────────
  it('rejects a non-numeric ifShift reading value with a precise error path', () => {
    const withFp = withFilterPassband(base);
    const malformed = {
      ...withFp,
      filterPassband: {
        ...withFp.filterPassband,
        ifShift: { reading: { status: 'known', value: 'left' }, availability: AVAIL },
      },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(/\$\.filterPassband\.ifShift\.reading\.value/);
  });

  it('rejects a non-numeric pbtInner reading value with a precise error path', () => {
    const withFp = withFilterPassband(base);
    const malformed = {
      ...withFp,
      filterPassband: {
        ...withFp.filterPassband,
        pbtInner: { reading: { status: 'known', value: true }, availability: AVAIL },
      },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(/\$\.filterPassband\.pbtInner\.reading\.value/);
  });

  it('rejects a non-boolean field inside a filterPassband field Availability', () => {
    const withFp = withFilterPassband(base);
    const malformed = {
      ...withFp,
      filterPassband: {
        ...withFp.filterPassband,
        dataMode: { reading: { status: 'unknown' }, availability: { structural: 'yes', operational: true } },
      },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(TypeError);
  });

  it('accepts an unknown reading distinct from a known one, independently per field', () => {
    const withFp = withFilterPassband(base);
    const partial = {
      ...withFp,
      filterPassband: {
        ...withFp.filterPassband,
        pbtOuter: { reading: { status: 'unknown' }, availability: { structural: true, operational: false } },
      },
    };
    const validated = validateRadioViewModel(partial);
    expect(validated.filterPassband?.pbtOuter).toEqual({
      reading: { status: 'unknown' }, availability: { structural: true, operational: false },
    });
    // The rest of the group is untouched by the one field's degrade.
    expect(validated.filterPassband?.pbtInner.reading).toEqual({ status: 'known', value: 0 });
  });

  // ── Kill-test 5: no speculative keys (N4) ─────────────────────────────────
  it('rejects an unknown extra key inside filterPassband', () => {
    const withFp = withFilterPassband(base);
    const malformed = { ...withFp, filterPassband: { ...withFp.filterPassband, bogus: 1 } };
    expect(() => validateRadioViewModel(malformed)).toThrow(TypeError);
  });

  it('rejects an unknown extra key inside a single filterPassband field', () => {
    const withFp = withFilterPassband(base);
    const malformed = {
      ...withFp,
      filterPassband: {
        ...withFp.filterPassband,
        filterShape: { reading: { status: 'known', value: 1 }, availability: AVAIL, extra: true },
      },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(TypeError);
  });

  it('rejects a reading that carries a value key while unknown', () => {
    const withFp = withFilterPassband(base);
    const malformed = {
      ...withFp,
      filterPassband: {
        ...withFp.filterPassband,
        dataMode: { reading: { status: 'unknown', value: 0 }, availability: AVAIL },
      },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(TypeError);
  });

  it('every field of a fully-populated group round-trips its own value', () => {
    const withFp = withFilterPassband(base);
    const validated = validateRadioViewModel(withFp).filterPassband as FilterPassbandViewModel;
    const knownValue = <T>(f: FilterPassbandField<T>): T | undefined =>
      f.reading.status === 'known' ? f.reading.value : undefined;
    expect(knownValue(validated.filterShape)).toBe(1);
    expect(knownValue(validated.ifShift)).toBe(0);
    expect(knownValue(validated.pbtInner)).toBe(0);
    expect(knownValue(validated.pbtOuter)).toBe(0);
    expect(knownValue(validated.dataMode)).toBe(0);
  });
});
