import { describe, expect, it } from 'vitest';
import { validateCapabilities } from '../capabilities';
import ftx1CapabilitiesJson from '$lib/runtime/adapters/__tests__/fixtures/ftx1-capabilities.json';

// MOR-3679: the profile names its CW modes once (`[modes] families`) and
// the server publishes them as `modeFamilies`; clients must not guess.
const base = {
  model: 'Test Radio',
  scope: false,
  audio: false,
  tx: false,
  capabilities: [],
  receivers: 1,
  vfoScheme: 'ab' as const,
  freqRanges: [],
  modes: ['USB', 'CW', 'CW-R'],
  filters: [],
  audioConfig: { sampleRate: 48_000, channels: 1, codecs: ['pcm'] },
  webrtc: { available: false, enabled: false },
  txBands: null,
  modeFamilies: { cw: ['CW', 'CW-R'] },
};

describe('modeFamilies capability field (MOR-3679)', () => {
  it('accepts a family -> mode names object', () => {
    expect(validateCapabilities(base).modeFamilies).toEqual({ cw: ['CW', 'CW-R'] });
  });

  it('accepts an empty object (profile declares no families)', () => {
    const payload = { ...base, modeFamilies: {} };
    expect(validateCapabilities(payload).modeFamilies).toEqual({});
  });

  it('stays optional for older servers', () => {
    const payload = { ...base } as Record<string, unknown>;
    delete payload.modeFamilies;
    expect(validateCapabilities(payload).modeFamilies).toBeUndefined();
  });

  it('rejects a non-object modeFamilies', () => {
    expect(() => validateCapabilities({ ...base, modeFamilies: ['CW'] })).toThrow(
      /\$\.modeFamilies: expected an object/,
    );
  });

  it('rejects a family that is not a string array', () => {
    expect(() => validateCapabilities({ ...base, modeFamilies: { cw: 'CW' } })).toThrow(
      /\$\.modeFamilies\.cw: expected an array/,
    );
    expect(() => validateCapabilities({ ...base, modeFamilies: { cw: [3] } })).toThrow(
      /\$\.modeFamilies\.cw\[0\]: expected a string/,
    );
  });

  it('FTX-1 fixture carries the profile CW family CW-U/CW-L', () => {
    const caps = validateCapabilities(structuredClone(ftx1CapabilitiesJson));
    expect(caps.modeFamilies).toEqual({ cw: ['CW-U', 'CW-L'] });
    for (const mode of ['USB', 'LSB', 'FM']) {
      expect(caps.modeFamilies?.cw).not.toContain(mode);
    }
  });
});
