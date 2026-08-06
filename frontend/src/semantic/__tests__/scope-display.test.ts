/**
 * MOR-1262 decomposition slice 12A (MOR-1301, the FINAL A-slice of the
 * vocabulary program) — `scopeDisplay` optional fact group (validator half).
 * Extended by slice 12B (MOR-1312, the LAST vocabulary slice) with the
 * `hardwareConnected` leaf (MOR-1352 finding).
 *
 * Facts: which scope SOURCE is currently live (hardware CI-V frames vs the
 * browser audio FFT), how HEALTHY that source's connection is, and whether
 * the HARDWARE channel is connected independent of that selection — never
 * scope tuning (`scopeControls`, slice 11A/11A′, unchanged by this file) and
 * never scope pixels/overlays. See `radio-view-model.ts`'s
 * `ScopeDisplayViewModel` doc comment for the full rationale, and
 * `radio-view-model-adapter.ts`'s `deriveScopeDisplay` for the live
 * derivation (covered by the companion `scope-display-adapter.test.ts`).
 *
 * Mirrors the companion families' (`scope-controls.test.ts`, `scan.test.ts`)
 * kill-tests: absent-group round-trip, exactKeys rejection of `null`/`{}`,
 * an extra-key rejection, and one precise-error-path rejection per leaf.
 */
import { describe, expect, it } from 'vitest';
import { validateRadioViewModel, type ScopeDisplayField } from '../radio-view-model';
import { topologyFixtures, withScopeDisplay } from '../fixtures/topologies';

const AVAIL = { structural: true, operational: true } as const;
const base = topologyFixtures['1/single'];

describe('scopeDisplay (MOR-1262 slice 12A)', () => {
  it('validates a scopeDisplay-absent model and never adds the key', () => {
    expect(Object.keys(base)).not.toContain('scopeDisplay');
    const validated = validateRadioViewModel(base);
    expect(Object.keys(validated)).not.toContain('scopeDisplay');
    expect(validated.scopeDisplay).toBeUndefined();
    expect(JSON.parse(JSON.stringify(validated))).toEqual(JSON.parse(JSON.stringify(base)));
  });

  it('validates a fully-populated scopeDisplay group and returns it unchanged', () => {
    const withSd = withScopeDisplay(base);
    expect(validateRadioViewModel(withSd).scopeDisplay).toEqual(withSd.scopeDisplay);
  });

  it('rejects an explicit scopeDisplay: null', () => {
    expect(() => validateRadioViewModel({ ...base, scopeDisplay: null })).toThrow(TypeError);
  });

  it('rejects an explicit scopeDisplay: {} — present, not absent, must satisfy its own shape', () => {
    expect(() => validateRadioViewModel({ ...base, scopeDisplay: {} })).toThrow(TypeError);
  });

  it('rejects an extra key on the group (closed shape, exactly the three facts)', () => {
    const withSd = withScopeDisplay(base);
    const extra: ScopeDisplayField<number> = { reading: { status: 'known', value: 0 }, availability: AVAIL };
    const malformed = { ...withSd, scopeDisplay: { ...withSd.scopeDisplay, bogus: extra } };
    expect(() => validateRadioViewModel(malformed)).toThrow(TypeError);
  });

  it('rejects an invalid source reading value with a precise error path', () => {
    const withSd = withScopeDisplay(base);
    const malformed = {
      ...withSd,
      scopeDisplay: {
        ...withSd.scopeDisplay, source: { reading: { status: 'known', value: 'usb' }, availability: AVAIL },
      },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(/\$\.scopeDisplay\.source\.reading\.value/);
  });

  it('rejects an invalid health reading value with a precise error path', () => {
    const withSd = withScopeDisplay(base);
    const malformed = {
      ...withSd,
      scopeDisplay: {
        ...withSd.scopeDisplay, health: { reading: { status: 'known', value: 'green' }, availability: AVAIL },
      },
    };
    expect(() => validateRadioViewModel(malformed)).toThrow(/\$\.scopeDisplay\.health\.reading\.value/);
  });

  it('accepts a structurally-absent field (present group, source not yet resolved)', () => {
    const withSd = withScopeDisplay(base);
    const off = { structural: false, operational: false } as const;
    const model = {
      ...withSd,
      scopeDisplay: { ...withSd.scopeDisplay, source: { reading: { status: 'unknown' }, availability: off } },
    };
    expect(validateRadioViewModel(model).scopeDisplay!.source.availability.structural).toBe(false);
  });

  it('accepts every declared ScopeHealthState value', () => {
    const states = [
      'inactive', 'starting', 'connecting', 'waiting',
      'reconnecting', 'failed', 'disconnected', 'connected',
    ] as const;
    for (const health of states) {
      const withSd = withScopeDisplay(base);
      const model = {
        ...withSd,
        scopeDisplay: { ...withSd.scopeDisplay, health: { reading: { status: 'known', value: health }, availability: AVAIL } },
      };
      expect(validateRadioViewModel(model).scopeDisplay!.health.reading).toEqual({ status: 'known', value: health });
    }
  });

  // MOR-1312 (12B) — `hardwareConnected` leaf.
  it('rejects an invalid hardwareConnected reading value with a precise error path', () => {
    const withSd = withScopeDisplay(base);
    const malformed = {
      ...withSd,
      scopeDisplay: {
        ...withSd.scopeDisplay,
        hardwareConnected: { reading: { status: 'known', value: 'yes' }, availability: AVAIL },
      },
    };
    expect(() => validateRadioViewModel(malformed))
      .toThrow(/\$\.scopeDisplay\.hardwareConnected\.reading\.value/);
  });

  it('accepts both boolean values for hardwareConnected', () => {
    for (const value of [true, false]) {
      const withSd = withScopeDisplay(base);
      const model = {
        ...withSd,
        scopeDisplay: {
          ...withSd.scopeDisplay,
          hardwareConnected: { reading: { status: 'known', value }, availability: AVAIL },
        },
      };
      expect(validateRadioViewModel(model).scopeDisplay!.hardwareConnected.reading)
        .toEqual({ status: 'known', value });
    }
  });
});
