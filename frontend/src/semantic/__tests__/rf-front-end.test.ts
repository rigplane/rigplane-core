/**
 * MOR-1262 decomposition slice 6A (MOR-1292) — `rfFrontEnd` optional fact
 * group (validator half). Extended by slice 6A′ (MOR-1293) with `digiSel`/
 * `ipPlus` and the `'mutually-exclusive-control'` `DisabledReasonCode` for
 * the PREAMP/DIGI-SEL hardware mutex (MOR-479) — the derivation lives in
 * `radio-view-model-adapter.ts`'s `deriveRfFrontEndMutex`, covered by
 * `rf-front-end-adapter.test.ts`; this file only pins the SHAPE.
 *
 * Facts only: preamp, attenuator, RF gain, squelch, DIGI-SEL, IP+ (+ the
 * `preValues`/`attValues` capability-derived choice sets). A SEPARATE group
 * from `dsp` (MOR-1290, slice 5A) — see `radio-view-model.ts`'s
 * `RfFrontEndViewModel` doc comment for the group-shape rationale, and
 * `radio-view-model-adapter.ts`'s `deriveRfFrontEnd` for the live derivation
 * (covered by the companion adapter test file).
 *
 * Mirrors the companion families' (`tx-aux.test.ts`, `dsp.test.ts`)
 * kill-tests for this family:
 *  1. an rfFrontEnd-absent model validates and round-trips byte-identically
 *  2. `"rfFrontEnd": null` / `{}` throw (absent ≠ malformed)
 *  3. a malformed inner field throws with a precise `$.rfFrontEnd....` path
 *  5. an unknown extra key inside rfFrontEnd (or a field) throws
 * (Kill-test 4, the adapter's evidence gate, lives in the adapter test file.)
 */
import { describe, expect, it } from 'vitest';
import {
  validateRadioViewModel, type RfFrontEndField, type RfFrontEndViewModel,
} from '../radio-view-model';
import { topologyFixtures, withRfFrontEnd } from '../fixtures/topologies';

const AVAIL = { structural: true, operational: true } as const;
const base = topologyFixtures['1/single'];

describe('rfFrontEnd (MOR-1262 slice 6A)', () => {
  // ── Kill-test 1: absence is byte-identical, not a new default shape ──────
  it('validates an rfFrontEnd-absent model and never adds the key', () => {
    expect(Object.keys(base)).not.toContain('rfFrontEnd');
    const validated = validateRadioViewModel(base);
    expect(Object.keys(validated)).not.toContain('rfFrontEnd');
    expect(validated.rfFrontEnd).toBeUndefined();
    expect(JSON.parse(JSON.stringify(validated))).toEqual(JSON.parse(JSON.stringify(base)));
  });

  it('validates a fully-populated rfFrontEnd group and returns it unchanged', () => {
    const withR = withRfFrontEnd(base);
    expect(validateRadioViewModel(withR).rfFrontEnd).toEqual(withR.rfFrontEnd);
  });

  // ── Kill-test 2: null / {} are not absent ────────────────────────────────
  it('rejects an explicit rfFrontEnd: null', () => {
    expect(() => validateRadioViewModel({ ...base, rfFrontEnd: null })).toThrow(TypeError);
  });

  it('rejects an explicit rfFrontEnd: {} — present, not absent, must satisfy its own shape', () => {
    expect(() => validateRadioViewModel({ ...base, rfFrontEnd: {} })).toThrow(TypeError);
  });

  // ── Kill-test 3: malformed inner field, precise path ─────────────────────
  it('rejects a non-numeric preamp reading value with a precise error path', () => {
    const withR = withRfFrontEnd(base);
    const malformed = {
      ...withR,
      rfFrontEnd: {
        ...withR.rfFrontEnd,
        preamp: { reading: { status: 'known', value: 'high' }, availability: AVAIL },
      },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(/\$\.rfFrontEnd\.preamp\.reading\.value/);
  });

  it('rejects a non-array preValues', () => {
    const withR = withRfFrontEnd(base);
    const malformed = { ...withR, rfFrontEnd: { ...withR.rfFrontEnd, preValues: 'P1' } };
    expect(() => validateRadioViewModel(malformed)).toThrow(/\$\.rfFrontEnd\.preValues/);
  });

  it('rejects a non-array attValues', () => {
    const withR = withRfFrontEnd(base);
    const malformed = { ...withR, rfFrontEnd: { ...withR.rfFrontEnd, attValues: '6dB' } };
    expect(() => validateRadioViewModel(malformed)).toThrow(/\$\.rfFrontEnd\.attValues/);
  });

  it('rejects a non-boolean field inside an rfFrontEnd field Availability', () => {
    const withR = withRfFrontEnd(base);
    const malformed = {
      ...withR,
      rfFrontEnd: {
        ...withR.rfFrontEnd,
        squelch: { reading: { status: 'unknown' }, availability: { structural: 'yes', operational: true } },
      },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(TypeError);
  });

  it('accepts an unknown reading distinct from a known one, independently per field', () => {
    const withR = withRfFrontEnd(base);
    const partial = {
      ...withR,
      rfFrontEnd: {
        ...withR.rfFrontEnd,
        rfGain: { reading: { status: 'unknown' }, availability: { structural: true, operational: false } },
      },
    };
    const validated = validateRadioViewModel(partial);
    expect(validated.rfFrontEnd?.rfGain).toEqual({
      reading: { status: 'unknown' }, availability: { structural: true, operational: false },
    });
    // The rest of the group is untouched by the one field's degrade.
    expect(validated.rfFrontEnd?.preamp.reading).toEqual({ status: 'known', value: 1 });
  });

  // ── Kill-test 5: no speculative keys (N4) ─────────────────────────────────
  it('rejects an unknown extra key inside rfFrontEnd', () => {
    const withR = withRfFrontEnd(base);
    const malformed = { ...withR, rfFrontEnd: { ...withR.rfFrontEnd, bogus: 1 } };
    expect(() => validateRadioViewModel(malformed)).toThrow(TypeError);
  });

  it('rejects an unknown extra key inside a single rfFrontEnd field', () => {
    const withR = withRfFrontEnd(base);
    const malformed = {
      ...withR,
      rfFrontEnd: {
        ...withR.rfFrontEnd,
        attenuator: { reading: { status: 'known', value: 6 }, availability: AVAIL, extra: true },
      },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(TypeError);
  });

  it('rejects a reading that carries a value key while unknown', () => {
    const withR = withRfFrontEnd(base);
    const malformed = {
      ...withR,
      rfFrontEnd: {
        ...withR.rfFrontEnd,
        squelch: { reading: { status: 'unknown', value: 0 }, availability: AVAIL },
      },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(TypeError);
  });

  it('every field of a fully-populated group round-trips its own value', () => {
    const withR = withRfFrontEnd(base);
    const validated = validateRadioViewModel(withR).rfFrontEnd as RfFrontEndViewModel;
    const knownValue = <T>(f: RfFrontEndField<T>): T | undefined =>
      f.reading.status === 'known' ? f.reading.value : undefined;
    expect(knownValue(validated.preamp)).toBe(1);
    expect(validated.preValues).toEqual([0, 1, 2]);
    expect(knownValue(validated.attenuator)).toBe(6);
    expect(validated.attValues).toEqual([0, 6, 12, 18]);
    expect(knownValue(validated.rfGain)).toBe(1);
    expect(knownValue(validated.squelch)).toBe(0);
    expect(knownValue(validated.digiSel)).toBe(false);
    expect(knownValue(validated.ipPlus)).toBe(false);
  });

  // ── MOR-1293 (slice 6A′): digiSel/ipPlus shape + the mutex reason code ───
  it('rejects a non-boolean digiSel reading value with a precise error path', () => {
    const withR = withRfFrontEnd(base);
    const malformed = {
      ...withR,
      rfFrontEnd: {
        ...withR.rfFrontEnd,
        digiSel: { reading: { status: 'known', value: 'on' }, availability: AVAIL },
      },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(/\$\.rfFrontEnd\.digiSel\.reading\.value/);
  });

  it('rejects a non-boolean ipPlus reading value with a precise error path', () => {
    const withR = withRfFrontEnd(base);
    const malformed = {
      ...withR,
      rfFrontEnd: {
        ...withR.rfFrontEnd,
        ipPlus: { reading: { status: 'known', value: 1 }, availability: AVAIL },
      },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(/\$\.rfFrontEnd\.ipPlus\.reading\.value/);
  });

  it('rejects an rfFrontEnd group missing digiSel/ipPlus (N4 — no partial 6A shape survives)', () => {
    const withR = withRfFrontEnd(base);
    const { digiSel: _digiSel, ...withoutDigiSel } = withR.rfFrontEnd as RfFrontEndViewModel;
    const malformed = { ...withR, rfFrontEnd: withoutDigiSel };
    expect(() => validateRadioViewModel(malformed)).toThrow(TypeError);
  });

  it('accepts the rfFrontEnd.preamp disabledReasons entry the DIGI-SEL mutex derives (MOR-479, MOR-1293)', () => {
    const withR = withRfFrontEnd(base);
    const withMutex = {
      ...withR,
      disabledReasons: [
        ...withR.disabledReasons,
        { field: 'rfFrontEnd.preamp', code: 'mutually-exclusive-control' },
      ],
    };
    const validated = validateRadioViewModel(withMutex);
    expect(validated.disabledReasons).toContainEqual(
      { field: 'rfFrontEnd.preamp', code: 'mutually-exclusive-control' },
    );
  });

  it('rejects a disabledReasons entry with an unrecognized code (mutex code is NOT a wildcard)', () => {
    const withR = withRfFrontEnd(base);
    const malformed = {
      ...withR,
      disabledReasons: [...withR.disabledReasons, { field: 'rfFrontEnd.preamp', code: 'digisel-is-on' }],
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(TypeError);
  });
});
