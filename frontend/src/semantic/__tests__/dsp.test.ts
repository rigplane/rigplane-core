/**
 * MOR-1262 decomposition slice 5A (MOR-1290) — `dsp` optional fact group
 * (validator half).
 *
 * Facts only: NR (active + level), NB (active + level + depth + width),
 * notch (mode + freq + manual width), AGC (mode + choice set + time
 * constant). A SEPARATE group from `filterPassband` (MOR-1284, slice 4A′) —
 * see `radio-view-model.ts`'s `DspViewModel` doc comment for the group-shape
 * rationale, and `radio-view-model-adapter.ts`'s `deriveDsp` for the live
 * derivation (covered by the companion adapter test file).
 *
 * Mirrors the companion families' (`tx-aux.test.ts`, `filter-passband.test.ts`)
 * kill-tests for this family:
 *  1. a dsp-absent model validates and round-trips byte-identically
 *  2. `"dsp": null` / `{}` throw (absent ≠ malformed)
 *  3. a malformed inner field throws with a precise `$.dsp....` path
 *  5. an unknown extra key inside dsp (or a field) throws
 * (Kill-test 4, the adapter's evidence gate, lives in the adapter test file.)
 */
import { describe, expect, it } from 'vitest';
import {
  validateRadioViewModel, type DspField, type DspViewModel,
} from '../radio-view-model';
import { topologyFixtures, withDsp } from '../fixtures/topologies';

const AVAIL = { structural: true, operational: true } as const;
const base = topologyFixtures['1/single'];

describe('dsp (MOR-1262 slice 5A)', () => {
  // ── Kill-test 1: absence is byte-identical, not a new default shape ──────
  it('validates a dsp-absent model and never adds the key', () => {
    expect(Object.keys(base)).not.toContain('dsp');
    const validated = validateRadioViewModel(base);
    expect(Object.keys(validated)).not.toContain('dsp');
    expect(validated.dsp).toBeUndefined();
    expect(JSON.parse(JSON.stringify(validated))).toEqual(JSON.parse(JSON.stringify(base)));
  });

  it('validates a fully-populated dsp group and returns it unchanged', () => {
    const withD = withDsp(base);
    expect(validateRadioViewModel(withD).dsp).toEqual(withD.dsp);
  });

  // ── Kill-test 2: null / {} are not absent ────────────────────────────────
  it('rejects an explicit dsp: null', () => {
    expect(() => validateRadioViewModel({ ...base, dsp: null })).toThrow(TypeError);
  });

  it('rejects an explicit dsp: {} — present, not absent, must satisfy its own shape', () => {
    expect(() => validateRadioViewModel({ ...base, dsp: {} })).toThrow(TypeError);
  });

  // ── Kill-test 3: malformed inner field, precise path ─────────────────────
  it('rejects a non-numeric nrLevel reading value with a precise error path', () => {
    const withD = withDsp(base);
    const malformed = {
      ...withD,
      dsp: {
        ...withD.dsp,
        nrLevel: { reading: { status: 'known', value: 'high' }, availability: AVAIL },
      },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(/\$\.dsp\.nrLevel\.reading\.value/);
  });

  it('rejects a non-boolean nbActive reading value with a precise error path', () => {
    const withD = withDsp(base);
    const malformed = {
      ...withD,
      dsp: {
        ...withD.dsp,
        nbActive: { reading: { status: 'known', value: 1 }, availability: AVAIL },
      },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(/\$\.dsp\.nbActive\.reading\.value/);
  });

  it('rejects a notchMode reading value outside the off/auto/manual union', () => {
    const withD = withDsp(base);
    const malformed = {
      ...withD,
      dsp: {
        ...withD.dsp,
        notchMode: { reading: { status: 'known', value: 'wide' }, availability: AVAIL },
      },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(/\$\.dsp\.notchMode\.reading\.value/);
  });

  it('rejects a non-array agcModes', () => {
    const withD = withDsp(base);
    const malformed = { ...withD, dsp: { ...withD.dsp, agcModes: 'FAST' } };
    expect(() => validateRadioViewModel(malformed)).toThrow(/\$\.dsp\.agcModes/);
  });

  it('rejects a non-boolean field inside a dsp field Availability', () => {
    const withD = withDsp(base);
    const malformed = {
      ...withD,
      dsp: {
        ...withD.dsp,
        agcMode: { reading: { status: 'unknown' }, availability: { structural: 'yes', operational: true } },
      },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(TypeError);
  });

  it('accepts an unknown reading distinct from a known one, independently per field', () => {
    const withD = withDsp(base);
    const partial = {
      ...withD,
      dsp: {
        ...withD.dsp,
        nbDepth: { reading: { status: 'unknown' }, availability: { structural: true, operational: false } },
      },
    };
    const validated = validateRadioViewModel(partial);
    expect(validated.dsp?.nbDepth).toEqual({
      reading: { status: 'unknown' }, availability: { structural: true, operational: false },
    });
    // The rest of the group is untouched by the one field's degrade.
    expect(validated.dsp?.nrLevel.reading).toEqual({ status: 'known', value: 8 });
  });

  // ── Kill-test 5: no speculative keys (N4) ─────────────────────────────────
  it('rejects an unknown extra key inside dsp', () => {
    const withD = withDsp(base);
    const malformed = { ...withD, dsp: { ...withD.dsp, bogus: 1 } };
    expect(() => validateRadioViewModel(malformed)).toThrow(TypeError);
  });

  it('rejects an unknown extra key inside a single dsp field', () => {
    const withD = withDsp(base);
    const malformed = {
      ...withD,
      dsp: {
        ...withD.dsp,
        agcTimeConstant: { reading: { status: 'known', value: 1 }, availability: AVAIL, extra: true },
      },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(TypeError);
  });

  it('rejects a reading that carries a value key while unknown', () => {
    const withD = withDsp(base);
    const malformed = {
      ...withD,
      dsp: {
        ...withD.dsp,
        nbWidth: { reading: { status: 'unknown', value: 0 }, availability: AVAIL },
      },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(TypeError);
  });

  it('every field of a fully-populated group round-trips its own value', () => {
    const withD = withDsp(base);
    const validated = validateRadioViewModel(withD).dsp as DspViewModel;
    const knownValue = <T>(f: DspField<T>): T | undefined =>
      f.reading.status === 'known' ? f.reading.value : undefined;
    expect(knownValue(validated.nrActive)).toBe(true);
    expect(knownValue(validated.nrLevel)).toBe(8);
    expect(knownValue(validated.nbActive)).toBe(false);
    expect(knownValue(validated.nbLevel)).toBe(64);
    expect(knownValue(validated.nbDepth)).toBe(5);
    expect(knownValue(validated.nbWidth)).toBe(2);
    expect(knownValue(validated.notchMode)).toBe('off');
    expect(knownValue(validated.notchFreq)).toBe(0);
    expect(knownValue(validated.manualNotchWidth)).toBe(10);
    expect(knownValue(validated.agcMode)).toBe(2);
    expect(validated.agcModes).toEqual([1, 2, 3]);
    expect(knownValue(validated.agcTimeConstant)).toBe(0);
  });
});
