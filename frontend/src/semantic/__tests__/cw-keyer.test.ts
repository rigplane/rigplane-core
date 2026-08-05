/**
 * MOR-1262 decomposition slice 9A (MOR-1296) — `cwKeyer` optional fact group
 * (validator half). SAFETY-CRITICAL: break-in keys the transmitter.
 *
 * Facts only: break-in mode/delay, keyer speed, pitch, reverse paddle, APF,
 * TPF. See `radio-view-model.ts`'s `CwKeyerViewModel` doc comment for the
 * group-shape rationale (and for the three shipped `CwProps` fields
 * deliberately absent from it), and `radio-view-model-adapter.ts`'s
 * `deriveCwKeyer`/`deriveCwKeyerReasons` for the live derivation (covered by
 * the companion `cw-keyer-adapter.test.ts` and `cw-keyer-purity.test.ts`).
 *
 * Mirrors the companion families' (`rit-xit.test.ts`, `scan.test.ts`)
 * kill-tests, plus one this family alone carries: the fail-closed cross-field
 * invariant tying a structurally-available `breakIn` to a `txPermit` that is
 * not `'allowed'` (safety constraint 2/3, same shape as MOR-1294's band pin).
 */
import { describe, expect, it } from 'vitest';
import {
  validateRadioViewModel, type CwKeyerField, type CwKeyerViewModel,
} from '../radio-view-model';
import { topologyFixtures, withCwKeyer } from '../fixtures/topologies';

const AVAIL = { structural: true, operational: true } as const;
const OFF = { structural: false, operational: false } as const;
const base = topologyFixtures['1/single'];

describe('cwKeyer (MOR-1262 slice 9A)', () => {
  it('validates a cwKeyer-absent model and never adds the key', () => {
    expect(Object.keys(base)).not.toContain('cwKeyer');
    const validated = validateRadioViewModel(base);
    expect(Object.keys(validated)).not.toContain('cwKeyer');
    expect(validated.cwKeyer).toBeUndefined();
    expect(JSON.parse(JSON.stringify(validated))).toEqual(JSON.parse(JSON.stringify(base)));
  });

  it('validates a fully-populated cwKeyer group and returns it unchanged', () => {
    const withC = withCwKeyer(base);
    expect(validateRadioViewModel(withC).cwKeyer).toEqual(withC.cwKeyer);
  });

  it('rejects an explicit cwKeyer: null', () => {
    expect(() => validateRadioViewModel({ ...base, cwKeyer: null })).toThrow(TypeError);
  });

  it('rejects an explicit cwKeyer: {} — present, not absent, must satisfy its own shape', () => {
    expect(() => validateRadioViewModel({ ...base, cwKeyer: {} })).toThrow(TypeError);
  });

  it('rejects a non-numeric keyerSpeed reading value with a precise error path', () => {
    const withC = withCwKeyer(base);
    const malformed = {
      ...withC,
      cwKeyer: { ...withC.cwKeyer, keyerSpeed: { reading: { status: 'known', value: '24' }, availability: AVAIL } },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(/\$\.cwKeyer\.keyerSpeed\.reading\.value/);
  });

  it('rejects a non-boolean twinPeak reading value with a precise error path', () => {
    const withC = withCwKeyer(base);
    const malformed = {
      ...withC,
      cwKeyer: { ...withC.cwKeyer, twinPeak: { reading: { status: 'known', value: 1 }, availability: AVAIL } },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(/\$\.cwKeyer\.twinPeak\.reading\.value/);
  });

  it('rejects the RAW v2 int encoding for breakIn — the contract carries the tri-state, not 0/1/2', () => {
    const withC = withCwKeyer(base);
    const malformed = {
      ...withC,
      cwKeyer: { ...withC.cwKeyer, breakIn: { reading: { status: 'known', value: 1 }, availability: AVAIL } },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(/\$\.cwKeyer\.breakIn\.reading\.value/);
  });

  it('rejects a breakIn mode outside the closed union (no silent extra keyer mode)', () => {
    const withC = withCwKeyer(base);
    const malformed = {
      ...withC,
      cwKeyer: { ...withC.cwKeyer, breakIn: { reading: { status: 'known', value: 'qsk' }, availability: AVAIL } },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(/\$\.cwKeyer\.breakIn\.reading\.value/);
  });

  it.each(['off', 'semi', 'full'] as const)('accepts the %s break-in mode', (mode) => {
    const withC = withCwKeyer(base);
    const model = {
      ...withC,
      cwKeyer: { ...withC.cwKeyer, breakIn: { reading: { status: 'known', value: mode }, availability: AVAIL } },
    };
    expect(validateRadioViewModel(model).cwKeyer?.breakIn.reading).toEqual({ status: 'known', value: mode });
  });

  it('accepts an unknown reading distinct from a known one, independently per field', () => {
    const withC = withCwKeyer(base);
    const partial = {
      ...withC,
      cwKeyer: { ...withC.cwKeyer, apf: { reading: { status: 'unknown' }, availability: OFF } },
    };
    const validated = validateRadioViewModel(partial);
    expect(validated.cwKeyer?.apf).toEqual({ reading: { status: 'unknown' }, availability: OFF });
    expect(validated.cwKeyer?.pitchHz.reading).toEqual({ status: 'known', value: 600 });
  });

  it('rejects an unknown extra key inside cwKeyer', () => {
    const withC = withCwKeyer(base);
    expect(() => validateRadioViewModel({ ...base, cwKeyer: { ...withC.cwKeyer, bogus: 1 } })).toThrow(TypeError);
  });

  it('rejects an unknown extra key inside a single cwKeyer field', () => {
    const withC = withCwKeyer(base);
    const malformed = {
      ...withC,
      cwKeyer: {
        ...withC.cwKeyer,
        reversePaddle: { reading: { status: 'known', value: false }, availability: AVAIL, extra: true },
      },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(TypeError);
  });

  it('rejects a reading that carries a value key while unknown', () => {
    const withC = withCwKeyer(base);
    const malformed = {
      ...withC,
      cwKeyer: { ...withC.cwKeyer, breakIn: { reading: { status: 'unknown', value: 'full' }, availability: AVAIL } },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(TypeError);
  });

  it('rejects a cwKeyer group missing a key (no partial 9A shape survives)', () => {
    const withC = withCwKeyer(base);
    const { apf: _apf, ...withoutApf } = withC.cwKeyer as CwKeyerViewModel;
    expect(() => validateRadioViewModel({ ...withC, cwKeyer: withoutApf })).toThrow(TypeError);
  });

  it('every field of a fully-populated group round-trips its own value', () => {
    const validated = validateRadioViewModel(withCwKeyer(base)).cwKeyer as CwKeyerViewModel;
    const knownValue = <T>(f: CwKeyerField<T>): T | undefined =>
      f.reading.status === 'known' ? f.reading.value : undefined;
    expect(knownValue(validated.breakIn)).toBe('semi');
    expect(knownValue(validated.breakInDelay)).toBe(64);
    expect(knownValue(validated.keyerSpeed)).toBe(24);
    expect(knownValue(validated.pitchHz)).toBe(600);
    expect(knownValue(validated.reversePaddle)).toBe(false);
    expect(knownValue(validated.apf)).toBe(0);
    expect(knownValue(validated.twinPeak)).toBe(false);
  });
});

/**
 * SAFETY CONSTRAINTS 2 + 3, enforced structurally by the contract itself: a
 * producer cannot ship a usable break-in fact while dropping the record that
 * TX is not permitted. This is the CW analogue of MOR-1294's band invariant,
 * and it is what makes "no second permit" checkable — the only permit in play
 * is the model's own `txPermit`.
 */
describe('cwKeyer break-in / txPermit fail-closed invariant (MOR-1296)', () => {
  const armed = withCwKeyer(base);

  it.each([
    ['denied', { status: 'denied', reason: 'outside-configured-ranges' }],
    ['unknown (ranges unconfigured)', { status: 'unknown', reason: 'ranges-unconfigured' }],
    ['unknown (tx target unknown)', { status: 'unknown', reason: 'tx-target-unknown' }],
  ] as const)(
    'rejects a structurally-available breakIn under a %s txPermit with no cwKeyer.breakIn disabled reason',
    (_label, txPermit) => {
      const model = {
        ...armed,
        txTarget: { status: 'unknown', reason: 'not-observed' },
        txPermit,
        vfos: armed.vfos.map((vfo) => ({ ...vfo, isTxTarget: false })),
        disabledReasons: [],
      };
      expect(() => validateRadioViewModel(model)).toThrow(/cwKeyer\.breakIn/);
    },
  );

  it('accepts the same model once the disabled reason is recorded', () => {
    const model = {
      ...armed,
      txTarget: { status: 'unknown', reason: 'not-observed' },
      txPermit: { status: 'unknown', reason: 'tx-target-unknown' },
      vfos: armed.vfos.map((vfo) => ({ ...vfo, isTxTarget: false })),
      disabledReasons: [{ field: 'cwKeyer.breakIn', code: 'tx-target-unknown' }],
    };
    expect(validateRadioViewModel(model).cwKeyer?.breakIn.reading).toEqual({ status: 'known', value: 'semi' });
  });

  it('needs no reason when txPermit is positively allowed', () => {
    expect(base.txPermit.status).toBe('allowed');
    expect(() => validateRadioViewModel(armed)).not.toThrow();
    expect(armed.disabledReasons).toEqual([]);
  });

  it('needs no reason when break-in is structurally absent — nothing to key with', () => {
    const model = {
      ...armed,
      txTarget: { status: 'unknown', reason: 'not-observed' },
      txPermit: { status: 'unknown', reason: 'tx-target-unknown' },
      vfos: armed.vfos.map((vfo) => ({ ...vfo, isTxTarget: false })),
      disabledReasons: [],
      cwKeyer: { ...armed.cwKeyer, breakIn: { reading: { status: 'unknown' }, availability: OFF } },
    };
    expect(() => validateRadioViewModel(model)).not.toThrow();
  });

  it('an UNKNOWN break-in reading is still gated — unobserved is not evidence the key is safe', () => {
    const model = {
      ...armed,
      txTarget: { status: 'unknown', reason: 'not-observed' },
      txPermit: { status: 'unknown', reason: 'tx-target-unknown' },
      vfos: armed.vfos.map((vfo) => ({ ...vfo, isTxTarget: false })),
      disabledReasons: [],
      cwKeyer: {
        ...armed.cwKeyer,
        breakIn: { reading: { status: 'unknown' }, availability: { structural: true, operational: false } },
      },
    };
    expect(() => validateRadioViewModel(model)).toThrow(/cwKeyer\.breakIn/);
  });
});
