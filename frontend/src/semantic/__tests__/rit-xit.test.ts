/**
 * MOR-1262 decomposition slice 8A (MOR-1295) — `ritXit` optional fact group
 * (validator half).
 *
 * Facts only: RIT/XIT enables and their shared frequency offset. A separate
 * group from `txAux` — RIT/XIT is not TX-adjacent (it offsets RX/TX without
 * keying), and family enumeration is explicit and CLOSED. See
 * `radio-view-model.ts`'s `RitXitViewModel` doc comment for the group-shape
 * rationale, and `radio-view-model-adapter.ts`'s `deriveRitXit` for the live
 * derivation (covered by the companion `rit-xit-adapter.test.ts`).
 *
 * Mirrors the companion families' (`rf-front-end.test.ts`, `dsp.test.ts`)
 * kill-tests for this family:
 *  1. a ritXit-absent model validates and round-trips byte-identically
 *  2. `"ritXit": null` / `{}` throw (absent ≠ malformed)
 *  3. a malformed inner field throws with a precise `$.ritXit....` path
 *  5. an unknown extra key inside ritXit (or a field) throws
 * (Kill-test 4, the adapter's evidence gate, lives in the adapter test file.)
 */
import { describe, expect, it } from 'vitest';
import { validateRadioViewModel, type RitXitField, type RitXitViewModel } from '../radio-view-model';
import { topologyFixtures, withRitXit } from '../fixtures/topologies';

const AVAIL = { structural: true, operational: true } as const;
const base = topologyFixtures['1/single'];

describe('ritXit (MOR-1262 slice 8A)', () => {
  it('validates a ritXit-absent model and never adds the key', () => {
    expect(Object.keys(base)).not.toContain('ritXit');
    const validated = validateRadioViewModel(base);
    expect(Object.keys(validated)).not.toContain('ritXit');
    expect(validated.ritXit).toBeUndefined();
    expect(JSON.parse(JSON.stringify(validated))).toEqual(JSON.parse(JSON.stringify(base)));
  });

  it('validates a fully-populated ritXit group and returns it unchanged', () => {
    const withR = withRitXit(base);
    expect(validateRadioViewModel(withR).ritXit).toEqual(withR.ritXit);
  });

  it('rejects an explicit ritXit: null', () => {
    expect(() => validateRadioViewModel({ ...base, ritXit: null })).toThrow(TypeError);
  });

  it('rejects an explicit ritXit: {} — present, not absent, must satisfy its own shape', () => {
    expect(() => validateRadioViewModel({ ...base, ritXit: {} })).toThrow(TypeError);
  });

  it('rejects a non-numeric ritOffset reading value with a precise error path', () => {
    const withR = withRitXit(base);
    const malformed = {
      ...withR,
      ritXit: { ...withR.ritXit, ritOffset: { reading: { status: 'known', value: '250' }, availability: AVAIL } },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(/\$\.ritXit\.ritOffset\.reading\.value/);
  });

  it('rejects a non-boolean xitActive reading value with a precise error path', () => {
    const withR = withRitXit(base);
    const malformed = {
      ...withR,
      ritXit: { ...withR.ritXit, xitActive: { reading: { status: 'known', value: 1 }, availability: AVAIL } },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(/\$\.ritXit\.xitActive\.reading\.value/);
  });

  it('accepts an unknown reading distinct from a known one, independently per field', () => {
    const withR = withRitXit(base);
    const partial = {
      ...withR,
      ritXit: { ...withR.ritXit, xitActive: { reading: { status: 'unknown' }, availability: { structural: false, operational: false } } },
    };
    const validated = validateRadioViewModel(partial);
    expect(validated.ritXit?.xitActive).toEqual({
      reading: { status: 'unknown' }, availability: { structural: false, operational: false },
    });
    expect(validated.ritXit?.ritActive.reading).toEqual({ status: 'known', value: true });
  });

  it('accepts ritOffset and xitOffset carrying independently-different values', () => {
    const withR = withRitXit(base);
    const model = {
      ...withR,
      ritXit: { ...withR.ritXit, xitOffset: { reading: { status: 'known', value: -300 }, availability: AVAIL } },
    };
    const validated = validateRadioViewModel(model);
    expect(validated.ritXit?.ritOffset.reading).toEqual({ status: 'known', value: 250 });
    expect(validated.ritXit?.xitOffset.reading).toEqual({ status: 'known', value: -300 });
  });

  it('rejects an unknown extra key inside ritXit', () => {
    const withR = withRitXit(base);
    expect(() => validateRadioViewModel({ ...base, ritXit: { ...withR.ritXit, bogus: 1 } })).toThrow(TypeError);
  });

  it('rejects an unknown extra key inside a single ritXit field', () => {
    const withR = withRitXit(base);
    const malformed = {
      ...withR,
      ritXit: { ...withR.ritXit, ritActive: { reading: { status: 'known', value: true }, availability: AVAIL, extra: true } },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(TypeError);
  });

  it('rejects a reading that carries a value key while unknown', () => {
    const withR = withRitXit(base);
    const malformed = {
      ...withR,
      ritXit: { ...withR.ritXit, ritActive: { reading: { status: 'unknown', value: true }, availability: AVAIL } },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(TypeError);
  });

  it('rejects a ritXit group missing a key (no partial 8A shape survives)', () => {
    const withR = withRitXit(base);
    const { xitOffset: _xitOffset, ...withoutXitOffset } = withR.ritXit as RitXitViewModel;
    expect(() => validateRadioViewModel({ ...withR, ritXit: withoutXitOffset })).toThrow(TypeError);
  });

  it('every field of a fully-populated group round-trips its own value', () => {
    const validated = validateRadioViewModel(withRitXit(base)).ritXit as RitXitViewModel;
    const knownValue = <T>(f: RitXitField<T>): T | undefined =>
      f.reading.status === 'known' ? f.reading.value : undefined;
    expect(knownValue(validated.ritActive)).toBe(true);
    expect(knownValue(validated.ritOffset)).toBe(250);
    expect(knownValue(validated.xitActive)).toBe(false);
    expect(knownValue(validated.xitOffset)).toBe(250);
  });
});
