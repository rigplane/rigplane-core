/**
 * MOR-1244 — `txAux` optional fact group (validator half).
 *
 * Slice 1A of the MOR-1262 decomposition: facts only for ATU/TUNE, VOX
 * (+gain/anti-vox/delay), COMP (+level), MON (+level), RF power, mic gain,
 * drive gain. See `radio-view-model.ts`'s `TxAuxViewModel`/`validateTxAux`
 * for the shape and `radio-view-model-adapter.ts`'s `deriveTxAux` for the
 * live derivation (covered by the companion adapter test file).
 *
 * This file pins the ticket's five kill-tests:
 *  1. a txAux-absent model validates and round-trips byte-identically
 *  2. `"txAux": null` throws (the S0/MOR-1264 absent-vs-malformed pin
 *     extends to a real group, not just the synthetic one)
 *  3. a malformed inner field throws with a precise `$.txAux....` path
 *  5. an unknown extra key inside txAux throws
 * (Kill-test 4, the adapter's evidence gate, lives in the adapter test file
 * next to the code it mutates.)
 */
import { describe, expect, it } from 'vitest';
import { validateRadioViewModel, type TxAuxField, type TxAuxViewModel } from '../radio-view-model';
import { topologyFixtures, withTxAux } from '../fixtures/topologies';

const AVAIL = { structural: true, operational: true } as const;
const base = topologyFixtures['1/single'];

describe('txAux (MOR-1244)', () => {
  // ── Kill-test 1: absence is byte-identical, not a new default shape ──────
  it('validates a txAux-absent model and never adds the key — structurally unavailable, not all-unknown', () => {
    expect(Object.keys(base)).not.toContain('txAux');
    const validated = validateRadioViewModel(base);
    expect(Object.keys(validated)).not.toContain('txAux');
    expect(validated.txAux).toBeUndefined();
    expect(JSON.parse(JSON.stringify(validated))).toEqual(JSON.parse(JSON.stringify(base)));
  });

  it('validates a fully-populated txAux group and returns it unchanged', () => {
    const withAux = withTxAux(base);
    const validated = validateRadioViewModel(withAux);
    expect(validated.txAux).toEqual(withAux.txAux);
  });

  // ── Kill-test 2: null is not absent (JSON has no undefined) ──────────────
  it('rejects an explicit txAux: null', () => {
    expect(() => validateRadioViewModel({ ...base, txAux: null })).toThrow(TypeError);
  });

  it('rejects an explicit txAux: {} — present, not absent, must satisfy its own shape', () => {
    expect(() => validateRadioViewModel({ ...base, txAux: {} })).toThrow(TypeError);
  });

  // ── Kill-test 3: malformed inner field, precise path ──────────────────────
  it('rejects a non-numeric voxGain reading value with a precise error path', () => {
    const withAux = withTxAux(base);
    const malformed = {
      ...withAux,
      txAux: {
        ...withAux.txAux,
        voxGain: { reading: { status: 'known', value: 'loud' }, availability: AVAIL },
      },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(/\$\.txAux\.voxGain\.reading\.value/);
  });

  it('rejects a non-boolean vox reading value with a precise error path', () => {
    const withAux = withTxAux(base);
    const malformed = {
      ...withAux,
      txAux: { ...withAux.txAux, vox: { reading: { status: 'known', value: 'yes' }, availability: AVAIL } },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(/\$\.txAux\.vox\.reading\.value/);
  });

  it('rejects an atu reading value outside off/on/tuning', () => {
    const withAux = withTxAux(base);
    const malformed = {
      ...withAux,
      txAux: { ...withAux.txAux, atu: { reading: { status: 'known', value: 'tuned' }, availability: AVAIL } },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(/\$\.txAux\.atu\.reading\.value/);
  });

  it('accepts each atu status (off, on, tuning)', () => {
    const withAux = withTxAux(base);
    for (const value of ['off', 'on', 'tuning'] as const) {
      const model = {
        ...withAux,
        txAux: { ...withAux.txAux, atu: { reading: { status: 'known', value }, availability: AVAIL } },
      };
      expect(validateRadioViewModel(model).txAux?.atu.reading).toEqual({ status: 'known', value });
    }
  });

  it('rejects a non-boolean field inside a txAux field Availability', () => {
    const withAux = withTxAux(base);
    const malformed = {
      ...withAux,
      txAux: {
        ...withAux.txAux,
        monitor: { reading: { status: 'unknown' }, availability: { structural: 'yes', operational: true } },
      },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(TypeError);
  });

  it('rejects a reading with neither known nor unknown status', () => {
    const withAux = withTxAux(base);
    const malformed = {
      ...withAux,
      txAux: { ...withAux.txAux, compressor: { reading: { status: 'stale' }, availability: AVAIL } },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(TypeError);
  });

  it('accepts an unknown reading distinct from a known one, independently per field', () => {
    const withAux = withTxAux(base);
    const partial = {
      ...withAux,
      txAux: {
        ...withAux.txAux,
        driveGain: { reading: { status: 'unknown' }, availability: { structural: true, operational: false } },
      },
    };
    const validated = validateRadioViewModel(partial);
    expect(validated.txAux?.driveGain).toEqual({
      reading: { status: 'unknown' }, availability: { structural: true, operational: false },
    });
    // The rest of the group is untouched by the one field's degrade.
    expect(validated.txAux?.rfPower.reading).toEqual({ status: 'known', value: 0.8 });
  });

  // ── Kill-test 5: no speculative keys (N4) ─────────────────────────────────
  it('rejects an unknown extra key inside txAux', () => {
    const withAux = withTxAux(base);
    const malformed = { ...withAux, txAux: { ...withAux.txAux, bogus: 1 } };
    expect(() => validateRadioViewModel(malformed)).toThrow(TypeError);
  });

  it('rejects an unknown extra key inside a single txAux field', () => {
    const withAux = withTxAux(base);
    const malformed = {
      ...withAux,
      txAux: {
        ...withAux.txAux,
        micGain: { reading: { status: 'known', value: 128 }, availability: AVAIL, extra: true },
      },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(TypeError);
  });

  it('rejects a reading that carries a value key while unknown', () => {
    const withAux = withTxAux(base);
    const malformed = {
      ...withAux,
      txAux: { ...withAux.txAux, monitorLevel: { reading: { status: 'unknown', value: 1 }, availability: AVAIL } },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(TypeError);
  });

  it('every field of a fully-populated group round-trips its own value', () => {
    const withAux = withTxAux(base);
    const validated = validateRadioViewModel(withAux).txAux as TxAuxViewModel;
    const knownValue = <T>(f: TxAuxField<T>): T | undefined =>
      f.reading.status === 'known' ? f.reading.value : undefined;
    expect(knownValue(validated.atu)).toBe('off');
    expect(knownValue(validated.vox)).toBe(false);
    expect(knownValue(validated.voxGain)).toBe(50);
    expect(knownValue(validated.antiVoxGain)).toBe(30);
    expect(knownValue(validated.voxDelay)).toBe(20);
    expect(knownValue(validated.compressor)).toBe(false);
    expect(knownValue(validated.compressorLevel)).toBe(10);
    expect(knownValue(validated.monitor)).toBe(false);
    expect(knownValue(validated.monitorLevel)).toBe(128);
    expect(knownValue(validated.rfPower)).toBe(0.8);
    expect(knownValue(validated.micGain)).toBe(128);
    expect(knownValue(validated.driveGain)).toBe(128);
  });
});
