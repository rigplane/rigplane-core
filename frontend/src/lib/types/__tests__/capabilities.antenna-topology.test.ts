import { describe, expect, it } from 'vitest';
import type { Capabilities } from '../capabilities';
import { validateCapabilities } from '../capabilities';

const baseCapabilities = {
  model: 'Test Radio',
  scope: false,
  audio: false,
  tx: false,
  capabilities: [],
  receivers: 1,
  vfoScheme: 'ab' as const,
  freqRanges: [],
  modes: [],
  filters: [],
  audioConfig: { sampleRate: 48_000, channels: 1, codecs: ['pcm'] },
  webrtc: { available: false, enabled: false },
  txBands: null,
};

const optionalContract: Pick<Capabilities, 'hasRxAntenna'> = {};
void optionalContract;

describe('RX antenna topology capability', () => {
  it.each([true, false])('accepts explicit boolean value %s', (hasRxAntenna) => {
    const payload = { ...baseCapabilities, hasRxAntenna };
    expect(validateCapabilities(payload)).toBe(payload);
  });

  it('keeps the additive field optional for older servers', () => {
    expect(validateCapabilities(baseCapabilities)).toBe(baseCapabilities);
  });

  it('rejects a non-boolean value when the field is present', () => {
    expect(() => validateCapabilities({ ...baseCapabilities, hasRxAntenna: 1 })).toThrow(
      /Invalid capabilities payload at \$\.hasRxAntenna: expected a boolean/,
    );
  });
});
