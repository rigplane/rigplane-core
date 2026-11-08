import { describe, expect, it } from 'vitest';
import {
  resolveLcdSpectrumFrame,
  type LcdSpectrumFrame,
  type LcdSpectrumFrameExpectation,
} from '../lcd-display-contract';

const EXPECT_HARDWARE_MAIN: LcdSpectrumFrameExpectation = {
  source: 'hardware',
  receiver: 'MAIN',
};

function frame(overrides: Partial<LcdSpectrumFrame> = {}): LcdSpectrumFrame {
  return {
    source: 'hardware',
    receiver: 'MAIN',
    freshness: 'fresh',
    startHz: 14_200_000,
    endHz: 14_300_000,
    pixels: Uint8Array.from([0.1, 0.7, 0.3], value => Math.round(value * 255)),
    ...overrides,
  };
}

describe('LCD display-frame contract', () => {
  it.each(['hardware', 'audio-fft'] as const)(
    'admits a valid, source-qualified %s frame without changing its samples',
    (source) => {
      const candidate = frame({ source });
      const result = resolveLcdSpectrumFrame(candidate, { source, receiver: 'MAIN' });

      expect(result).toEqual({ state: 'live', frame: candidate });
      if (result.state === 'live') {
        expect(result.frame).toBe(candidate);
        expect(result.frame.pixels).toBe(candidate.pixels);
      }
    },
  );

  it.each([
    ['source-mismatch', frame({ source: 'audio-fft' }), EXPECT_HARDWARE_MAIN],
    ['receiver-mismatch', frame({ receiver: 'SUB' }), EXPECT_HARDWARE_MAIN],
    ['stale', frame({ freshness: 'stale' }), EXPECT_HARDWARE_MAIN],
    ['receiver-unknown', frame(), { source: 'hardware', receiver: null }],
  ] as const)('fails a %s closed to ghost geometry', (reason, candidate, expectation) => {
    expect(resolveLcdSpectrumFrame(candidate, expectation)).toEqual({ state: 'ghost', reason });
  });

  it.each([
    undefined,
    null,
  ])('treats absent production input as missing, never as a zero frame', (candidate) => {
    expect(resolveLcdSpectrumFrame(candidate, EXPECT_HARDWARE_MAIN))
      .toEqual({ state: 'ghost', reason: 'missing' });
  });

  it.each([
    {},
    'unknown',
    frame({ startHz: Number.NaN }),
    frame({ endHz: Number.POSITIVE_INFINITY }),
    frame({ startHz: 14_300_000, endHz: 14_300_000 }),
    frame({ startHz: 14_300_000, endHz: 14_200_000 }),
    frame({ pixels: Uint8Array.from([], value => Math.round(value * 255)) }),
    frame({ pixels: Uint8Array.from([0.5], value => Math.round(value * 255)) }),
    frame({ pixels: new Uint8Array(0) }),
    frame({ pixels: new Uint8Array(1) }),
    frame({ pixels: null }),
    frame({ pixels: 'not-a-payload' as unknown as Uint8Array }),
  ])('rejects malformed production input without fabricating renderable data', (candidate) => {
    const result = resolveLcdSpectrumFrame(candidate, EXPECT_HARDWARE_MAIN);

    expect(result).toEqual({ state: 'ghost', reason: 'invalid' });
    expect(result).not.toHaveProperty('frame');
  });

  it('never borrows the other receiver when the requested receiver has no matching frame', () => {
    const mainFrame = frame({ receiver: 'MAIN' });

    expect(resolveLcdSpectrumFrame(mainFrame, { source: 'hardware', receiver: 'SUB' }))
      .toEqual({ state: 'ghost', reason: 'receiver-mismatch' });
  });
});
