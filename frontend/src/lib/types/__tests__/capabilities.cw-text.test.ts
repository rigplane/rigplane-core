// MOR-3678: `cwText` says whether send_cw_text works on this radio. The
// server owns the facts (profile `[cw_text]`); clients only display them.
import { describe, expect, it } from 'vitest';
import type { Capabilities, CwTextCapability } from '../capabilities';
import { validateCapabilities } from '../capabilities';
import ftx1CapsJson from '$lib/runtime/adapters/__tests__/fixtures/ftx1-capabilities.json';
import ic7300CapsJson from '$lib/runtime/adapters/__tests__/fixtures/ic7300-capabilities.json';
import goldens from '$lib/transport/__tests__/fixtures/state-wire-goldens.json';

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

const optionalOnOlderServers: Pick<Capabilities, 'cwText'> = {};
void optionalOnOlderServers;

describe('capabilities cwText', () => {
  it.each<CwTextCapability>([
    { supported: false, maxLength: 50, memoryChannel: 5 },
    { supported: true, maxLength: 512, memoryChannel: null },
  ])('accepts %o', (cwText) => {
    expect(validateCapabilities({ ...baseCapabilities, cwText }).cwText).toEqual(cwText);
  });

  it('accepts a payload without cwText (older servers)', () => {
    expect(validateCapabilities(baseCapabilities).cwText).toBeUndefined();
  });

  it.each([
    [{ supported: 'no', maxLength: 50, memoryChannel: 5 }, /\$\.cwText\.supported: expected a boolean/],
    [{ supported: true, maxLength: 0, memoryChannel: null }, /\$\.cwText\.maxLength/],
    [{ supported: true, maxLength: 50, memoryChannel: '5' }, /\$\.cwText\.memoryChannel/],
    [{ supported: true, maxLength: 50 }, /\$\.cwText\.memoryChannel/],
    [null, /\$\.cwText: expected an object/],
  ])('rejects malformed cwText %o', (cwText, message) => {
    expect(() => validateCapabilities({ ...baseCapabilities, cwText })).toThrow(message);
  });

  it('fixtures carry the served cwText', () => {
    expect(ftx1CapsJson.cwText).toEqual({ supported: false, maxLength: 50, memoryChannel: 5 });
    expect(ic7300CapsJson.cwText).toEqual({ supported: true, maxLength: 512, memoryChannel: null });
  });

  it('state-wire goldens carry the served cwText', () => {
    const byModel = goldens as unknown as Record<string, { capabilities: { cwText?: unknown } }>;
    expect(byModel['FTX-1'].capabilities.cwText).toEqual({
      supported: false,
      maxLength: 50,
      memoryChannel: 5,
    });
    expect(byModel['TX-500'].capabilities.cwText).toEqual({
      supported: false,
      maxLength: 512,
      memoryChannel: null,
    });
  });
});
