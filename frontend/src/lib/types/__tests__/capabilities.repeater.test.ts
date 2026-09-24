import { describe, expect, it } from 'vitest';
import { validateCapabilities } from '../capabilities';

const base = {
  model: 'Test Radio',
  scope: false,
  audio: false,
  tx: false,
  capabilities: [],
  receivers: 1,
  vfoScheme: 'ab' as const,
  freqRanges: [{ start: 144_000_000, end: 148_000_000, label: '2m', repeater: true }],
  modes: [],
  filters: [],
  audioConfig: { sampleRate: 48_000, channels: 1, codecs: ['pcm'] },
  webrtc: { available: false, enabled: false },
  txBands: null,
  ctcssTones: [6700, 8850, 25410],
};

describe('repeater-band and CTCSS capability facts (MOR-2111)', () => {
  it('accepts a repeater-flagged range and a ctcssTones array', () => {
    expect(validateCapabilities(base)).toBe(base);
  });

  it('keeps both facts optional for older servers', () => {
    const payload = {
      ...base,
      freqRanges: [{ start: 1_800_000, end: 2_000_000, label: '160m' }],
    } as Record<string, unknown>;
    delete payload.ctcssTones;
    expect(validateCapabilities(payload)).toBe(payload);
  });

  it('rejects a non-boolean repeater flag', () => {
    const payload = {
      ...base,
      freqRanges: [{ ...base.freqRanges[0], repeater: 1 }],
    };
    expect(() => validateCapabilities(payload)).toThrow(
      /freqRanges\[0\]\.repeater: expected a boolean/,
    );
  });

  it('rejects a non-array ctcssTones', () => {
    expect(() => validateCapabilities({ ...base, ctcssTones: 'nope' })).toThrow(
      /ctcssTones: expected a non-empty array/,
    );
  });

  it('rejects an empty ctcssTones array', () => {
    expect(() => validateCapabilities({ ...base, ctcssTones: [] })).toThrow(
      /ctcssTones: expected a non-empty array/,
    );
  });

  it('rejects a non-integer ctcssTones entry', () => {
    expect(() => validateCapabilities({ ...base, ctcssTones: [6700, 88.5] })).toThrow(
      /ctcssTones\[1\]: expected an integer/,
    );
  });
});
