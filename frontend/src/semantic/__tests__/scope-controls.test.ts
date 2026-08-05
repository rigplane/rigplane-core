/**
 * MOR-1262 decomposition slice 11A (MOR-1298) — `scopeControls` optional
 * fact group (validator half).
 *
 * Facts only: span preset index, sweep speed, dual-scope on/off, scope
 * receiver (MAIN/SUB). See `radio-view-model.ts`'s `ScopeControlsViewModel`
 * doc comment for the group-shape rationale (why MODE/EDGE/HOLD/REF and
 * frame data are deliberately absent), and `radio-view-model-adapter.ts`'s
 * `deriveScopeControls` for the live derivation (covered by the companion
 * `scope-controls-adapter.test.ts`).
 *
 * Mirrors the companion families' (`scan.test.ts`, `cw-keyer.test.ts`)
 * kill-tests: absent-group round-trip, exactKeys rejection of `null`/`{}`,
 * and one precise-error-path rejection per field type.
 */
import { describe, expect, it } from 'vitest';
import { validateRadioViewModel, type ScopeControlsField } from '../radio-view-model';
import { topologyFixtures, withScopeControls } from '../fixtures/topologies';

const AVAIL = { structural: true, operational: true } as const;
const base = topologyFixtures['1/single'];

describe('scopeControls (MOR-1262 slice 11A)', () => {
  it('validates a scopeControls-absent model and never adds the key', () => {
    expect(Object.keys(base)).not.toContain('scopeControls');
    const validated = validateRadioViewModel(base);
    expect(Object.keys(validated)).not.toContain('scopeControls');
    expect(validated.scopeControls).toBeUndefined();
    expect(JSON.parse(JSON.stringify(validated))).toEqual(JSON.parse(JSON.stringify(base)));
  });

  it('validates a fully-populated scopeControls group and returns it unchanged', () => {
    const withSc = withScopeControls(base);
    expect(validateRadioViewModel(withSc).scopeControls).toEqual(withSc.scopeControls);
  });

  it('rejects an explicit scopeControls: null', () => {
    expect(() => validateRadioViewModel({ ...base, scopeControls: null })).toThrow(TypeError);
  });

  it('rejects an explicit scopeControls: {} — present, not absent, must satisfy its own shape', () => {
    expect(() => validateRadioViewModel({ ...base, scopeControls: {} })).toThrow(TypeError);
  });

  it('rejects an extra key on the group (closed shape, exactly the four facts)', () => {
    const withSc = withScopeControls(base);
    const extra: ScopeControlsField<number> = { reading: { status: 'known', value: 0 }, availability: AVAIL };
    const malformed = { ...withSc, scopeControls: { ...withSc.scopeControls, mode: extra } };
    expect(() => validateRadioViewModel(malformed)).toThrow(TypeError);
  });

  it('rejects a non-numeric span reading value with a precise error path', () => {
    const withSc = withScopeControls(base);
    const malformed = {
      ...withSc,
      scopeControls: { ...withSc.scopeControls, span: { reading: { status: 'known', value: '3' }, availability: AVAIL } },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(/\$\.scopeControls\.span\.reading\.value/);
  });

  it('rejects a non-numeric speed reading value with a precise error path', () => {
    const withSc = withScopeControls(base);
    const malformed = {
      ...withSc,
      scopeControls: { ...withSc.scopeControls, speed: { reading: { status: 'known', value: true }, availability: AVAIL } },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(/\$\.scopeControls\.speed\.reading\.value/);
  });

  it('rejects a non-boolean dual reading value with a precise error path', () => {
    const withSc = withScopeControls(base);
    const malformed = {
      ...withSc,
      scopeControls: { ...withSc.scopeControls, dual: { reading: { status: 'known', value: 1 }, availability: AVAIL } },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(/\$\.scopeControls\.dual\.reading\.value/);
  });

  it('rejects a non-numeric receiver reading value with a precise error path', () => {
    const withSc = withScopeControls(base);
    const malformed = {
      ...withSc,
      scopeControls: {
        ...withSc.scopeControls, receiver: { reading: { status: 'known', value: 'SUB' }, availability: AVAIL },
      },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(/\$\.scopeControls\.receiver\.reading\.value/);
  });

  it('accepts a structurally-absent field (present group, one control this radio lacks)', () => {
    const withSc = withScopeControls(base);
    const off = { structural: false, operational: false } as const;
    const model = {
      ...withSc,
      scopeControls: { ...withSc.scopeControls, dual: { reading: { status: 'unknown' }, availability: off } },
    };
    expect(validateRadioViewModel(model).scopeControls!.dual.availability.structural).toBe(false);
  });
});
