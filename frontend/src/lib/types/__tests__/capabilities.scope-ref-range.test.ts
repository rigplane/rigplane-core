import { describe, expect, it } from 'vitest';
import { validateCapabilities } from '../capabilities';
import ic7300CapabilitiesJson from '$lib/runtime/adapters/__tests__/fixtures/ic7300-capabilities.json';

// MOR-3694: the server publishes the profile's `[scope]` REF range with
// the profile fields' own names (`ref_min_db`/`ref_max_db`/`ref_step_db`).
// The REF steppers fail closed when the group is absent, so the parser
// accepts an absent group and rejects a malformed one loudly.
const base = {
  model: 'Test Radio',
  scope: true,
  audio: false,
  tx: false,
  capabilities: ['scope'],
  receivers: 1,
  vfoScheme: 'single' as const,
  freqRanges: [],
  modes: ['USB', 'CW', 'CW-R'],
  filters: [],
  audioConfig: { sampleRate: 48_000, channels: 1, codecs: ['pcm'] },
  webrtc: { available: false, enabled: false },
  txBands: null,
};

describe('scope REF range capability fields (MOR-3694)', () => {
  it('accepts a complete declared group', () => {
    const caps = validateCapabilities({
      ...base,
      ref_min_db: -20,
      ref_max_db: 20,
      ref_step_db: 0.5,
    });
    expect(caps.ref_min_db).toBe(-20);
    expect(caps.ref_max_db).toBe(20);
    expect(caps.ref_step_db).toBe(0.5);
  });

  it('stays optional for older servers and non-declaring profiles', () => {
    const caps = validateCapabilities({ ...base });
    expect(caps.ref_min_db).toBeUndefined();
    expect(caps.ref_max_db).toBeUndefined();
    expect(caps.ref_step_db).toBeUndefined();
  });

  it('rejects a partial group', () => {
    expect(() => validateCapabilities({ ...base, ref_min_db: -20 })).toThrow(
      /\$: expected a complete or absent scope REF range group/,
    );
    expect(() => validateCapabilities({ ...base, ref_min_db: -20, ref_max_db: 20 })).toThrow(
      /\$: expected a complete or absent scope REF range group/,
    );
  });

  it('rejects non-finite values, min >= max, and a non-positive step', () => {
    expect(() => validateCapabilities({
      ...base, ref_min_db: 'x', ref_max_db: 20, ref_step_db: 0.5,
    } as unknown as Record<string, unknown>)).toThrow(/\$\.ref_min_db: expected a finite number/);
    expect(() => validateCapabilities({
      ...base, ref_min_db: 20, ref_max_db: 20, ref_step_db: 0.5,
    })).toThrow(/\$\.ref_min_db: expected min < max/);
    expect(() => validateCapabilities({
      ...base, ref_min_db: -20, ref_max_db: 20, ref_step_db: 0,
    })).toThrow(/\$\.ref_step_db: expected > 0/);
  });

  it('the IC-7300 fixture carries the profile-declared range', () => {
    const caps = validateCapabilities(structuredClone(ic7300CapabilitiesJson));
    expect(caps.ref_min_db).toBe(-20);
    expect(caps.ref_max_db).toBe(20);
    expect(caps.ref_step_db).toBe(0.5);
  });
});
