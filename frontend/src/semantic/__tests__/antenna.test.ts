/**
 * MOR-1262 decomposition slice 8A (MOR-1295) — `antenna` optional fact group
 * (validator half).
 *
 * Facts only: selected TX antenna port, the port-dependent RX-antenna
 * override, and the declared port count. ATU/tuner state is DELIBERATELY
 * absent (family 1's `txAux.atu`, MOR-1244) — see `radio-view-model.ts`'s
 * `AntennaViewModel` doc comment. `radio-view-model-adapter.ts`'s
 * `deriveAntenna` covers the live derivation (companion
 * `antenna-adapter.test.ts`).
 *
 * Mirrors the companion families' kill-tests:
 *  1. an antenna-absent model validates and round-trips byte-identically
 *  2. `"antenna": null` / `{}` throw (absent ≠ malformed)
 *  3. a malformed inner field throws with a precise `$.antenna....` path
 *  5. an unknown extra key inside antenna (or a field) throws
 * (Kill-test 4, the adapter's evidence gate, lives in the adapter test file.)
 */
import { describe, expect, it } from 'vitest';
import { validateRadioViewModel, type AntennaField, type AntennaViewModel } from '../radio-view-model';
import { topologyFixtures, withAntenna } from '../fixtures/topologies';

const AVAIL = { structural: true, operational: true } as const;
const base = topologyFixtures['1/single'];

describe('antenna (MOR-1262 slice 8A)', () => {
  it('validates an antenna-absent model and never adds the key', () => {
    expect(Object.keys(base)).not.toContain('antenna');
    const validated = validateRadioViewModel(base);
    expect(Object.keys(validated)).not.toContain('antenna');
    expect(validated.antenna).toBeUndefined();
    expect(JSON.parse(JSON.stringify(validated))).toEqual(JSON.parse(JSON.stringify(base)));
  });

  it('validates a fully-populated antenna group and returns it unchanged', () => {
    const withA = withAntenna(base);
    expect(validateRadioViewModel(withA).antenna).toEqual(withA.antenna);
  });

  it('rejects an explicit antenna: null', () => {
    expect(() => validateRadioViewModel({ ...base, antenna: null })).toThrow(TypeError);
  });

  it('rejects an explicit antenna: {} — present, not absent, must satisfy its own shape', () => {
    expect(() => validateRadioViewModel({ ...base, antenna: {} })).toThrow(TypeError);
  });

  it('rejects a non-numeric txAntenna reading value with a precise error path', () => {
    const withA = withAntenna(base);
    const malformed = {
      ...withA,
      antenna: { ...withA.antenna, txAntenna: { reading: { status: 'known', value: 'ANT1' }, availability: AVAIL } },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(/\$\.antenna\.txAntenna\.reading\.value/);
  });

  it('rejects a non-boolean rxAnt reading value with a precise error path', () => {
    const withA = withAntenna(base);
    const malformed = {
      ...withA,
      antenna: { ...withA.antenna, rxAnt: { reading: { status: 'known', value: 1 }, availability: AVAIL } },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(/\$\.antenna\.rxAnt\.reading\.value/);
  });

  it('rejects a non-numeric antennaCount', () => {
    const withA = withAntenna(base);
    expect(() => validateRadioViewModel({ ...withA, antenna: { ...withA.antenna, antennaCount: '2' } }))
      .toThrow(/\$\.antenna\.antennaCount/);
  });

  it('accepts an unknown rxAnt reading independent of a known txAntenna reading', () => {
    const withA = withAntenna(base);
    const partial = {
      ...withA,
      antenna: { ...withA.antenna, rxAnt: { reading: { status: 'unknown' }, availability: { structural: false, operational: false } } },
    };
    const validated = validateRadioViewModel(partial);
    expect(validated.antenna?.rxAnt).toEqual({
      reading: { status: 'unknown' }, availability: { structural: false, operational: false },
    });
    expect(validated.antenna?.txAntenna.reading).toEqual({ status: 'known', value: 1 });
  });

  it('rejects an unknown extra key inside antenna', () => {
    const withA = withAntenna(base);
    expect(() => validateRadioViewModel({ ...base, antenna: { ...withA.antenna, bogus: 1 } })).toThrow(TypeError);
  });

  it('rejects an unknown extra key inside a single antenna field', () => {
    const withA = withAntenna(base);
    const malformed = {
      ...withA,
      antenna: { ...withA.antenna, txAntenna: { reading: { status: 'known', value: 1 }, availability: AVAIL, extra: true } },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(TypeError);
  });

  it('rejects a reading that carries a value key while unknown', () => {
    const withA = withAntenna(base);
    const malformed = {
      ...withA,
      antenna: { ...withA.antenna, rxAnt: { reading: { status: 'unknown', value: false }, availability: AVAIL } },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(TypeError);
  });

  it('rejects an antenna group missing a key (no partial 8A shape survives)', () => {
    const withA = withAntenna(base);
    const { antennaCount: _antennaCount, ...withoutCount } = withA.antenna as AntennaViewModel;
    expect(() => validateRadioViewModel({ ...withA, antenna: withoutCount })).toThrow(TypeError);
  });

  it('every field of a fully-populated group round-trips its own value', () => {
    const validated = validateRadioViewModel(withAntenna(base)).antenna as AntennaViewModel;
    const knownValue = <T>(f: AntennaField<T>): T | undefined =>
      f.reading.status === 'known' ? f.reading.value : undefined;
    expect(knownValue(validated.txAntenna)).toBe(1);
    expect(knownValue(validated.rxAnt)).toBe(false);
    expect(validated.antennaCount).toBe(2);
  });
});
